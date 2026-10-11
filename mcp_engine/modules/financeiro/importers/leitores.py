"""Leitura das planilhas (spec 024): só valores, sem macros, `.xlsm` recusado."""

from __future__ import annotations

import hashlib
import re
import unicodedata
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import openpyxl

from mcp_engine.modules.financeiro.db import ErroFinanceiro

TAMANHO_MAX = 20 * 1024 * 1024
LINHAS_MAX = 200_000
MESES = {
    n: i
    for i, n in enumerate(
        (
            "janeiro fevereiro marco abril maio junho julho agosto setembro outubro novembro"
            " dezembro"
        ).split(),
        start=1,
    )
}
GRUPOS = {
    "GASTOS VARIADOS": "variada",
    "DESPESAS FIXAS": "fixa",
    "IMPOSTOS": "imposto",
    "FOLHA DE PAGAMENTO": "folha",
}
COLUNAS = {
    "DATA": "data",
    "PACIENTE": "paciente",
    "FORMA PGMT": "forma",
    "DATA SESSOES": "sessoes",
    "VALOR": "valor",
    "NF": "nf",
    "PSICOLOGO(A)": "profissional",
    "NOME COMPROVANTE": "comprovante",
}


@dataclass
class LinhaRecebimento:
    aba: str
    linha: int
    data: Any
    paciente: str
    forma: str
    sessoes: Any
    valor: Any
    nf: Any
    profissional: str
    comprovante: str
    observacao: str


@dataclass
class LinhaDespesa:
    aba: str
    linha: int
    grupo: str
    data: Any
    descricao: str
    forma: str
    valor: Any


@dataclass
class Planilha:
    layout: str
    arquivo: str
    hash: str
    mes: str | None = None
    unidade: str | None = None
    secretaria: str | None = None
    recebimentos: list[LinhaRecebimento] = field(default_factory=list)
    despesas: list[LinhaDespesa] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


