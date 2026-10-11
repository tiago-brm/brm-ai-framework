"""Planilhas SINTÉTICAS no layout das planilhas da clínica (nomes e valores genéricos)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl

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


def d(m: int, dia: int, ano: int = 2026) -> datetime:
    return datetime(ano, m, dia)


LINHAS_SECRETARIAS: list[tuple[Any, ...]] = [
    (d(2, 2), "Paciente 01", "DEB", "02,23/2,02/03,09/03", 1260, 8462, "Ana Souza", None, None),
    (d(2, 2), "Paciente 02", "PIX STONE", d(2, 2), 275, 8459, "Ana Souza", None, None),
    (d(2, 2), "Paciente 03", "DEB", d(2, 2), 275, "S/NF", "Bruno Lima", None, None),
    (d(2, 2), "Paciente 04", "PIX SICOOB", d(2, 2), 200, 8460, "Ana Souza", "pix_0202.pdf", None),
    (d(2, 3), "Paciente 05", "PIX SICOOB", d(1, 27), 230, "S/NF", "Bruno Lima", None, None),
    (
        d(2, 3),
        "Paciente 06",
        "PIX SICOOB",
        "03/12/2025,21,28/01/2026.+ JUROS DE ATRASO",
        775,
        8463,
        "Ana Souza",
        None,
        None,
    ),
    (d(2, 3), "Paciente 07", "PIX SICOOB", "03,10,17,24/02", 900, 8468, "Bruno Lima", None, None),
    (d(2, 4), "Paciente 08", "TED SICOOB", d(2, 3), 500, 8467, "Ana Souza", None, None),
    (d(2, 4), "Paciente 01", "DINH", d(2, 4), 250, 8466, "Ana Souza", None, None),
    (d(2, 4), "Paciente 02", "DEB", d(4, 4), 275, 8464, "Ana Souza", None, None),
    (d(2, 5), "Paciente 03", "PIX SICREDI", d(2, 5), 350, "S/NF", "Bruno Lima", None, None),
    (d(2, 5), "Paciente 04", "CRED", "03,10/02", 519.57, 8475, "Ana Souza", None, None),
    (d(2, 5), "Paciente 05", "PIX SICOOB", d(1, 29), 220, 8475, "Bruno Lima", None, None),
    (d(2, 6), "Paciente 06", "PIX SICOOB", d(2, 6), 280.5, 8493, "Ana Souza", None, None),
    (
        d(2, 9),
        "Paciente 07",
        "PIX SICOOB",
        "26,28/01,25/02,04/02",
        1188,
        "S/NF",
        "Bruno Lima",
        None,
        None,
    ),
    (d(2, 10), "Paciente 08", "ACORDO", d(2, 10), 220, "S/NF", "Ana Souza", None, None),
    (
        d(2, 26),
        "Paciente 01",
        "PIX INTER",
        d(2, 26),
        220,
        8700,
        "Ana Souza",
        None,
        "ESTORNADO PARA A PACIENTE POR DESISTENCIA 26/02/2026",
    ),
    (d(2, 26), "Paciente 02", "BOLETO", d(2, 26), 100, None, "Ana Souza", None, None),
    (d(2, 27), "Paciente 09", "PIX STONE", d(2, 27), 275, "S/NF", "Carla Nova", None, None),
    (d(2, 28), "Paciente 10", "PIX SICOOB", "06,13,20,27/02", 704, 8680, "Ana Souza", None, None),
    (d(2, 28), "Paciente 11", "PIX SICOOB", d(2, 28), -50, None, "Ana Souza", None, None),
]

LINHAS_FINANCEIRO: list[tuple[str, Any, str, str, Any]] = [
    ("variada", d(2, 3), "Material de escritório", "PIX", 150.75),
    ("variada", d(2, 10), "Café e copa", "DINH", 80),
    ("fixa", d(2, 5), "ALUGUEL", "PIX", 4500),
    ("fixa", d(2, 7), "ENERGIA", "PIX", 612.3),
    ("fixa", d(2, 8), "INTERNET", "PIX", 199.9),
    ("imposto", d(2, 20), "SIMPLES NACIONAL", "PIX", 1320.55),
    ("folha", None, "", "HOLERITE", 3500),
    ("folha", None, "", "HOLERITE", 2800),
]


def criar_secretarias(
    caminho: Path,
    linhas: list[tuple[Any, ...]] | None = None,
    titulo: str = "FEVEREIRO 2026 - ADULTOS",
    secretaria: str = "SECRETARIA TESTE",
) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Planilha1"
    ws["A1"] = secretaria
    ws["C1"] = titulo
    ws["A3"] = "PLANILHA PAGAMENTOS"
    for i, t in enumerate(CABECALHO, start=1):
        ws.cell(row=4, column=i, value=t)
    for n, linha in enumerate(LINHAS_SECRETARIAS if linhas is None else linhas, start=5):
        for i, v in enumerate(linha, start=1):
            if v is not None:
                ws.cell(row=n, column=i, value=v)
    wb.save(caminho)
    return caminho


def criar_financeiro(
    caminho: Path,
    linhas: list[tuple[str, Any, str, str, Any]] | None = None,
    titulo: str = "FEVEREIRO DE 2026",
) -> Path:
    from mcp_engine.modules.financeiro.importers.exportador import BLOCOS

    wb = openpyxl.Workbook()
    wb.active.title = "ENTRADAS"
    ws = wb.create_sheet("SAÍDAS ")
    ws["A1"] = "DEMONSTRATIVO DE RESULTADO DO EXERCÍCIO - "
    ws["A2"] = "SAÍDAS"
    ws["A3"] = titulo
    for i, t in enumerate(("DATA", "DESCRIÇÃO", "FORMA DE PGMT", "VALOR"), start=1):
        ws.cell(row=4, column=i, value=t)
    dados = LINHAS_FINANCEIRO if linhas is None else linhas
    n = 5
    for grupo, rotulo in BLOCOS:
        ws.cell(row=n, column=1, value=rotulo)
        n += 1
        subtotal = 0.0
        for g, data, desc, forma, valor in dados:
            if g != grupo:
                continue
            ws.cell(row=n, column=1, value=data)
            ws.cell(row=n, column=2, value=desc or None)
            ws.cell(row=n, column=3, value=forma)
            ws.cell(row=n, column=4, value=valor)
            subtotal += float(valor)
            n += 1
        ws.cell(row=n, column=4, value=round(subtotal, 2))
        n += 2
    ws.cell(row=n, column=2, value="TOTAL ENTRADAS")
    ws.cell(row=n + 1, column=2, value="TOTAL SAÍDAS")
    ws.cell(row=n + 2, column=3, value="RESULTADO")
    wb.save(caminho)
    return caminho


if __name__ == "__main__":
    destino = Path(__file__).parent
    criar_secretarias(destino / "planilha_secretarias_sintetica.xlsx")
    criar_financeiro(destino / "relatorio_financeiro_sintetico.xlsx")
    print("fixtures sintéticas geradas em", destino)
