"""Relatórios determinísticos do ledger para o caderno (spec 025).

O relatório não recebe nem interpreta texto de usuário: período e unidade já
foram validados pelo núcleo. Os totais reutilizam as consultas nomeadas do
ledger, para que a nota e a ferramenta ``consultar_financeiro`` tenham a mesma
fonte de verdade.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from mcp_engine.modules.financeiro import consultas
from mcp_engine.modules.financeiro.db import caminho_ledger
from mcp_engine.modules.financeiro.lancamentos import Quem
from mcp_engine.modules.financeiro.money import formatar_brl
from mcp_engine.vault.segundo_cerebro import Gerador

NOME_GERADOR = "financeiro-mensal"

_TEMPLATE = """# Resumo financeiro — $periodo

Unidade: $unidade

## Caixa

- Entradas: $entradas
- Saídas: $saidas
- Resultado: $resultado
- Lançamentos: $lancamentos

## Distribuição

- Taxas: $taxas
- Repasses: $repasses
- Parte da clínica: $parte_clinica

## Atenção

- Recebimentos sem NF: $sem_nf
- Pendências operacionais abertas: $pendencias

_Relatório determinístico gerado a partir do ledger._
"""


def _conexao_leitura(caminho: Path) -> sqlite3.Connection:
    """Abre o ledger em modo somente leitura; gerar uma nota nunca muda o caixa."""
    con = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True, timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def dados_mensais(
    data_root: Path, client_id: str, periodo: str, unidade: str | None
) -> dict[str, Any]:
    """Dados já formatados para o template, calculados pelas consultas oficiais."""
    caminho = caminho_ledger(data_root, client_id)
    with _conexao_leitura(caminho) as con:
        de, ate = consultas.parse_periodo(periodo, date.today())
        ctx = consultas.Ctx(
            con=con,
            quem=Quem(actor="sistema:relatorio", role="admin"),
            hoje=date.today(),
            de=de,
            ate=ate,
            filtros={"unidade": unidade} if unidade else {},
        )
        resumo = consultas.resumo_mensal(ctx)["dados"]
        sem_nf = consultas.sem_nf(ctx)["dados"]["por_status"]
        pendencias = consultas.pendencias(ctx)["dados"]["resumo"]

    return {
        "periodo": periodo,
        "unidade": unidade or "todas",
        "entradas": formatar_brl(resumo["entradas_centavos"]),
        "saidas": formatar_brl(resumo["saidas_centavos"]),
        "resultado": formatar_brl(resumo["resultado_centavos"]),
        "lancamentos": resumo["lancamentos"],
        "taxas": formatar_brl(resumo["taxas_centavos"]),
        "repasses": formatar_brl(resumo["repasse_centavos"]),
        "parte_clinica": formatar_brl(resumo["parte_clinica_centavos"]),
        "sem_nf": sum(linha["n"] for linha in sem_nf),
        "pendencias": sum(linha["n"] for linha in pendencias if linha["status"] == "aberta"),
    }


def criar_gerador(data_root: Path, client_id: str) -> Gerador:
    return Gerador(
        nome=NOME_GERADOR,
        descricao="Resumo mensal do caixa, repasses, NFs e pendências do ledger.",
        dados=lambda periodo, unidade: dados_mensais(data_root, client_id, periodo, unidade),
        template=_TEMPLATE,
    )
