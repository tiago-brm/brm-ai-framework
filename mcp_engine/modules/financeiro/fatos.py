"""Provedores de fatos do financeiro para as regras e gatilhos (spec 031, parte A1).

Somente leitura (conexão `mode=ro`), sem efeito colateral. O handler recalcula os mesmos sinais
ao gravar, então a regra decide e o banco continua sendo a rede de proteção.
"""

from __future__ import annotations

import calendar
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from mcp_engine.core.fatos import ContextoFatos
from mcp_engine.modules.financeiro import handlers as h
from mcp_engine.modules.financeiro import integridade, lancamentos, params, sessoes
from mcp_engine.modules.financeiro.db import ErroFinanceiro, caminho_ledger, mes_de, normalizar_nome

FATOS_RECEBIMENTO = (
    "valor_atipico",
    "retroativo_dias",
    "mes_fechado",
    "duplicidade_suspeita",
    "paciente_a_confirmar",
)
FATOS_DESPESA = ("retroativo_dias", "mes_fechado", "duplicidade_suspeita")
FATOS_SITUACAO = (
    "qtd_sem_nf",
    "pendencias_abertas",
    "sessoes_sem_desfecho",
    "dias_para_fim_do_mes",
    "mes_anterior_aberto",
)


def agora() -> datetime:
    return datetime.now(UTC)


def _abrir_leitura(caminho: Path) -> sqlite3.Connection | None:
    if not caminho.is_file():
        return None
    con = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True, timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def fatos_do_recebimento(ctx: ContextoFatos, args: Mapping[str, Any]) -> dict[str, Any]:
    conexao = _abrir_leitura(caminho_ledger(ctx.data_root, ctx.client_id))
    if conexao is None:
        return {}
    with closing(conexao) as con:
        try:
            nome = h._texto(args, "paciente", obrigatorio=True, maximo=160)
            paciente = h._paciente(con, nome)
            if paciente is None:
                return {}
            forma = h._texto(args, "forma", obrigatorio=True, maximo=30)
            unidade = h._texto(args, "unidade", maximo=80)
            tipo = h._texto(args, "tipo", maximo=10) or (
                "acordo" if normalizar_nome(forma) == "acordo" else "sessao"
            )
            dados = {
                "data_pagamento": h.parse_data(h._texto(args, "data_pagamento", obrigatorio=True)),
                "paciente_id": paciente["id"],
                "forma_id": params.id_forma(con, forma),
                "valor": h._dinheiro(args, "valor"),
                "tipo": tipo,
                "datas_sessoes": h._datas(args, "datas_sessoes"),
                "unidade_id": (
                    h._id_por_nome(con, "unidade", unidade, "Unidade")
                    if unidade
                    else paciente["unidade_id"]
                ),
            }
            return lancamentos.fatos_recebimento(con, dados, h.hoje())
        except ErroFinanceiro:
            return {}


def fatos_da_despesa(ctx: ContextoFatos, args: Mapping[str, Any]) -> dict[str, Any]:
    conexao = _abrir_leitura(caminho_ledger(ctx.data_root, ctx.client_id))
    if conexao is None:
        return {}
    with closing(conexao) as con:
        try:
            categoria = h._texto(args, "categoria", obrigatorio=True, maximo=80)
            grupo = h._texto(args, "grupo", maximo=10)
            linhas = con.execute(
                "SELECT id FROM categoria_despesa WHERE nome = ? COLLATE NOCASE"
                " AND (? IS NULL OR grupo = ?)",
                (categoria, grupo, grupo),
            ).fetchall()
            if len(linhas) != 1:
                return {}
            forma = h._texto(args, "forma", maximo=30)
            dados = {
                "data": h.parse_data(h._texto(args, "data", obrigatorio=True)),
                "descricao": h._texto(args, "descricao", obrigatorio=True),
                "categoria_id": linhas[0]["id"],
                "forma_id": params.id_forma(con, forma) if forma else None,
                "valor": h._dinheiro(args, "valor"),
            }
            return lancamentos.fatos_despesa(con, dados, h.hoje())
        except ErroFinanceiro:
            return {}


def fatos_da_situacao(ctx: ContextoFatos, args: Mapping[str, Any]) -> dict[str, Any]:
    conexao = _abrir_leitura(caminho_ledger(ctx.data_root, ctx.client_id))
    if conexao is None:
        return {}
    with closing(conexao) as con:
        hoje = h.hoje()
        anterior = mes_de(hoje.replace(day=1) - timedelta(days=1))
        fechado = con.execute(
            "SELECT 1 FROM fechamento_vigente WHERE mes = ? AND status = 'fechado' LIMIT 1",
            (anterior,),
        ).fetchone()
        return {
            "qtd_sem_nf": con.execute(
                "SELECT COUNT(*) FROM recebimento"
                " WHERE tipo <> 'estorno' AND nf_status <> 'emitida'"
            ).fetchone()[0],
            "pendencias_abertas": con.execute(
                "SELECT COUNT(*) FROM pendencia_operacional WHERE status = 'aberta'"
            ).fetchone()[0],
            "sessoes_sem_desfecho": sessoes.contar_sessoes_sem_desfecho(con, agora()),
            "dias_para_fim_do_mes": calendar.monthrange(hoje.year, hoje.month)[1] - hoje.day,
            "mes_anterior_aberto": fechado is None,
        }


def verificar_integridade_ledger(data_root: Path, client_id: str) -> dict[str, Any]:
    """Verificação para o /health: só códigos de invariante, nunca dados do ledger."""
    conexao = _abrir_leitura(caminho_ledger(data_root, client_id))
    if conexao is None:
        return {"ok": False, "detalhe": "ledger ausente"}
    with closing(conexao) as con:
        violacoes = integridade.verificar(con)
    return {
        "ok": not violacoes,
        "violacoes": len(violacoes),
        "invariantes": sorted({v["invariante"] for v in violacoes}),
    }
