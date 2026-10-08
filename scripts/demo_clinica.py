"""Demo ao vivo do BRM Framework para clínicas de psicologia.

6 cenas de "um dia na clínica" contra o motor real do cliente `clinica-psi`,
usando as regras/skills de clients/clinica-psi/config e o vault fictício em
clients/clinica-psi/vault. Todos os pacientes, psicólogos e documentos são
inventados. Fase 1 é mocked-by-design: nenhuma chamada externa acontece.

Parte 1 (cenas 1-6): rotina da clínica. Parte 2 (cenas 7-11, SUGESTÃO): o
Instituto como escola, com supervisão, aulas gravadas, frequência, leads e
divulgação de turma.

Uso: uv run python scripts/demo_clinica.py [--auto] [--parte1]
  --auto    roda sem pausas (para teste/gravação)
  --parte1  só as 6 cenas da clínica
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_engine.core.auth import Role, UserProfile, set_current_user  # noqa: E402
from mcp_engine.core.mcp_server import BRMEngine  # noqa: E402

BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"

AUTO = "--auto" in sys.argv
PARTE1 = "--parte1" in sys.argv
TOTAL = 6 if PARTE1 else 11

LUCAS = UserProfile(identity="lucas.ribeiro@clinica-demo.com.br", role=Role.ADMIN)
WAGNER = UserProfile(identity="wagner@clinica-demo.com.br", role=Role.ADMIN)
CAMILA = UserProfile(identity="camila.souza@clinica-demo.com.br", role=Role.VIEWER)
ANA = UserProfile(identity="ana.weis@clinica-demo.com.br", role=Role.ADMIN)
HELENA = UserProfile(identity="helena.prado@clinica-demo.com.br", role=Role.SUPER_ADMIN)
BEATRIZ = UserProfile(identity="beatriz.lima@aluno-demo.com.br", role=Role.VIEWER)
RODRIGO = UserProfile(identity="rodrigo.azevedo@clinica-demo.com.br", role=Role.ADMIN)

PERSONAS = {
    "lucas": ("Lucas Ribeiro", "admin · psicólogo", LUCAS),
    "wagner": ("Wagner Massaranduba", "admin · psicólogo", WAGNER),
    "camila": ("Camila Souza", "viewer · recepção", CAMILA),
    "ana": ("Ana Flávia Weis", "admin · psicóloga e diretora", ANA),
    "helena": ("Dra. Helena Prado", "super_admin · responsável técnica", HELENA),
    "beatriz": ("Beatriz Lima", "viewer · aluna da pós", BEATRIZ),
    "rodrigo": ("Prof. Rodrigo Azevedo", "admin · supervisor", RODRIGO),
}

ENGINE: BRMEngine


def pause(msg: str = "Pressione Enter para continuar...") -> None:
    if not AUTO:
        input(f"\n{DIM}» {msg}{RESET}")


def header(n: int, total: int, title: str) -> None:
    print(f"\n{BOLD}{CYAN}── Cena {n}/{total} ── {title}{RESET}")


def run(persona_key: str, skill: str, args: dict[str, Any], note: str = "") -> dict[str, Any]:
    nome, papel, profile = PERSONAS[persona_key]
    set_current_user(profile)
    print(f"\n{BOLD}{nome}{RESET} {DIM}({papel}){RESET} chama {BOLD}{skill}{RESET}")
    if note:
        print(f"{DIM}{note}{RESET}")

    result = ENGINE.call_skill(skill, args)

    if result["allowed"]:
        print(f"{GREEN}{BOLD}✓ ALLOWED{RESET}  {DIM}{result.get('result', '')}{RESET}")
    elif result.get("reason") == "human approval required":
        print(f"{YELLOW}{BOLD}⏸ REQUIRE_APPROVAL{RESET}  regra: {result.get('rule_id')}")
        print(f"{DIM}  {_rule_message(result.get('rule_id'))}{RESET}")
    else:
        print(f"{RED}{BOLD}✕ DENY{RESET}  {result.get('reason')}")
    return result


def _rule_message(rule_id: str | None) -> str:
    for rule in ENGINE.rules.rules:
        if rule.id == rule_id:
            return rule.message or ""
    return ""


def nota_paciente(paciente_id: str, tipo: str) -> dict[str, Any] | None:
    for note in ENGINE.vault_list_notes()["notes"]:
        fm = note["frontmatter"]
        if fm.get("paciente_id") == paciente_id and fm.get("tipo") == tipo:
            return note
    return None


def mostrar_prontuario(paciente_id: str) -> None:
    note = nota_paciente(paciente_id, "prontuario")
    if note is None:
        print(f"{DIM}(nenhum prontuário encontrado){RESET}")
        return
    print(f"\n{DIM}Contexto entregue ao Claude a partir do vault ({note['note_id']}):{RESET}")
    evolucao = note["content"].split("## Evolução", 1)[-1].split("## Encaminhamento", 1)[0]
    for line in evolucao.strip().splitlines()[-4:]:
        print(f"  {DIM}{line}{RESET}")
    print(
        f"{DIM}  → No uso real, o Claude lê este contexto e responde "
        f"(ex.: resumo das últimas sessões e tarefa pendente).{RESET}"
    )
    print(
        f"{DIM}  → O registro documental (hipóteses) é outra nota, fora do que é entregue.{RESET}"
    )


def mostrar_trechos(result: dict[str, Any]) -> None:
    for note in result.get("vault", []):
        trecho = " ".join(note["content"].split("\n\n")[1].split())
        print(f"  {DIM}[{note['note_id']}]{RESET} {trecho[:230]}…")
    print(f"{DIM}  → No uso real, o Claude responde com base nestes trechos e cita a fonte.{RESET}")
    print(f"{DIM}  → O vault só entrega os tipos de nota que a skill declara (aula, FAQ).{RESET}")


def parte2() -> None:
    print(f"\n{BOLD}{YELLOW}══ PARTE 2 · SUGESTÃO · O Instituto como escola ══{RESET}")
    print(
        f"{DIM}Cenas sugeridas a partir da página de pós-graduação. Validar com o Instituto.{RESET}"
    )
    pause()

    header(7, TOTAL, "SUGESTÃO · Caso clínico do aluno na supervisão")
    caso_bruto = (
        "Paciente João S., CPF 123.456.789-09, tel (67) 99999-1234, 34 anos, "
        "ansiedade em avaliações no trabalho."
    )
    run(
        "beatriz",
        "enviar_caso_supervisao",
        {"aluno_id": "ALU-017", "trilha": "tcc", "caso_texto": caso_bruto},
        "A aluna cola o caso com dados do paciente. O DLP barra antes de sair.",
    )
    pause()
    run(
        "beatriz",
        "enviar_caso_supervisao",
        {
            "aluno_id": "ALU-017",
            "trilha": "tcc",
            "caso_texto": "Paciente P., adulto, ansiedade em avaliações no trabalho, 8 sessões.",
        },
        "Mesmo caso, anonimizado: segue para a supervisão.",
    )
    pause()
    run(
        "beatriz",
        "compartilhar_caso_grupo",
        {"caso_id": "CASO-042"},
        "Levar o caso ao grupo exige a revisão do supervisor.",
    )
    pause()
    run(
        "rodrigo",
        "compartilhar_caso_grupo",
        {"caso_id": "CASO-042"},
        "O supervisor confere a anonimização e libera.",
    )
    print(
        f"{DIM}  Limite: o DLP pega padrões (CPF, telefone). Nome próprio solto exige "
        f"uma etapa de NER, que está no roadmap.{RESET}"
    )
    pause()

    header(8, TOTAL, "SUGESTÃO · Perguntas ao conteúdo gravado das aulas")
    pergunta = "o que é o modo Criança Vulnerável e como formular o caso"
    run(
        "beatriz",
        "buscar_aulas",
        {"pergunta": pergunta, "aluno_id": "ALU-023"},
        "Aluno com matrícula trancada (ALU-023) tenta consultar as aulas.",
    )
    pause()
    result = run(
        "beatriz",
        "buscar_aulas",
        {"pergunta": pergunta, "aluno_id": "ALU-017"},
        "Matrícula ativa (ALU-017): a busca roda sobre as transcrições das aulas.",
    )
    if result["allowed"]:
        mostrar_trechos(result)
    pause()

    header(9, TOTAL, "SUGESTÃO · Declaração de horas e frequência")
    run(
        "camila",
        "emitir_declaracao_horas",
        {"aluno_id": "ALU-023"},
        "ALU-023 tem 62% de frequência no cadastro, abaixo do mínimo (75% é ilustrativo).",
    )
    pause()
    run(
        "camila",
        "emitir_declaracao_horas",
        {"aluno_id": "ALU-017"},
        "ALU-017 tem 88% de frequência: a secretaria emite sem incomodar ninguém.",
    )
    pause()

    header(10, TOTAL, "SUGESTÃO · Interessado chega pelo WhatsApp")
    result = run(
        "camila",
        "responder_lead_whatsapp",
        {
            "telefone": "+55 67 90000-0000",
            "mensagem": "Quem pode participar e como são os encontros?",
            "menciona_valor_ou_vaga": False,
        },
        "Pergunta sobre requisitos e formato: respondida a partir do FAQ do vault.",
    )
    if result["allowed"]:
        mostrar_trechos(result)
    pause()
    run(
        "camila",
        "responder_lead_whatsapp",
        {
            "telefone": "+55 67 90000-0000",
            "mensagem": "Quanto custa e ainda tem vaga?",
            "menciona_valor_ou_vaga": True,
        },
        "Valor e vaga não estão no FAQ: uma pessoa da secretaria decide.",
    )
    pause()

    header(11, TOTAL, "SUGESTÃO · Divulgação de turma")
    run(
        "camila",
        "publicar_post_turma",
        {
            "trilha": "terapia-do-esquema",
            "texto": "Faça a pós e fique livre da ansiedade para sempre!",
            "promete_resultado": True,
        },
        "Texto que promete resultado ao paciente futuro de quem fizer o curso.",
    )
    pause()
    run(
        "camila",
        "publicar_post_turma",
        {
            "trilha": "terapia-do-esquema",
            "texto": "Pós em Terapia do Esquema: encontros mensais, online e presencial.",
            "promete_resultado": False,
        },
        "Texto que descreve conteúdo e formato.",
    )
    pause("Pressione Enter para ver a trilha de auditoria desta demonstração")


def main() -> None:
    global ENGINE
    data_root = Path(tempfile.mkdtemp(prefix="brm-clinica-demo-"))
    ENGINE = BRMEngine(
        client_id="instituto-afw",
        config_root=Path("clients"),
        data_root=data_root,
    )
    ENGINE._vault_provider.reindex(ENGINE.client_id)

    print(f"{BOLD}BRM Framework — demonstração ao vivo{RESET}")
    print(
        f"{DIM}Instituto de psicologia (pacientes e alunos fictícios) · "
        f"cliente 'instituto-afw'{RESET}"
    )
    pause("Pressione Enter para começar")

    header(1, TOTAL, "O psicólogo consulta o prontuário do próprio paciente")
    result = run(
        "lucas",
        "consultar_prontuario",
        {"paciente_id": "PAC-001"},
        "Pergunta ao Claude: resuma as últimas sessões do PAC-001 e a tarefa pendente.",
    )
    if result["allowed"]:
        mostrar_prontuario("PAC-001")
    pause()

    header(2, TOTAL, "Quem não tem vínculo não abre o prontuário")
    run(
        "camila",
        "consultar_prontuario",
        {"paciente_id": "PAC-001"},
        "Recepção tentando abrir o prontuário.",
    )
    pause()
    run(
        "wagner",
        "consultar_prontuario",
        {"paciente_id": "PAC-001"},
        "Outro psicólogo da clínica, sem vínculo com este paciente.",
    )
    pause()

    header(3, TOTAL, "Declaração de comparecimento: só dia, hora e duração")
    declaracao = {"paciente_id": "PAC-001", "finalidade": "justificativa no trabalho"}
    run(
        "camila",
        "emitir_declaracao_comparecimento",
        {**declaracao, "incluir_sintomas": False},
        "Recepção emite a declaração padrão, sem conteúdo clínico.",
    )
    pause()
    run(
        "camila",
        "emitir_declaracao_comparecimento",
        {**declaracao, "incluir_sintomas": True},
        "Alguém pede para constar 'ansiedade' na declaração.",
    )
    pause()

    header(4, TOTAL, "Relatório para a escola: rascunho + entrevista devolutiva")
    relatorio = {"paciente_id": "PAC-002", "solicitante": "escola", "finalidade": "acompanhamento"}
    run(
        "camila",
        "emitir_relatorio_psicologico",
        relatorio,
        "Recepção tenta gerar o relatório do adolescente.",
    )
    pause()
    run(
        "wagner",
        "emitir_relatorio_psicologico",
        relatorio,
        "O psicólogo responsável pede o rascunho. Nada sai sem a devolutiva.",
    )
    pause()

    header(5, TOTAL, "Pai pede o prontuário do filho adolescente")
    run(
        "wagner",
        "entregar_prontuario_responsavel",
        {"paciente_id": "PAC-002", "responsavel": "Marcos (pai)"},
        "Menor de idade: confere o responsável legal e o que será entregue.",
    )
    pause()
    run(
        "lucas",
        "entregar_prontuario_responsavel",
        {"paciente_id": "PAC-001", "responsavel": "o próprio paciente"},
        "Paciente adulto pedindo o próprio prontuário (direito de acesso integral).",
    )
    pause()

    header(6, TOTAL, "Dado sensível no WhatsApp e na cobrança")
    run(
        "camila",
        "enviar_lembrete_sessao",
        {
            "telefone": "+55 67 90000-0000",
            "mensagem": "Lembrete: sessão amanhã às 15h (acompanhamento F41.1).",
        },
        "A mensagem escapou com um CID — para a recepção, ele é redigido.",
    )
    pause()
    run(
        "camila",
        "cobrar_mensalidade",
        {
            "destinatario": "responsavel@exemplo.com.br",
            "valor": "R$ 480,00",
            "dias_atraso": 10,
            "mensagem": "Titular CPF 123.456.789-09",
        },
        "Cobrança pela recepção: exige aprovação da gestão.",
    )
    pause()
    run(
        "helena",
        "cobrar_mensalidade",
        {
            "destinatario": "responsavel@exemplo.com.br",
            "valor": "R$ 480,00",
            "dias_atraso": 10,
            "mensagem": "Titular CPF 123.456.789-09",
        },
        "Gestão envia, mas o CPF no texto é bloqueado pelo DLP.",
    )
    if PARTE1:
        pause("Pressione Enter para ver a trilha de auditoria desta demonstração")
    else:
        pause("Pressione Enter para a Parte 2 (sugestão)")
        parte2()

    print(f"\n{BOLD}{CYAN}── Trilha de auditoria (hash-chained, append-only) ──{RESET}")
    log = ENGINE.query_audit_log()
    for event in log["events"]:
        decision = event["decision"]
        color = {"allowed": GREEN, "denied": RED, "pending_approval": YELLOW}.get(decision, "")
        line = f"{event['actor']:42s} {event['action']:34s} {color}{decision}{RESET}"
        print(f"{DIM}{event['occurred_at']}{RESET}  {line}")

    closing = (
        "Nada aqui foi decidido pela IA sozinha — cada linha é uma regra que a clínica configurou."
    )
    print(f"\n{BOLD}{closing}{RESET}\n")


if __name__ == "__main__":
    main()
