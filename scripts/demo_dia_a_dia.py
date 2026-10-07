"""Demo ao vivo do BRM Framework para apresentação a clientes.

Roda as 6 cenas de "um dia com o BRM" (ver docs/guia-apresentacao-clientes.md)
contra o motor real do cliente `example`, usando as regras e skills
efetivamente promovidas em clients/example/config/{rules,skills}.yaml.
Nenhuma chamada real acontece (Fase 1 é mocked-by-design) e a skill
`enviar_email` nunca é chamada aqui de propósito, para não disparar um
e-mail real caso SMTP_* esteja configurado no ambiente.

Uso: uv run python scripts/demo_dia_a_dia.py
"""

from __future__ import annotations

import sys
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

JULIA = UserProfile(identity="julia.ferraz@nogueira.adv.br", role=Role.VIEWER)
RAFAEL = UserProfile(identity="rafael.duarte@nogueira.adv.br", role=Role.ADMIN)
BIANCA = UserProfile(identity="bianca.nogueira@nogueira.adv.br", role=Role.SUPER_ADMIN)

PERSONAS = {
    "julia": ("Júlia Ferraz", "viewer · estagiária", JULIA),
    "rafael": ("Rafael Duarte", "admin · advogado", RAFAEL),
    "bianca": ("Dra. Bianca Nogueira", "super_admin · sócia", BIANCA),
}


def pause(msg: str = "Pressione Enter para continuar...") -> None:
    input(f"\n{DIM}» {msg}{RESET}")


def header(n: int, total: int, title: str) -> None:
    print(f"\n{BOLD}{CYAN}── Cena {n}/{total} ── {title}{RESET}")


def run(persona_key: str, skill: str, args: dict[str, Any], note: str = "") -> None:
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
    else:
        print(f"{RED}{BOLD}✕ DENY{RESET}  {result.get('reason')}")


def main() -> None:
    global ENGINE
    ENGINE = BRMEngine(
        client_id="example",
        config_root=Path("clients"),
        data_root=Path("data"),
    )

    print(f"{BOLD}BRM Framework — demonstração ao vivo{RESET}")
    print(f"{DIM}Nogueira & Duarte Advocacia (dados fictícios) · cliente 'example'{RESET}")
    pause("Pressione Enter para começar")

    processo = "0829581-29.2014.8.12.0001"

    header(1, 6, "Júlia tenta reagendar uma audiência")
    reagendamento = {
        "numero_processo": processo,
        "nova_data_audiencia": "2026-10-14",
        "motivo": "conflito de agenda",
    }
    run(
        "julia",
        "reagendar_audiencia",
        reagendamento,
        "Estagiária, sem validar antes com o advogado responsável.",
    )
    pause()
    run(
        "rafael",
        "reagendar_audiencia",
        reagendamento,
        "Mesmo pedido, agora pelo advogado responsável.",
    )
    pause()

    header(2, 6, "Cobrança de honorário em atraso — Comércio Ferreira & Cia")
    cobranca = {
        "destinatario": "financeiro@comercioferreira.com.br",
        "cliente": "Comércio Ferreira & Cia",
        "valor": "R$ 3.100,00",
        "dias_atraso": 12,
    }
    run(
        "julia",
        "cobrar_honorario_atrasado",
        cobranca,
        "Estagiária tentando disparar a cobrança sozinha.",
    )
    pause()
    run(
        "rafael",
        "cobrar_honorario_atrasado",
        cobranca,
        "Mesma cobrança, agora autorizada pelo advogado responsável.",
    )
    pause()

    header(3, 6, "Notificação de movimentação processual ao cliente")
    run(
        "bianca",
        "notificar_cliente_movimentacao",
        {
            "destinatario": "cliente@exemplo.com.br",
            "numero_processo": processo,
            "resumo_movimentacao": "Trânsito em julgado da decisão",
        },
        "Mesmo a sócia não consegue pular a revisão humana antes do envio.",
    )
    pause()

    header(4, 6, "Arquivamento de processo com prazo em aberto")
    run(
        "rafael",
        "arquivar_processo",
        {"numero_processo": processo, "motivo": "baixa administrativa", "prazo_dias_restantes": 2},
        "Faltam só 2 dias para um prazo — o sistema trava sozinho.",
    )
    pause()
    run(
        "rafael",
        "arquivar_processo",
        {"numero_processo": processo, "motivo": "baixa administrativa", "prazo_dias_restantes": 15},
        "Sem prazo crítico em aberto, o mesmo pedido passa direto.",
    )
    pause()

    header(5, 6, "Dado financeiro sensível: ERP e mascaramento")
    run(
        "julia",
        "consultar_erp",
        {"filtro": "conta ACCT-118820"},
        "Perfil viewer não tem acesso a dado financeiro do ERP — bloqueio total.",
    )
    pause()
    andamento_com_conta = {"numero_processo": processo, "obs": "conta vinculada ACCT-118820"}
    run(
        "julia",
        "consulta-andamento-processual",
        andamento_com_conta,
        "Consulta permitida, mas o número de conta que aparecer é redigido para o perfil dela.",
    )
    pause()
    run(
        "rafael",
        "consulta-andamento-processual",
        andamento_com_conta,
        "Mesma consulta, perfil advogado: dado completo.",
    )
    pause()

    header(6, 6, "Petição de alto valor — revisão de sócio")
    peca_alto_valor = {
        "numero_processo": processo,
        "tipo_peca": "inicial",
        "conteudo": "...",
        "valor_causa": 120000,
    }
    run(
        "rafael",
        "protocolar_peca_processual",
        peca_alto_valor,
        "Causa de R$ 120.000 — acima do limite que exige aprovação de sócio.",
    )
    pause()
    run(
        "bianca",
        "protocolar_peca_processual",
        peca_alto_valor,
        "A própria sócia executando — a aprovação já está implícita.",
    )
    pause("Pressione Enter para ver a trilha de auditoria desta demonstração")

    print(f"\n{BOLD}{CYAN}── Trilha de auditoria (hash-chained, append-only) ──{RESET}")
    log = ENGINE.query_audit_log()
    for event in log["events"][-13:]:
        decision = event["decision"]
        color = {"allowed": GREEN, "denied": RED, "pending_approval": YELLOW}.get(decision, "")
        line = f"{event['actor']:32s} {event['action']:32s} {color}{decision}{RESET}"
        print(f"{DIM}{event['occurred_at']}{RESET}  {line}")

    closing = (
        "Nada aqui foi decidido pela IA sozinha — "
        "cada linha é uma regra que o escritório configurou."
    )
    print(f"\n{BOLD}{closing}{RESET}\n")


if __name__ == "__main__":
    main()
