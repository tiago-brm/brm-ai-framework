"""Ciclo de vida das notas de configuração no motor (spec 030).

editar no caderno, validar, propor, aprovar por outra pessoa (operação A3),
ativar, guardar o snapshot. O que vale é o conteúdo aprovado em `state.db`; o
arquivo do caderno editado depois não muda nada.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from mcp_engine.core import config_notas_paginas as paginas
from mcp_engine.core.auth import (
    Role,
    UserProfile,
    current_user_var,
    set_current_user,
)
from mcp_engine.core.config_notas import (
    OPERACAO_ATIVAR,
    CompiladoDivergente,
    ConfigNotaErro,
    ContextoValidacao,
    Extensoes,
    SnapshotStore,
    Validacao,
    VersaoAtiva,
    diff_legivel,
    escrever_atomico,
    id_valido,
    listar_notas,
    parse_nota,
    projecoes,
    sha256,
    validar_nota,
    versao_de_snapshot,
)
from mcp_engine.core.models import AuditDecision, AuditEvent, RuleSet, SkillSet
from mcp_engine.core.operacoes import AGUARDANDO, CONFIRMADA, EXECUTADA
from mcp_engine.core.state import ConhecimentoRow
from mcp_engine.modules import resolve_skills
from mcp_engine.vault.segundo_cerebro import avaliar_conhecimento

GESTORES = (Role.ADMIN, Role.SUPER_ADMIN)
MAX_LINHAS_IMPACTO = 40
ATOR = "motor"


class Caderno:
    def __init__(
        self,
        engine: Any,
        extensoes: Extensoes,
        vault_root: Path,
        hoje: Callable[[], date] = date.today,
    ) -> None:
        self._e = engine
        self.ext = extensoes
        self.vault_root = Path(vault_root)
        self.hoje = hoje
        self.snapshots = SnapshotStore(engine._state)
        self._lock = threading.RLock()

    @property
    def ativo(self) -> bool:
        return bool(self._e._client_config.config_via_notas)

    @property
    def compilado(self) -> Path:
        return self._e._data_root / self._e.client_id / "compilado"

    def _auditar(self, action: str, decisao: AuditDecision, ator: str = ATOR, **det: Any) -> None:
        self._e._audit_sink.record(
            AuditEvent(
                client_id=self._e.client_id,
                occurred_at=datetime.now(UTC),
                actor=ator,
                action=action,
                decision=decisao,
                details=det,
            )
        )

    def contexto(self, ativos: bool = True) -> ContextoValidacao:
        mapa = {}
        if ativos:
            mapa = {(r.tipo, r.config_id): r.caminho for r in self.snapshots.ultimas()}
        return ContextoValidacao(
            client_id=self._e.client_id,
            allowlist=tuple(self._e._client_config.domain_allowlist),
            modulos=self._e._modules,
            handlers=self._e.handlers,
            reservados=frozenset(self._e.reserved_tool_names()),
            ativos=mapa,
            skills={s.name: s for s in self._e.skills},
            provedores=self._e.extensoes.provedores,
        )

    def verificar_snapshots(self) -> list[VersaoAtiva]:
        ctx = self.contexto(ativos=False)
        ativas = []
        for row in self.snapshots.ultimas():
            if sha256(row.conteudo) != row.hash:
                raise CompiladoDivergente(f"snapshot adulterado: {row.caminho} v{row.versao}")
            op = self._e.operacoes.get(row.operacao_id)
            ok = (
                op is not None
                and op.skill == OPERACAO_ATIVAR
                and op.status in (CONFIRMADA, EXECUTADA)
                and op.aprovado_por == row.aprovado_por
                and op.proposto_por == row.proposto_por
                and row.aprovado_por not in ("", row.proposto_por)
                and op.argumentos.get("hash") == row.hash
            )
            if not ok:
                raise CompiladoDivergente(
                    f"snapshot sem aprovação válida: {row.caminho} v{row.versao}"
                )
            ativas.append(versao_de_snapshot(row, self.ext, ctx))
        return ativas

    def _esperado(self, ativas: list[VersaoAtiva]) -> dict[str, str]:
        return projecoes(self._e.client_id, self.ext, ativas)

    def _arquivos_atuais(self) -> dict[str, str]:
        if not self.compilado.is_dir():
            return {}
        return {
            p.name: p.read_text(encoding="utf-8")
            for p in self.compilado.iterdir()
            if p.is_file() and not p.name.endswith(".tmp")
        }

    def divergencias(self, ativas: list[VersaoAtiva] | None = None) -> list[str]:
        ativas = self.verificar_snapshots() if ativas is None else ativas
        esperado = self._esperado(ativas)
        atual = self._arquivos_atuais()
        problemas = []
        for nome in sorted(set(esperado) | set(atual)):
            if sha256(esperado.get(nome, "")) != sha256(atual.get(nome, "")):
                problemas.append(nome)
        return problemas

    def verificar_integridade(self) -> None:
        try:
            ativas = self.verificar_snapshots()
            problemas = self.divergencias(ativas)
        except CompiladoDivergente as exc:
            self._auditar("config_integridade", AuditDecision.DENIED, motivo=str(exc))
            raise
        if not problemas:
            return
        orfa = any(o.skill == OPERACAO_ATIVAR for o in self._e.operacoes.orfas())
        if orfa:
            self.reconstruir()
            return
        self._auditar("config_compilado_divergente", AuditDecision.DENIED, arquivos=problemas)
        raise CompiladoDivergente(
            "compilado/ diverge das versões aprovadas: "
            f"{', '.join(problemas)}. Rode a reconstrução (python -m "
            "mcp_engine.core.config_notas_servico reconstruir <cliente>)."
        )

    def reconstruir(self) -> dict[str, str]:
        with self._lock:
            ativas = self.verificar_snapshots()
            esperado = self._esperado(ativas)
            for nome, texto in esperado.items():
                escrever_atomico(self.compilado / nome, texto)
            for nome in set(self._arquivos_atuais()) - set(esperado):
                shutil.move(
                    str(self.compilado / nome),
                    str(Path(tempfile.gettempdir()) / f"brm-compilado-{self._e.client_id}-{nome}"),
                )
            for tipo in self.ext.tipos.values():
                if tipo.materializar is not None:
                    tipo.materializar(tuple(v for v in ativas if v.tipo == tipo.nome))
            return {n: sha256(t) for n, t in esperado.items()}

    def carregar_efetivo(self) -> tuple[RuleSet, SkillSet]:
        cid = self._e.client_id
        regras = self.compilado / "rules.yaml"
        skills = self.compilado / "skills.yaml"
        ruleset = RuleSet.model_validate(
            yaml.safe_load(regras.read_text(encoding="utf-8"))
            if regras.is_file()
            else {"schema_version": 1, "client_id": cid, "rules": []}
        )
        dados = (
            yaml.safe_load(skills.read_text(encoding="utf-8"))
            if skills.is_file()
            else {"schema_version": 1, "client_id": cid, "skills": []}
        )
        dados["skills"] = resolve_skills(
            dados["skills"], self._e._modules, self._e._client_config.domain_allowlist
        )
        return ruleset, SkillSet.model_validate(dados)

    def aplicar_no_motor(self) -> None:
        self._e._ruleset, self._e._skillset = self.carregar_efetivo()

    def _ler(self, caminho: str) -> str | None:
        return dict(listar_notas(self.vault_root)).get(caminho)

    def escanear(self) -> list[Validacao]:
        ctx = self.contexto()
        saida = []
        for caminho, texto in listar_notas(self.vault_root):
            try:
                tipo = parse_nota(texto).frontmatter.get("tipo")
            except ConfigNotaErro:
                continue
            if tipo in self.ext.tipos:
                saida.append(validar_nota(caminho, texto, self.ext, ctx))
        return saida

    def _pendentes(self) -> dict[str, str]:
        pend = {}
        for op in self._e.operacoes.listar(AGUARDANDO):
            if op.skill != OPERACAO_ATIVAR:
                continue
            atual = self._e._carregar_operacao(op.id)
            if atual is not None and atual.status == AGUARDANDO:
                pend[atual.argumentos["hash"]] = atual.id
        return pend

    def sincronizar_conhecimento(self, validacoes: list[Validacao]) -> list[str]:
        mudou = []
        with Session(self._e._state.engine) as s:
            for v in validacoes:
                if not (v.tipo and self.ext.tipos[v.tipo].conhecimento):
                    continue
                row = s.get(ConhecimentoRow, v.caminho)
                if row is None:
                    s.add(ConhecimentoRow(caminho=v.caminho, hash=v.hash))
                elif row.hash != v.hash:
                    self._auditar(
                        "conhecimento_alterado",
                        AuditDecision.ALLOWED,
                        caminho=v.caminho,
                        hash_anterior=row.hash,
                        hash_novo=v.hash,
                    )
                    row.hash = v.hash
                    row.visto_em = datetime.now(UTC)
                    mudou.append(v.caminho)
                    continue
                else:
                    continue
            s.commit()
        return mudou

    def estado(self) -> list[dict[str, Any]]:
        validacoes = self.escanear()
        self.sincronizar_conhecimento(validacoes)
        ultimas = {r.caminho: r for r in self.snapshots.ultimas()}
        pend = self._pendentes()
        itens: list[dict[str, Any]] = []
        vistos = set()
        for v in validacoes:
            vistos.add(v.caminho)
            ativa = ultimas.get(v.caminho)
            item: dict[str, Any] = {
                "caminho": v.caminho,
                "tipo": v.tipo,
                "id": v.config_id,
                "hash": v.hash,
                "erros": list(v.erros),
                "versao_ativa": ativa.versao if ativa else None,
                "aprovado_por": ativa.aprovado_por if ativa else None,
                "aprovado_em": ativa.aprovado_em.isoformat() if ativa else None,
                "proposto_por": ativa.proposto_por if ativa else None,
                "operacao_pendente": pend.get(v.hash),
            }
            tipo = self.ext.tipos[v.tipo] if v.tipo else None
            if tipo is not None and tipo.conhecimento:
                est, msg = avaliar_conhecimento(v.frontmatter, self.hoje())
                item.update(
                    estado="conhecimento",
                    conhecimento=est,
                    aviso=msg,
                    frontmatter=dict(v.frontmatter),
                )
            elif v.rascunho:
                item["estado"] = "rascunho"
            elif v.ok and ativa and ativa.hash == v.hash:
                item["estado"] = "ativa"
            elif v.ok and ativa:
                item["estado"] = "alterada_aguardando_aprovacao"
            elif v.ok:
                item["estado"] = "pendente_aprovacao"
            elif ativa:
                item["estado"] = "invalida_versao_anterior_ativa"
            else:
                item["estado"] = "invalida"
            itens.append(item)
        for caminho, r in ultimas.items():
            if caminho not in vistos:
                itens.append(
                    {
                        "caminho": caminho,
                        "tipo": r.tipo,
                        "id": r.config_id,
                        "hash": r.hash,
                        "erros": [],
                        "estado": "removida_do_caderno_versao_ativa_mantida",
                        "versao_ativa": r.versao,
                        "aprovado_por": r.aprovado_por,
                        "aprovado_em": r.aprovado_em.isoformat(),
                        "proposto_por": r.proposto_por,
                        "operacao_pendente": None,
                    }
                )
        return sorted(itens, key=lambda i: i["caminho"])

    def _impacto_regras(self, v: Validacao) -> list[str]:
        ativas = [x for x in self.verificar_snapshots() if x.tipo == "regra"]
        base = [x for x in ativas if x.caminho != v.caminho]
        nova = VersaoAtiva(
            caminho=v.caminho, tipo="regra", config_id=v.config_id or "", versao=0,
            dados=v.dados or {}, hash=v.hash, proposto_por="", aprovado_por="",
            aprovado_em=datetime.now(UTC), operacao_id="", vigente_de=v.vigente_de,
            ordem=v.ordem,
        )  # fmt: skip
        tipo = self.ext.tipos["regra"]
        assert tipo.projetar is not None
        depois = RuleSet.model_validate(
            yaml.safe_load(tipo.projetar(self._e.client_id, [*base, nova]))
        )
        antes = RuleSet.model_validate(
            yaml.safe_load(tipo.projetar(self._e.client_id, base))
            if base
            else {"schema_version": 1, "client_id": self._e.client_id, "rules": []}
        )
        cenarios = self._e.demo_scenarios().scenarios
        if not cenarios:
            return ["Sem cenários de simulação cadastrados: impacto por pessoa não calculado."]
        linhas = []
        with self._e.vault_snapshot():
            for user in self._e.user_store.list_users():
                token = set_current_user(user)
                try:
                    for sc in cenarios:
                        a = self._e.avaliar_skill(sc.skill, sc.arguments, ruleset=antes)["efeito"]
                        d = self._e.avaliar_skill(sc.skill, sc.arguments, ruleset=depois)["efeito"]
                        if a != d:
                            linhas.append(
                                f"{user.identity} ({user.role.value}) em '{sc.title}': {a} -> {d}"
                            )
                finally:
                    current_user_var.reset(token)
        return linhas or ["Nenhum cenário cadastrado muda de decisão."]

    def _impacto_gatilho(self, v: Validacao) -> list[str]:
        d = v.dados or {}
        linhas = [
            "O gatilho passa a executar sozinho, sem pessoa:",
            f"- ator sistema:{d['id']} com papel {d['papel']} e permissões "
            f"{', '.join(d['permissoes'])} (o RBAC do cliente também precisa conceder)",
            f"- ação {d['acao']['skill']}: só A0 e A1 executam; A2 e A3 viram operação "
            "aguardando aprovação de uma pessoa",
            f"- saída {d['saida']['tipo']} para {d['destinatario']}, no máximo "
            f"{d['limites']['max_por_hora']} execução(ões) por hora",
        ]
        try:
            simulado = self._e.gatilhos.simular(dados=d)
            linhas.append(
                f"Se rodasse agora: {simulado.get('decisao', '?')}"
                + (f" ({simulado['detalhe']})" if simulado.get("detalhe") else "")
            )
        except Exception as exc:
            linhas.append(f"Simulação indisponível: {type(exc).__name__}")
        return linhas

    def impacto(self, v: Validacao) -> list[str]:
        assert v.tipo and v.dados is not None
        anterior = self.snapshots.ativa(v.caminho)
        antes = (
            versao_de_snapshot(anterior, self.ext, self.contexto(ativos=False)).dados
            if anterior
            else None
        )
        linhas = [
            "Mudanças no bloco (+ novo, - removido, ~ alterado):",
            *(diff_legivel(antes, v.dados) or ["(nenhuma diferença no bloco)"]),
        ]
        if v.tipo == "regra":
            linhas += ["Quem passa a poder o quê:", *self._impacto_regras(v)]
        elif v.tipo == "gatilho":
            linhas += self._impacto_gatilho(v)
        elif v.tipo == "skill":
            linhas.append(
                "A skill só fica disponível a quem o RBAC conceder (padrão: negar); "
                "novas tools aparecem após reiniciar o servidor."
            )
        return linhas[:MAX_LINHAS_IMPACTO]

    def _negado(self, motivo: str, **extra: Any) -> dict[str, Any]:
        return {"allowed": False, "reason": motivo, **extra}

    def _propor_texto(self, user: UserProfile, caminho: str, texto: str, motivo: str) -> dict:
        v = validar_nota(caminho, texto, self.ext, self.contexto())
        if v.rascunho:
            return self._negado("nota em rascunho: remova 'status: rascunho' depois de conferir")
        if not v.ok or v.tipo is None:
            self._auditar("config_proposta", AuditDecision.DENIED, user.identity,
                          caminho=caminho, erros=list(v.erros))  # fmt: skip
            return self._negado("a nota não passou na validação", erros=list(v.erros))
        tipo = self.ext.tipos[v.tipo]
        if not tipo.aprovacao:
            return {
                "allowed": True,
                "status": "sem_aprovacao",
                "mensagem": "Conhecimento não exige aprovação; mudança auditada por hash.",
            }
        ativa = self.snapshots.ativa(caminho)
        if ativa is not None and ativa.hash == v.hash:
            return self._negado("esta versão já está ativa")
        existente = self._pendentes().get(v.hash)
        if existente:
            return {"allowed": True, "status": AGUARDANDO, "operacao_id": existente,
                    "mensagem": "Já existe uma proposta pendente para este conteúdo."}  # fmt: skip
        linhas = self.impacto(v)
        resumo = (
            f"{motivo} {v.tipo} '{v.config_id}' ({caminho}), conteúdo {v.hash[:12]}.\n"
            + "\n".join(linhas)
        )
        op = self._e.operacoes.criar(
            skill=OPERACAO_ATIVAR,
            argumentos={
                "caminho": caminho,
                "tipo": v.tipo,
                "id": v.config_id,
                "hash": v.hash,
                "conteudo": texto,
            },  # fmt: skip
            resumo=resumo,
            status=AGUARDANDO,
            proposto_por=user.identity,
            proposto_papel=user.role.value,
            aprovador_papel=tipo.aprovador,
            regra_id=None,
            ttl_horas=self._e._client_config.operacao_ttl_horas,
        )
        self._e._audit_operacao(op, AGUARDANDO, AuditDecision.PENDING_APPROVAL)
        return {"allowed": True, "status": AGUARDANDO, "operacao_id": op.id, "resumo": resumo,
                "aprovador_papel": tipo.aprovador}  # fmt: skip

    def propor(self, user: UserProfile, caminho: str) -> dict[str, Any]:
        if not self.ativo:
            return self._negado("este cliente não usa configuração via notas")
        if user.role not in GESTORES:
            return self._negado("Somente gestor propõe configuração.")
        texto = self._ler(caminho)
        if texto is None:
            return self._negado(f"nota de configuração não encontrada: {caminho}")
        with self._lock:
            return self._propor_texto(user, caminho, texto, "Ativar")

    def reverter(self, user: UserProfile, caminho: str) -> dict[str, Any]:
        if not self.ativo:
            return self._negado("este cliente não usa configuração via notas")
        if user.role not in GESTORES:
            return self._negado("Somente gestor propõe reversão.")
        hist = self.snapshots.historico(caminho)
        if len(hist) < 2:
            return self._negado("não há versão anterior para reverter")
        with self._lock:
            return self._propor_texto(
                user, caminho, hist[-2].conteudo, "Reverter para versão anterior de"
            )

    def executar_ativacao(self, op: Any) -> dict[str, Any]:
        a = op.argumentos
        with self._lock:
            return self._ativar(op, a)

    def _ativar(self, op: Any, a: dict[str, Any]) -> dict[str, Any]:
        def falha(motivo: str, **extra: Any) -> dict[str, Any]:
            self._auditar("config_ativacao_recusada", AuditDecision.DENIED,
                          op.aprovado_por or op.proposto_por, caminho=a.get("caminho"),
                          motivo=motivo, operacao_id=op.id)  # fmt: skip
            return self._negado(motivo, **extra)

        if sha256(a["conteudo"]) != a["hash"]:
            return falha("conteúdo da operação não confere com o hash aprovado")
        if not op.aprovado_por or op.aprovado_por == op.proposto_por:
            return falha("ativação exige aprovador identificado e diferente de quem propôs")
        existentes = [r for r in self.snapshots.historico(a["caminho"]) if r.operacao_id == op.id]
        if existentes:
            return {"allowed": True, "caminho": a["caminho"], "versao": existentes[0].versao,
                    "hash": a["hash"], "idempotente": True}  # fmt: skip
        limite = self._e._client_config.config_aprovacoes_por_hora
        if self.snapshots.aprovacoes_na_ultima_hora(op.aprovado_por) >= limite:
            return falha(f"limite de {limite} aprovações de configuração por hora atingido")

        v = validar_nota(a["caminho"], a["conteudo"], self.ext, self.contexto())
        if not v.ok or v.tipo is None or v.config_id is None:
            return falha("a nota não valida mais", erros=list(v.erros))
        tipo = self.ext.tipos[v.tipo]
        ativas = self.verificar_snapshots()
        anterior = self.snapshots.ativa(a["caminho"])
        provisoria = VersaoAtiva(
            caminho=a["caminho"], tipo=v.tipo, config_id=v.config_id,
            versao=(anterior.versao if anterior else 0) + 1, dados=v.dados or {},
            hash=v.hash, proposto_por=op.proposto_por, aprovado_por=op.aprovado_por,
            aprovado_em=datetime.now(UTC), operacao_id=op.id, vigente_de=v.vigente_de,
            ordem=v.ordem,
        )  # fmt: skip
        novo_conjunto = [x for x in ativas if x.caminho != a["caminho"]] + [provisoria]
        try:
            if tipo.materializar is not None:
                tipo.materializar(tuple(x for x in novo_conjunto if x.tipo == v.tipo))
            esperado = self._esperado(novo_conjunto)
        except Exception as exc:
            return falha(f"falha ao materializar: {exc}")
        row = self.snapshots.gravar(
            caminho=a["caminho"], tipo=v.tipo, config_id=v.config_id, conteudo=a["conteudo"],
            proposto_por=op.proposto_por, aprovado_por=op.aprovado_por, operacao_id=op.id,
        )  # fmt: skip
        for nome, texto in esperado.items():
            escrever_atomico(self.compilado / nome, texto)
        self.aplicar_no_motor()
        self._auditar("config_ativada", AuditDecision.ALLOWED, op.aprovado_por,
                      caminho=row.caminho, tipo=row.tipo, id=row.config_id, versao=row.versao,
                      hash=row.hash, proposto_por=row.proposto_por, operacao_id=op.id)  # fmt: skip
        try:
            self.atualizar_paginas()
        except Exception:
            pass
        return {"allowed": True, "caminho": row.caminho, "versao": row.versao, "hash": row.hash}

    def rascunho_de_draft(self, user: UserProfile, draft_id: str, kind: str) -> dict[str, Any]:
        if not self.ativo:
            return self._negado("este cliente não usa configuração via notas")
        if user.role not in GESTORES:
            return self._negado("Somente gestor gera rascunho de configuração.")
        if kind not in ("rule", "skill"):
            return self._negado("kind deve ser 'rule' ou 'skill'")
        draft = self._e._draft_store.get_draft(self._e.client_id, kind, draft_id)
        if draft is None:
            return self._negado("draft não encontrado")
        conteudo = draft["content"]
        config_id = str(conteudo.get("id") or conteudo.get("name") or "")
        tipo = "regra" if kind == "rule" else "skill"
        cab = yaml.safe_dump(
            {"tipo": tipo, "id": config_id, "status": "rascunho", "origem_draft": draft_id},
            sort_keys=False, allow_unicode=True,
        )  # fmt: skip
        bloco = yaml.safe_dump(conteudo, sort_keys=True, allow_unicode=True)
        texto = (
            f"---\n{cab}---\nRascunho gerado pelo interpretador a partir de "
            f"`{draft['from_note_id']}`. Confira o bloco, remova `status: rascunho` do "
            "cabeçalho e peça a proposta de ativação.\n\n```brm-config\n"
            f"{bloco}```\n"
        )
        if not config_id or not id_valido(config_id):
            return self._negado("o rascunho não tem um id utilizável")
        rel = f"rascunhos-config/{config_id}.md"
        destino = self.vault_root / rel
        if destino.exists():
            return self._negado(f"já existe {rel}; remova ou renomeie antes")
        escrever_atomico(destino, texto)
        self._auditar("config_rascunho_gerado", AuditDecision.ALLOWED, user.identity,
                      caminho=rel, draft_id=draft_id)  # fmt: skip
        return {"allowed": True, "nota": rel}

    def atualizar_paginas(self) -> dict[str, str]:
        with self._lock:
            return paginas.atualizar(self)

    def on_nota_alterada(self, note_id: str) -> None:
        if not self.ativo:
            return
        try:
            self.atualizar_paginas()
        except Exception as exc:
            self._auditar("config_paginas_falha", AuditDecision.DENIED, motivo=str(exc))


def principal(argv: list[str] | None = None) -> int:
    import argparse
    import os

    from mcp_engine.core.mcp_server import BRMEngine

    ap = argparse.ArgumentParser(description="Verifica ou reconstrói o compilado/ do cliente")
    ap.add_argument("comando", choices=["verificar", "reconstruir"])
    ap.add_argument("cliente")
    ap.add_argument("--config-root", default=os.getenv("CONFIG_ROOT", "clients"))
    ap.add_argument("--data-root", default=os.getenv("DATA_ROOT", "data"))
    args = ap.parse_args(argv)
    try:
        eng = BRMEngine(args.cliente, Path(args.config_root), Path(args.data_root),
                        skip_config_integrity=True)  # fmt: skip
    except Exception as exc:
        print(f"erro: {exc}")
        return 2
    cad = eng.caderno
    if args.comando == "verificar":
        try:
            problemas = cad.divergencias()
        except CompiladoDivergente as exc:
            print(f"DIVERGENTE: {exc}")
            return 1
        print("divergências: " + (", ".join(problemas) or "nenhuma"))
        return 1 if problemas else 0
    print({k: v[:12] for k, v in cad.reconstruir().items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(principal())
