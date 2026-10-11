from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp_engine.core.executor import HandlerContext
from mcp_engine.modules.financeiro import params
from mcp_engine.modules.financeiro.db import abrir, preparar_banco, transacao

CLIENTE = "cli-teste"


def criar_ledger(raiz: Path) -> Path:
    return preparar_banco(raiz, CLIENTE)


def semear(path: Path) -> dict[str, int]:
    ids: dict[str, int] = {}
    con = abrir(path)
    try:
        with transacao(con):
            ids["adultos"] = params.garantir_unidade(con, "Adultos")
            ids["infantil"] = params.garantir_unidade(con, "Infantil")
            ids["ana"] = params.garantir_profissional(con, "Ana Souza", email="ana@clinica.test")
            ids["bruno"] = params.garantir_profissional(con, "Bruno Lima")
            ids["pac_a"] = params.garantir_paciente(
                con,
                "Paciente A",
                unidade_id=ids["adultos"],
                profissional_id=ids["ana"],
                valor_sessao_centavos=27500,
            )
            ids["pac_b"] = params.garantir_paciente(
                con,
                "Paciente B",
                unidade_id=ids["infantil"],
                profissional_id=ids["bruno"],
                valor_sessao_centavos=33000,
            )
    finally:
        con.close()
    params.materializar_meio(
        path, {"nome": "Sicoob", "tipo": "banco", "vigente_de": "2025-01-01"}, criado_por="t"
    )
    params.materializar_meio(
        path, {"nome": "Stone", "tipo": "maquininha", "vigente_de": "2025-01-01"}, criado_por="t"
    )
    params.materializar_taxa(
        path,
        {"meio": "Stone", "forma": "debito", "taxa": "1,5", "vigente_de": "2025-01-01"},
        criado_por="t",
    )
    params.materializar_taxa(
        path,
        {"meio": "Stone", "forma": "credito", "taxa": "3,5", "vigente_de": "2025-01-01"},
        criado_por="t",
    )
    params.materializar_comissao(
        path,
        {
            "profissional": "Ana Souza",
            "tipo": "percentual",
            "valor": "40",
            "base": "liquido",
            "vigente_de": "2025-01-01",
        },
        criado_por="t",
    )
    params.materializar_comissao(
        path,
        {
            "profissional": "Bruno Lima",
            "tipo": "percentual",
            "valor": "50",
            "base": "bruto",
            "vigente_de": "2025-01-01",
        },
        criado_por="t",
    )
    return ids


def ctx(
    raiz: Path, role: str = "admin", actor: str = "gestor@clinica.test", key: str | None = None
) -> HandlerContext:
    return HandlerContext(
        client_id=CLIENTE,
        actor=actor,
        role=role,
        trace_id="t-1",
        data_root=raiz,
        idempotency_key=key,
    )


def recebimento(**kw: Any) -> dict[str, Any]:
    base = {
        "data_pagamento": "2026-02-02",
        "paciente": "Paciente A",
        "forma": "pix",
        "meio": "Sicoob",
        "valor": "275",
    }
    base.update(kw)
    return base
