"""Páginas geradas em `vault/sistema/` (somente leitura no caderno).

O conteúdo é determinístico: não leva data de geração, para que regenerar sem
mudança não reescreva o arquivo. Tudo que o caderno mostra vem do motor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp_engine.core.auth import current_user_var, set_current_user
from mcp_engine.core.config_notas import OPERACAO_ATIVAR

if TYPE_CHECKING:
    from mcp_engine.core.config_notas_servico import Caderno

PAGINAS = {
    "estado": "Estado do sistema",
    "matriz": "Matriz de governança",
    "mapa-de-cobertura": "Mapa de cobertura",
    "alertas": "Alertas",
}
ROTULO = {
    "ativa": "ativa",
    "alterada_aguardando_aprovacao": "alterada, aguardando aprovação",
    "pendente_aprovacao": "pendente de aprovação",
    "invalida": "inválida",
    "invalida_versao_anterior_ativa": "inválida (versão anterior segue ativa)",
    "removida_do_caderno_versao_ativa_mantida": "removida do caderno (versão ativa mantida)",
    "rascunho": "rascunho",
    "conhecimento": "conhecimento",
}
ENTRE = "\n"


def _celula(valor: Any) -> str:
    return str(valor if valor not in (None, "") else "-").replace("|", "\\|").replace("\n", " ")


def _tabela(cabecalho: list[str], linhas: list[list[Any]]) -> str:
    if not linhas:
        return "_Nada a mostrar._"
    out = ["| " + " | ".join(cabecalho) + " |", "|" + "---|" * len(cabecalho)]
    out += ["| " + " | ".join(_celula(c) for c in linha) + " |" for linha in linhas]
    return ENTRE.join(out)


def pagina_estado(itens: list[dict[str, Any]]) -> str:
    config = [i for i in itens if i["estado"] != "conhecimento"]
    saber = [i for i in itens if i["estado"] == "conhecimento"]
    linhas = [
        [
            i["caminho"],
            i["tipo"],
            i["id"],
            ROTULO.get(i["estado"], i["estado"]),
            i["versao_ativa"],
            i["proposto_por"],
            i["aprovado_por"],
            i["aprovado_em"],
            i["operacao_pendente"],
        ]  # fmt: skip
        for i in config
    ]
    conhec = [[i["caminho"], i["tipo"], i["conhecimento"], i["aviso"]] for i in saber]
    return ENTRE.join(
        [
            "Gerado pelo motor. Não edite: a próxima geração restaura o conteúdo.",
            "",
            "## Configuração (só vale a versão aprovada)",
            _tabela(
                [
                    "nota",
                    "tipo",
                    "id",
                    "estado",
                    "versão",
                    "proposto por",
                    "aprovado por",
                    "aprovado em",
                    "operação pendente",
                ],
                linhas,
            ),  # fmt: skip
            "",
            "## Conhecimento (sem aprovação; mudanças auditadas por hash)",
            _tabela(["nota", "tipo", "situação", "observação"], conhec),
        ]
    )


def _skills_com_efeito(engine: Any) -> list[Any]:
    return [
        s
        for s in engine.skills
        if s.autonomia != "A0"
        or s.irreversivel
        or (s.method is not None and s.method.value != "GET")
    ]


def pagina_matriz(engine: Any) -> str:
    skills = sorted(engine.skills, key=lambda s: s.name)
    tabela_skills = _tabela(
        ["skill", "autonomia", "aprovador", "irreversível", "executor"],
        [
            [s.name, s.autonomia, s.aprovador if s.autonomia == "A3" else "-",
             "sim" if s.irreversivel else "não", s.executor.type]
            for s in skills
        ],
    )  # fmt: skip
    cenarios = engine.demo_scenarios().scenarios
    usuarios = sorted(engine.user_store.list_users(), key=lambda u: u.identity)
    if not cenarios or not usuarios:
        decisoes = "_Sem cenários de simulação ou sem usuários cadastrados._"
    else:
        linhas = []
        with engine.vault_snapshot():
            for sc in cenarios:
                if not sc.in_matrix:
                    continue
                linha: list[Any] = [sc.title]
                for u in usuarios:
                    token = set_current_user(u)
                    try:
                        linha.append(engine.avaliar_skill(sc.skill, sc.arguments)["efeito"])
                    finally:
                        current_user_var.reset(token)
                linhas.append(linha)
        decisoes = _tabela(["cenário", *[u.identity for u in usuarios]], linhas)
    return ENTRE.join(
        [
            "Gerado pelo motor. Decisões das regras ativas por pessoa e cenário.",
            "",
            "## Decisões por cenário",
            decisoes,
            "",
            "## Skills e autonomia",
            tabela_skills,
        ]
    )


def cobertura(engine: Any, itens: list[dict[str, Any]]) -> dict[str, list[str]]:
    politicas = [
        i for i in itens
        if i["tipo"] == "politica" and i["estado"] == "conhecimento"
        and i.get("conhecimento") != "bloqueado"
    ]  # fmt: skip
    nomes = {s.name for s in engine.skills}
    citadas: set[str] = set()
    sem_skill = []
    for p in politicas:
        lista = p.get("frontmatter", {}).get("skills") or []
        lista = [str(x) for x in lista] if isinstance(lista, list) else []
        citadas.update(lista)
        if not lista:
            sem_skill.append(f"{p['caminho']} (nenhuma skill vinculada)")
        for ref in lista:
            if ref not in nomes:
                sem_skill.append(f"{p['caminho']} cita skill inexistente: {ref}")
    sem_politica = sorted(s.name for s in _skills_com_efeito(engine) if s.name not in citadas)
    return {"skills_sem_politica": sem_politica, "politicas_sem_skill": sorted(sem_skill)}


def pagina_cobertura(engine: Any, itens: list[dict[str, Any]]) -> str:
    c = cobertura(engine, itens)

    def lista(xs: list[str]) -> str:
        return ENTRE.join(f"- {x}" for x in xs) if xs else "_Nenhum._"

    return ENTRE.join(
        [
            "Gerado pelo motor. Cruza skills com efeito e notas de política "
            "(campo `skills` no cabeçalho da política).",
            "",
            "## Skills com efeito sem política documentada",
            lista(c["skills_sem_politica"]),
            "",
            "## Políticas sem skill",
            lista(c["politicas_sem_skill"]),
        ]
    )


def alertas(caderno: Caderno, itens: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for i in itens:
        if i["estado"] == "invalida" or i["estado"] == "invalida_versao_anterior_ativa":
            out.append(f"{i['caminho']}: nota inválida. {'; '.join(i['erros'])}")
        elif i["estado"] == "alterada_aguardando_aprovacao":
            out.append(
                f"{i['caminho']}: alterada depois de aprovada; a versão aprovada segue valendo"
            )
        elif i["estado"] == "pendente_aprovacao":
            out.append(f"{i['caminho']}: nota válida ainda sem aprovação")
        elif i["estado"].startswith("removida"):
            out.append(f"{i['caminho']}: nota removida do caderno; a versão aprovada segue ativa")
        elif i["estado"] == "conhecimento" and i["conhecimento"] != "ok":
            out.append(f"{i['caminho']}: {i['conhecimento']}. {i['aviso']}")
    memoria = caderno.vault_root / "memoria"
    hoje = caderno.hoje().isoformat()
    if memoria.is_dir():
        from mcp_engine.core.config_notas import parse_nota

        for arq in sorted(memoria.glob("*.md")):
            try:
                cab = parse_nota(arq.read_text(encoding="utf-8")).frontmatter
            except Exception:
                continue
            if cab.get("expira_em") and str(cab["expira_em"])[:10] < hoje:
                out.append(f"memoria/{arq.name}: memória expirada, revise ou apague")
    for gatilho_id in caderno._e.gatilhos.atrasados():
        out.append(f"gatilho {gatilho_id}: execução atrasada (o agendador está rodando?)")
    aberta = [
        o for o in caderno._e.operacoes.listar("aguardando_aprovacao") if o.skill == OPERACAO_ATIVAR
    ]
    if aberta:
        out.append(f"{len(aberta)} configuração(ões) aguardando aprovação")
    legado = caderno._e._rule_provider
    try:
        tem_arquivo = bool(legado.get_rules(caderno._e.client_id).rules)
    except Exception:
        tem_arquivo = False
    if caderno.ativo and tem_arquivo and not (caderno.compilado / "rules.yaml").is_file():
        out.append(
            "config/rules.yaml tem regras que não foram migradas para notas e estão inativas"
        )
    return sorted(out)


def _alertas_de_gatilhos(caderno: Caderno) -> list[str]:
    linhas = []
    for a in caderno._e.gatilhos.alertas_recentes():
        quando = a.criado_em.strftime("%Y-%m-%d %H:%M") + " UTC"
        visto = f"visto por {a.reconhecido_por}" if a.reconhecido_por else "sem ação registrada"
        linhas.append(
            f"- [{a.nivel}] {quando} ({a.gatilho_id} para {a.destinatario}, {visto}): {a.texto}"
        )
    return linhas


def pagina_alertas(caderno: Caderno, itens: list[dict[str, Any]]) -> str:
    xs = alertas(caderno, itens)
    corpo = ENTRE.join(f"- {x}" for x in xs) if xs else "_Sem alertas._"
    de_gatilhos = _alertas_de_gatilhos(caderno)
    secao = ENTRE.join(de_gatilhos) if de_gatilhos else "_Nenhum alerta de gatilho._"
    return f"Gerado pelo motor.\n\n{corpo}\n\n## Alertas de gatilhos\n{secao}"


def atualizar_alertas(caderno: Caderno) -> str:
    estado, _ = caderno._e.escritor.escrever(
        "sistema/alertas.md",
        PAGINAS["alertas"],
        pagina_alertas(caderno, caderno.estado()),
        {"tipo": "sistema", "zona": "sistema", "gerado": True, "somente_leitura": True},
    )
    return estado


def atualizar(caderno: Caderno) -> dict[str, str]:
    engine = caderno._e
    itens = caderno.estado()
    corpos = {
        "estado": pagina_estado(itens),
        "matriz": pagina_matriz(engine),
        "mapa-de-cobertura": pagina_cobertura(engine, itens),
        "alertas": pagina_alertas(caderno, itens),
    }
    resultado = {}
    for chave, titulo in PAGINAS.items():
        estado, _ = engine.escritor.escrever(
            f"sistema/{chave}.md",
            titulo,
            corpos[chave],
            {"tipo": "sistema", "zona": "sistema", "gerado": True, "somente_leitura": True},
        )
        resultado[chave] = estado
    return resultado
