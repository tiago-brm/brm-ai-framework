"""Casos de uso sobre a carteira de pacientes do cliente `instituto-afw`.

Cinco pacientes fictícios (idades e abordagens diferentes) com histórico de
sessões no vault. Cada cena parte de dados que já estão lá: frontmatter dos
prontuários, notas de resumo e registro documental. As integrações seguem
simuladas.

Uso: uv run python scripts/demo_casos_de_uso.py [--auto]
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import demo_clinica as d  # noqa: E402

from mcp_engine.core.mcp_server import BRMEngine  # noqa: E402

TOTAL = 7


def prontuarios() -> list[dict]:
    notas = d.ENGINE.vault_list_notes()["notes"]
    return sorted(
        (n for n in notas if n["frontmatter"].get("tipo") == "prontuario"),
        key=lambda n: n["frontmatter"]["paciente_id"],
    )


def psicologo(email: str) -> str:
    return email.split("@")[0].replace(".", " ").title()


def cena_carteira() -> None:
    d.header(1, TOTAL, "A carteira de pacientes do Instituto (dados fictícios)")
    d.set_current_user(d.PERSONAS["helena"][2])
    print(f"{d.DIM}Visão da gestão (responsável técnica).{d.RESET}")
    cab = f"{'ID':8s}{'idade':>6s}  {'unidade':16s}{'abordagem':32s}{'sess.':>6s}{'faltas':>7s}"
    print(f"{d.DIM}{cab}{d.RESET}")
    for n in prontuarios():
        fm = n["frontmatter"]
        print(
            f"{fm['paciente_id']:8s}{fm['idade']:>6}  {fm['unidade']:16s}"
            f"{fm['abordagem']:32s}{fm['sessoes_realizadas']:>6}{fm['faltas']:>7}"
        )
    print(
        f"{d.DIM}Cada paciente tem prontuário, resumo e, às vezes, registro documental.{d.RESET}"
    )
    d.pause()


def cena_mesma_busca() -> None:
    d.header(2, TOTAL, "Controle de acesso: a mesma busca, quatro respostas")
    consulta = "hipótese de trabalho autocrítica procrastinação desligamento"
    print(f"{d.DIM}Busca no vault: \"{consulta}\"{d.RESET}")
    for chave, nota in [
        ("camila", "recepção: só a ficha de identificação"),
        ("lucas", "outro psicólogo: só a ficha de identificação"),
        ("wagner", "psicólogo responsável: o caso completo"),
        ("helena", "responsável técnica: tudo, com acesso registrado"),
    ]:
        nome, papel, perfil = d.PERSONAS[chave]
        d.set_current_user(perfil)
        resultado = d.ENGINE.vault_search(consulta, limit=6)["notes"]
        pac005 = [n for n in resultado if n["frontmatter"].get("paciente_id") == "PAC-005"]
        completo = [n["frontmatter"].get("tipo") for n in pac005 if n["content"]]
        ficha = [n for n in pac005 if not n["content"]]
        print(f"\n{d.BOLD}{nome}{d.RESET} {d.DIM}({papel}){d.RESET} · {nota}")
        print(f"  {d.GREEN}conteúdo recebido:{d.RESET} {', '.join(completo) or 'nenhum'}")
        print(f"  {d.YELLOW}só ficha:{d.RESET} {', '.join(n['title'] for n in ficha) or '—'}")
    d.pause()


def cena_pre_sessao() -> None:
    d.header(3, TOTAL, "Caso de uso 1 · Preparação da sessão em 30 segundos")
    paciente = "PAC-005"
    result = d.run(
        "wagner",
        "consultar_prontuario",
        {"paciente_id": paciente},
        "Wagner abre o caso antes da sessão e pede ao Claude o resumo e os pontos de atenção.",
    )
    if result["allowed"]:
        d.mostrar_prontuario(paciente)
        resumo = d.nota_paciente(paciente, "resumo")
        if resumo:
            print(f"\n{d.DIM}Resumo guardado no vault ({resumo['note_id']}):{d.RESET}")
            corpo = " ".join(resumo["content"].split("\n\n")[1].split())
            print(f"  {d.DIM}{corpo}{d.RESET}")
    d.pause()
    d.run(
        "lucas",
        "consultar_prontuario",
        {"paciente_id": paciente},
        "Outro psicólogo da clínica tenta abrir o mesmo caso: sem vínculo, sem acesso.",
    )
    d.pause()


def cena_faltas() -> None:
    d.header(4, TOTAL, "Caso de uso 2 · Alerta de faltas e contato acolhedor")
    d.set_current_user(d.PERSONAS["helena"][2])
    alvo = [n for n in prontuarios() if n["frontmatter"]["faltas"] >= 2]
    for n in alvo:
        fm = n["frontmatter"]
        print(
            f"{d.YELLOW}Atenção:{d.RESET} {fm['paciente_id']} com {fm['faltas']} faltas "
            f"em {fm['sessoes_realizadas'] + fm['faltas']} encontros "
            f"(psicólogo: {psicologo(fm['psicologo_responsavel'])})."
        )
    d.pause()
    for n in alvo:
        d.run(
            "camila",
            "enviar_lembrete_sessao",
            {
                "telefone": "+55 67 90000-0000",
                "mensagem": "Oi! Sentimos sua falta. Quer reagendar sua sessão desta semana?",
            },
            "Mensagem neutra da recepção, sem citar tratamento, só o convite para remarcar.",
        )
    d.pause()


def cena_encaminhamento() -> None:
    d.header(5, TOTAL, "Caso de uso 3 · Relatório de encaminhamento ao médico")
    paciente = "PAC-004"
    result = d.run(
        "paulo",
        "emitir_relatorio_psicologico",
        {
            "paciente_id": paciente,
            "solicitante": "médico (clínico/geriatra)",
            "finalidade": "avaliação do sono",
        },
        "Paulo precisa encaminhar o paciente para avaliação médica do sono.",
    )
    pront = d.nota_paciente(paciente, "prontuario")
    if pront:
        print(
            f"\n{d.DIM}Insumos que o Claude recebe (Res. CFP 06/2019, 5 itens):{d.RESET}"
        )
        for item, origem in [
            ("Identificação", "frontmatter do prontuário"),
            ("Descrição da demanda", "seção Demanda e objetivos"),
            ("Procedimento", "evolução das 6 sessões"),
            ("Análise", "escrita pelo psicólogo"),
            ("Conclusão", "escrita pelo psicólogo"),
        ]:
            print(f"  {d.DIM}- {item:22s} ← {origem}{d.RESET}")
        print(f"  {d.DIM}O registro documental (hipóteses) não entra no rascunho.{d.RESET}")
    if result.get("reason") == "human approval required":
        print(f"{d.DIM}  → O psicólogo revisa e realiza a devolutiva antes de entregar.{d.RESET}")
    d.pause()


def cena_painel() -> None:
    d.header(6, TOTAL, "Caso de uso 4 · Painel da responsável técnica")
    d.run(
        "camila",
        "consultar_painel_clinica",
        {"periodo": "2026-09"},
        "A recepção tenta abrir o painel da clínica.",
    )
    d.pause()
    result = d.run(
        "helena",
        "consultar_painel_clinica",
        {"periodo": "2026-09"},
        "A responsável técnica vê o painel agregado, sem identificar pacientes.",
    )
    if result["allowed"]:
        agregado: dict[str, list[int]] = {}
        for n in prontuarios():
            fm = n["frontmatter"]
            linha = agregado.setdefault(psicologo(fm["psicologo_responsavel"]), [0, 0, 0])
            linha[0] += 1
            linha[1] += fm["sessoes_realizadas"]
            linha[2] += fm["faltas"]
        print(f"\n{d.DIM}{'psicólogo':18s}{'pacientes':>10s}{'sessões':>9s}{'faltas':>8s}{d.RESET}")
        for nome, (pac, sess, faltas) in agregado.items():
            print(f"{nome:18s}{pac:>10}{sess:>9}{faltas:>8}")
    d.pause()


def cena_cobranca() -> None:
    d.header(7, TOTAL, "Caso de uso 5 · Mensalidades em atraso")
    d.set_current_user(d.PERSONAS["helena"][2])
    atrasadas = [
        n for n in prontuarios() if n["frontmatter"].get("mensalidade_status") == "atrasada"
    ]
    for n in atrasadas:
        fm = n["frontmatter"]
        args = {
            "destinatario": fm["responsavel_financeiro_email"],
            "valor": f"R$ {fm['mensalidade_valor']},00",
            "dias_atraso": fm["mensalidade_dias_atraso"],
            "mensagem": "Lembrete de mensalidade em aberto. Dúvidas? Fale com a secretaria.",
        }
        atraso = fm["mensalidade_dias_atraso"]
        print(f"\n{d.YELLOW}{fm['paciente_id']}{d.RESET} {d.DIM}· {atraso} dias de atraso{d.RESET}")
        d.run("camila", "cobrar_mensalidade", args, "A recepção prepara a cobrança.")
        d.run("helena", "cobrar_mensalidade", args, "A gestão aprova e envia.")
    d.pause("Pressione Enter para ver a trilha de auditoria")


def main() -> None:
    d.ENGINE = BRMEngine(
        client_id="instituto-afw",
        config_root=Path("clients"),
        data_root=Path(tempfile.mkdtemp(prefix="brm-casos-")),
    )
    d.ENGINE._vault_provider.reindex(d.ENGINE.client_id)
    d.PERSONAS["paulo"] = (
        "Paulo Tavares",
        "admin · psicólogo",
        d.UserProfile(identity="paulo.tavares@clinica-demo.com.br", role=d.Role.ADMIN),
    )

    print(f"{d.BOLD}BRM Framework — casos de uso com a carteira de pacientes{d.RESET}")
    print(
        f"{d.DIM}Cliente 'instituto-afw' · 5 pacientes fictícios, histórico no vault{d.RESET}"
    )
    d.pause("Pressione Enter para começar")

    cena_carteira()
    cena_mesma_busca()
    cena_pre_sessao()
    cena_faltas()
    cena_encaminhamento()
    cena_painel()
    cena_cobranca()

    print(f"\n{d.BOLD}{d.CYAN}── Trilha de auditoria ──{d.RESET}")
    for event in d.ENGINE.query_audit_log()["events"]:
        color = {"allowed": d.GREEN, "denied": d.RED, "pending_approval": d.YELLOW}.get(
            event["decision"], ""
        )
        quando = event["occurred_at"][:19]
        acao, decisao = event["action"], event["decision"]
        print(f"{d.DIM}{quando}{d.RESET}  {acao:32s} {color}{decisao}{d.RESET}")
    print(
        f"\n{d.BOLD}Os dados vieram do vault; cada decisão veio de uma regra da clínica.{d.RESET}\n"
    )


if __name__ == "__main__":
    main()
