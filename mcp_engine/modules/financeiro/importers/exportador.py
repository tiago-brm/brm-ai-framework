"""Exportação para .xlsx no layout das planilhas atuais (spec 024).

Os totais são calculados pelo motor e gravados como valores (não como fórmulas), para que
qualquer leitor, inclusive o importador, veja os mesmos números.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import Font

from mcp_engine.modules.financeiro.db import ErroFinanceiro, parse_mes
from mcp_engine.modules.financeiro.importers.mapeamento import Mapeamento
from mcp_engine.modules.financeiro.importers.sessoes_parser import formatar
from mcp_engine.modules.financeiro.lancamentos import Quem

MESES = (
    "JANEIRO FEVEREIRO MARÇO ABRIL MAIO JUNHO JULHO AGOSTO SETEMBRO OUTUBRO NOVEMBRO DEZEMBRO"
).split()
BLOCOS = (
    ("variada", "GASTOS VARIADOS"),
    ("fixa", "DESPESAS FIXAS"),
    ("imposto", "IMPOSTOS"),
    ("folha", "FOLHA DE PAGAMENTO"),
)
CABECALHO = [
    "DATA",
    "PACIENTE",
    "FORMA PGMT",
    "DATA SESSÕES",
    "VALOR",
    "NF",
    "PSICOLOGO(A)",
    "NOME COMPROVANTE",
]


def _reais(centavos: int) -> float:
    return centavos / 100


def _limites(mes: str) -> tuple[str, str]:
    return f"{mes}-01", f"{mes}-31"


def _mapeamento(client_id: str | None) -> Mapeamento:
    if client_id:
        raiz = Path(os.getenv("CONFIG_ROOT", "clients"))
        return Mapeamento.carregar(raiz / client_id / "config" / "importacao.yaml")
    return Mapeamento()


def _slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", texto.lower()).strip("-") or "todas"


def exportar_secretarias(
    con: sqlite3.Connection, quem: Quem, mes: str, unidade: str, destino: Path, mapa: Mapeamento
) -> dict[str, Any]:
    uni = con.execute(
        "SELECT id, nome FROM unidade WHERE nome = ? COLLATE NOCASE", (unidade,)
    ).fetchone()
    if uni is None:
        raise ErroFinanceiro(f"Unidade desconhecida: {unidade!r}.")
    de, ate = _limites(mes)
    rows = con.execute(
        "SELECT r.*, pa.nome AS paciente_nome, f.nome AS forma_nome, m.nome AS meio_nome,"
        " pr.nome AS prof_nome FROM recebimento r JOIN paciente_ref pa ON pa.id = r.paciente_id"
        " JOIN forma_pagamento f ON f.id = r.forma_id"
        " LEFT JOIN meio_recebimento m ON m.id = r.meio_id"
        " LEFT JOIN profissional pr ON pr.id = r.profissional_id"
        " WHERE r.unidade_id = ? AND r.data_pagamento BETWEEN ? AND ? ORDER BY"
        " r.data_pagamento, r.id",
        (uni["id"], de, ate),
    ).fetchall()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Planilha1"
    ws["A1"] = "EXPORTAÇÃO"
    ws["C1"] = f"{MESES[int(mes[5:]) - 1]} {mes[:4]} - {uni['nome'].upper()}"
    ws["A3"] = "PLANILHA PAGAMENTOS"
    for i, titulo in enumerate(CABECALHO, start=1):
        ws.cell(row=4, column=i, value=titulo).font = Font(bold=True)
    total = 0
    for n, r in enumerate(rows, start=5):
        d = date.fromisoformat(r["data_pagamento"])
        sess = [
            date.fromisoformat(x["data_sessao"])
            for x in con.execute(
                "SELECT data_sessao FROM recebimento_sessao WHERE recebimento_id = ? ORDER BY"
                " data_sessao",
                (r["id"],),
            )
        ]
        rotulo = (
            mapa.rotulo_de(r["forma_nome"], r["meio_nome"], d)
            or " ".join(x for x in (r["forma_nome"], r["meio_nome"]) if x).upper()
        )
        ws.cell(row=n, column=1, value=datetime(d.year, d.month, d.day))
        ws.cell(
            row=n, column=2, value=r["paciente_nome"] if quem.gestor else f"PAC-{r['paciente_id']}"
        )
        ws.cell(row=n, column=3, value=rotulo)
        if len(sess) == 1:
            ws.cell(row=n, column=4, value=datetime(sess[0].year, sess[0].month, sess[0].day))
        elif sess:
            ws.cell(row=n, column=4, value=formatar(tuple(sess)))
        ws.cell(row=n, column=5, value=_reais(r["valor_centavos"]))
        if r["nf_status"] == "emitida" and r["nf_numero"]:
            nf = r["nf_numero"]
            ws.cell(row=n, column=6, value=int(nf) if nf.isdigit() else nf)
        elif r["nf_status"] == "sem_nf":
            ws.cell(row=n, column=6, value="S/NF")
        ws.cell(row=n, column=7, value=r["prof_nome"])
        ws.cell(row=n, column=8, value=r["comprovante_ref"])
        if r["estorno_de"]:
            ws.cell(row=n, column=9, value=f"ESTORNO DO LANÇAMENTO {r['estorno_de']}")
        total += r["valor_centavos"]
    return _salvar(wb, destino, "secretarias", mes, uni["nome"], len(rows), total)


def exportar_financeiro(
    con: sqlite3.Connection, mes: str, destino: Path, mapa: Mapeamento
) -> dict[str, Any]:
    de, ate = _limites(mes)
    titulo = f"{MESES[int(mes[5:]) - 1]} DE {mes[:4]}"
    wb = openpyxl.Workbook()
    ent = wb.active
    ent.title = "ENTRADAS"
    ent["A1"] = "DEMONSTRATIVO DE RESULTADO DO EXERCÍCIO - "
    ent["A2"] = "ENTRADAS"
    ent["A3"] = titulo
    for i, t in enumerate(("FORMA", "UNIDADE", "VALOR"), start=1):
        ent.cell(row=4, column=i, value=t).font = Font(bold=True)
    linhas = con.execute(
        "SELECT f.nome forma, COALESCE(u.nome, 'sem unidade') unidade, SUM(r.valor_centavos) total"
        " FROM recebimento r JOIN forma_pagamento f ON f.id = r.forma_id"
        " LEFT JOIN unidade u ON u.id = r.unidade_id WHERE r.data_pagamento BETWEEN ? AND ?"
        " GROUP BY f.id, u.id ORDER BY f.nome, u.nome",
        (de, ate),
    ).fetchall()
    total_ent = 0
    for n, r in enumerate(linhas, start=5):
        ent.cell(row=n, column=1, value=r["forma"])
        ent.cell(row=n, column=2, value=r["unidade"])
        ent.cell(row=n, column=3, value=_reais(r["total"]))
        total_ent += r["total"]
    ent.cell(row=len(linhas) + 6, column=2, value="TOTAL ENTRADAS")
    ent.cell(row=len(linhas) + 6, column=3, value=_reais(total_ent))

    sai = wb.create_sheet("SAÍDAS")
    sai.merge_cells("A1:D1")
    sai["A1"] = "DEMONSTRATIVO DE RESULTADO DO EXERCÍCIO - "
    sai["A2"] = "SAÍDAS"
    sai["A3"] = titulo
    for i, t in enumerate(("DATA", "DESCRIÇÃO", "FORMA DE PGMT", "VALOR"), start=1):
        sai.cell(row=4, column=i, value=t).font = Font(bold=True)
    linha = 5
    total_sai = 0
    for grupo, rotulo_bloco in BLOCOS:
        sai.cell(row=linha, column=1, value=rotulo_bloco).font = Font(bold=True)
        linha += 1
        sub = 0
        for r in con.execute(
            "SELECT d.*, f.nome forma FROM despesa d JOIN categoria_despesa c ON c.id ="
            " d.categoria_id"
            " LEFT JOIN forma_pagamento f ON f.id = d.forma_id"
            " WHERE c.grupo = ? AND d.data BETWEEN ? AND ? ORDER BY d.data, d.id",
            (grupo, de, ate),
        ):
            d = date.fromisoformat(r["data"])
            sai.cell(row=linha, column=1, value=datetime(d.year, d.month, d.day))
            sai.cell(row=linha, column=2, value=r["descricao"])
            forma = mapa.rotulo_de(r["forma"], None, d, despesa=True) or (
                "HOLERITE" if grupo == "folha" else (r["forma"] or "").upper()
            )
            sai.cell(row=linha, column=3, value=forma)
            sai.cell(row=linha, column=4, value=_reais(r["valor_centavos"]))
            sub += r["valor_centavos"]
            linha += 1
        sai.cell(row=linha, column=2, value="SUBTOTAL")
        sai.cell(row=linha, column=4, value=_reais(sub))
        total_sai += sub
        linha += 2
    sai.cell(row=linha, column=2, value="TOTAL ENTRADAS")
    sai.cell(row=linha, column=4, value=_reais(total_ent))
    sai.cell(row=linha + 1, column=2, value="TOTAL SAÍDAS")
    sai.cell(row=linha + 1, column=4, value=_reais(total_sai))
    sai.cell(row=linha + 2, column=3, value="RESULTADO")
    sai.cell(row=linha + 2, column=4, value=_reais(total_ent - total_sai))
    return _salvar(
        wb,
        destino,
        "financeiro",
        mes,
        None,
        len(linhas),
        total_ent,
        extra={"saidas_centavos": total_sai, "resultado_centavos": total_ent - total_sai},
    )


def _salvar(
    wb: openpyxl.Workbook,
    destino: Path,
    tipo: str,
    mes: str,
    unidade: str | None,
    linhas: int,
    total: int,
    extra: dict[str, int] | None = None,
) -> dict[str, Any]:
    destino.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
    caminho = destino / f"{tipo}_{mes}_{_slug(unidade or 'geral')}_{carimbo}.xlsx"
    wb.save(caminho)
    return {
        "exportado": True,
        "arquivo": caminho.name,
        "caminho": str(caminho),
        "tipo": tipo,
        "mes": mes,
        "linhas": linhas,
        "total_entradas_centavos": total,
        **(extra or {}),
    }


def exportar(
    con: sqlite3.Connection,
    quem: Quem,
    *,
    tipo: str,
    periodo: str,
    unidade: str | None,
    destino: Path,
    client_id: str | None = None,
) -> dict[str, Any]:
    mes = parse_mes(periodo)
    mapa = _mapeamento(client_id)
    if tipo == "secretarias":
        if not unidade:
            raise ErroFinanceiro("O layout secretarias exige a unidade.")
        return exportar_secretarias(con, quem, mes, unidade, destino, mapa)
    if tipo == "financeiro":
        return exportar_financeiro(con, mes, destino, mapa)
    raise ErroFinanceiro("Tipo deve ser 'secretarias' ou 'financeiro'.")


__all__ = ["exportar"]