def hash_arquivo(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _sem_acento_maiusculo(texto: object) -> str:
    base = unicodedata.normalize("NFKD", str(texto or ""))
    base = "".join(c for c in base if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", base.upper()).strip()


def valor_em_centavos(celula: object) -> int | None:
    """Número da planilha em centavos; None se ausente. Levanta se ambíguo ou inválido."""
    if celula is None or celula == "":
        return None
    if isinstance(celula, bool):
        raise ErroFinanceiro("valor lógico na coluna de valor")
    if isinstance(celula, int | float):
        d = Decimal(repr(celula))
    elif isinstance(celula, str):
        from mcp_engine.modules.financeiro.money import ValorInvalido, parse_brl

        try:
            return parse_brl(celula)
        except ValorInvalido as exc:
            raise ErroFinanceiro(str(exc)) from None
    else:
        raise ErroFinanceiro("tipo de célula de valor não suportado")
    try:
        centavos = d * 100
        if centavos != centavos.to_integral_value():
            raise ErroFinanceiro("valor com mais de duas casas decimais")
    except InvalidOperation:
        raise ErroFinanceiro("valor inválido") from None
    return int(centavos)


def abrir_planilha(path: Path) -> openpyxl.Workbook:
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        raise ErroFinanceiro(
            "Só arquivos .xlsx são aceitos (.xlsm e demais formatos são recusados)."
        )
    if path.stat().st_size > TAMANHO_MAX:
        raise ErroFinanceiro("Arquivo acima do limite de 20 MB.")
    try:
        with zipfile.ZipFile(path) as z:
            nomes = [n.lower() for n in z.namelist()]
            if any("vbaproject" in n for n in nomes):
                raise ErroFinanceiro("Planilha com macro (VBA) recusada.")
            if sum(i.file_size for i in z.infolist()) > 20 * TAMANHO_MAX:
                raise ErroFinanceiro("Arquivo descompactado acima do limite.")
    except zipfile.BadZipFile:
        raise ErroFinanceiro("Arquivo não é uma planilha .xlsx válida.") from None
    return openpyxl.load_workbook(path, read_only=True, data_only=True, keep_vba=False)


def _linhas(ws: Any) -> Iterator[tuple[int, tuple[Any, ...]]]:
    for n, row in enumerate(ws.iter_rows(values_only=True), start=1):
        if n > LINHAS_MAX:
            raise ErroFinanceiro("Planilha acima do limite de linhas.")
        yield n, row


def _texto(v: Any) -> str:
    return "" if v is None else str(v).strip()


def detectar_layout(wb: openpyxl.Workbook) -> str:
    for ws in wb.worksheets:
        for n, row in _linhas(ws):
            if n > 30:
                break
            rot = {_sem_acento_maiusculo(c) for c in row if c is not None}
            if {"DATA", "PACIENTE", "VALOR"} <= rot:
                return "secretarias"
    if any(_sem_acento_maiusculo(ws.title).startswith("SAIDAS") for ws in wb.worksheets):
        return "financeiro"
    raise ErroFinanceiro("Layout de planilha não reconhecido (secretarias ou financeiro).")


_CABECALHO = re.compile(r"([A-Za-zÀ-ú]+)\s+(\d{4})\s*[-–]\s*([A-Za-zÀ-ú ]+)")


def ler_secretarias(wb: openpyxl.Workbook, arquivo: str, hash_: str) -> Planilha:
    plan = Planilha(layout="secretarias", arquivo=arquivo, hash=hash_)
    for ws in wb.worksheets:
        linhas = list(_linhas(ws))
        cab = next(
            (
                n
                for n, row in linhas[:40]
                if {"DATA", "PACIENTE", "VALOR"} <= {_sem_acento_maiusculo(c) for c in row if c}
            ),
            None,
        )
        if cab is None:
            continue
        mapa: dict[str, int] = {}
        for i, c in enumerate(dict(linhas)[cab]):
            nome = COLUNAS.get(_sem_acento_maiusculo(c))
            if nome and nome not in mapa:
                mapa[nome] = i
        ultima = max(mapa.values())
        for n, row in linhas:
            if n >= cab:
                break
            for i, c in enumerate(row):
                if isinstance(c, str):
                    m = _CABECALHO.search(c)
                    mes = MESES.get(_sem_acento_maiusculo(m.group(1)).lower()) if m else None
                    if m and mes and plan.mes is None:
                        plan.mes = f"{m.group(2)}-{mes:02d}"
                        unidade = re.sub(r"[^A-Za-zÀ-ú ]", "", m.group(3)).strip()
                        plan.unidade = unidade.title()
                        antes = [_texto(x) for x in row[:i] if x]
                        if antes:
                            plan.secretaria = antes[0]
        if plan.secretaria is None:
            primeira = next((_texto(r[0]) for n, r in linhas[:2] if r and r[0]), "")
            plan.secretaria = primeira or None
        for n, row in linhas:
            if n <= cab:
                continue
            valores = [
                row[mapa[k]] if mapa.get(k, 10**6) < len(row) else None for k in COLUNAS.values()
            ]
            extras = [_texto(c) for c in row[ultima + 1 :] if c not in (None, "")]
            if all(v in (None, "") for v in valores) and not extras:
                continue
            reg = dict(zip(COLUNAS.values(), valores, strict=True))
            plan.recebimentos.append(
                LinhaRecebimento(
                    aba=ws.title.strip(),
                    linha=n,
                    data=reg["data"],
                    paciente=_texto(reg["paciente"]),
                    forma=_texto(reg["forma"]),
                    sessoes=reg["sessoes"],
                    valor=reg["valor"],
                    nf=reg["nf"],
                    profissional=_texto(reg["profissional"]),
                    comprovante=_texto(reg["comprovante"]),
                    observacao=" | ".join(extras),
                )
            )
    if plan.mes is None:
        plan.avisos.append("Cabeçalho com mês, ano e unidade não encontrado.")
    return plan


def ler_financeiro(
    wb: openpyxl.Workbook, arquivo: str, hash_: str, mes_override: str | None = None
) -> Planilha:
    plan = Planilha(layout="financeiro", arquivo=arquivo, hash=hash_)
    for ws in wb.worksheets:
        if not _sem_acento_maiusculo(ws.title).startswith("SAIDAS"):
            continue
        grupo: str | None = None
        for n, row in _linhas(ws):
            a, b, c, d = (list(row) + [None] * 4)[:4]
            if isinstance(a, str):
                m = re.search(r"([A-Za-zÀ-ú]+)\s+DE\s+(\d{4})", a, re.IGNORECASE)
                mes = MESES.get(_sem_acento_maiusculo(m.group(1)).lower()) if m else None
                if m and mes and plan.mes is None:
                    plan.mes = f"{m.group(2)}-{mes:02d}"
                rotulo = _sem_acento_maiusculo(a).strip()
                achado = next(
                    (g for k, g in GRUPOS.items() if _sem_acento_maiusculo(k) == rotulo), None
                )
                if achado:
                    grupo = achado
                    continue
            rot_b = _sem_acento_maiusculo(b)
            if rot_b.startswith("TOTAL") or _sem_acento_maiusculo(c) == "RESULTADO":
                grupo = None
                continue
            if grupo is None or isinstance(d, str) and d.startswith("="):
                continue
            if rot_b.startswith("SUBTOTAL") or not (a or b or c) or d in (None, ""):
                continue
            plan.despesas.append(
                LinhaDespesa(
                    aba=ws.title.strip(),
                    linha=n,
                    grupo=grupo,
                    data=a,
                    descricao=_texto(b),
                    forma=_texto(c),
                    valor=d,
                )
            )
    if mes_override:
        plan.mes = mes_override
    if plan.mes is None:
        plan.avisos.append("Mês não encontrado na planilha (informe --mes AAAA-MM).")
    return plan


def ler(path: Path, *, mes: str | None = None, layout: str | None = None) -> Planilha:
    path = Path(path)
    wb = abrir_planilha(path)
    try:
        detectado = layout or detectar_layout(wb)
        h = hash_arquivo(path)
        if detectado == "secretarias":
            return ler_secretarias(wb, path.name, h)
        if detectado == "financeiro":
            return ler_financeiro(wb, path.name, h, mes)
        raise ErroFinanceiro(f"Layout desconhecido: {detectado!r}.")
    finally:
        wb.close()


def eh_data(v: Any) -> bool:
    return isinstance(v, datetime | date)
