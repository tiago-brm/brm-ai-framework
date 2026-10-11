"""Parser da coluna "data sessões" das planilhas (spec 024).

Gramática (EBNF), sem distinção de maiúsculas:

    celula  = datas [ sufixo ] ;
    sufixo  = { " " | "." } "+" { " " } "JUROS" [ { " " } "DE" { " " } "ATRASO" ] ;
    datas   = item { "," item } ;
    item    = DIA [ "/" MES [ "/" ANO ] ] ;

Um item sem mês herda o mês (e o ano) do próximo item à direita que os tenha
("03,10,17,24/02" = quatro datas de fevereiro; "03/12/2025,21,28/01/2026" = 03/12/2025 e
21 e 28 de 01/2026). Item `DD/MM` sem ano recebe o ano que deixa a data mais perto do
pagamento (vira o ano em dezembro e janeiro). Células de data entram como data única.

Confiança (1.0 a 0.0, vale o menor fator): mês herdado do pagamento 0.7; data 46 a 59 dias
depois do pagamento 0.7 (60 ou mais: 0.3); mais de 120 dias antes 0.5; datas repetidas 0.5;
mais de 8 datas 0.6; data impossível 0.0. Abaixo do limiar da configuração (padrão 0.8) a
linha vira pendência com a sugestão, nunca um palpite gravado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

LIMIAR_PADRAO = 0.8
_ITEM = re.compile(r"^(\d{1,2})(?:/(\d{1,2})(?:/(\d{4}))?)?$")
_JUROS = re.compile(r"[\s.]*\+\s*JUROS(?:\s*DE\s*ATRASO)?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Sessoes:
    datas: tuple[date, ...] = ()
    confianca: float = 1.0
    juros: bool = False
    avisos: tuple[str, ...] = ()
    sugestao: tuple[date, ...] | None = None
    erro: str | None = None
    bruto: str = ""


def _valida(ano: int, mes: int, dia: int) -> date | None:
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def _ano_mais_proximo(dia: int, mes: int, pagamento: date) -> date | None:
    candidatas = [
        d
        for a in (pagamento.year - 1, pagamento.year, pagamento.year + 1)
        if (d := _valida(a, mes, dia)) is not None
    ]
    return min(candidatas, key=lambda d: abs((d - pagamento).days)) if candidatas else None


def _fator_distancia(d: date, pagamento: date) -> tuple[float, str | None]:
    delta = (d - pagamento).days
    if delta >= 60:
        return 0.3, f"{d.isoformat()} é {delta} dias depois do pagamento"
    if delta > 45:
        return 0.7, f"{d.isoformat()} é {delta} dias depois do pagamento"
    if delta < -120:
        return 0.5, f"{d.isoformat()} é {-delta} dias antes do pagamento"
    return 1.0, None


def _sugerir(d: date, pagamento: date) -> tuple[date, ...] | None:
    candidatas = []
    if d.day <= 12 and d.month != d.day:
        candidatas.append(_valida(d.year, d.day, d.month))
    candidatas.append(_valida(pagamento.year, pagamento.month, d.day))
    for c in candidatas:
        if c is not None and c != d and _fator_distancia(c, pagamento)[0] == 1.0:
            return (c,)
    return None


def interpretar_data(valor: date | datetime, pagamento: date) -> Sessoes:
    d = valor.date() if isinstance(valor, datetime) else valor
    fator, aviso = _fator_distancia(d, pagamento)
    return Sessoes(
        datas=(d,),
        confianca=fator,
        avisos=(aviso,) if aviso else (),
        sugestao=_sugerir(d, pagamento) if fator < 1.0 else None,
        bruto=d.isoformat(),
    )


def interpretar_texto(texto: str, pagamento: date) -> Sessoes:
    bruto = texto
    texto = texto.strip()
    juros = False
    m = _JUROS.search(texto)
    if m:
        juros, texto = True, texto[: m.start()]
    texto = texto.strip(" .;")
    if not texto:
        return Sessoes(juros=juros, bruto=bruto)
    itens = [i.strip() for i in texto.split(",")]
    parsed: list[tuple[int, int | None, int | None]] = []
    for item in itens:
        g = _ITEM.match(item.replace(" ", ""))
        if not g:
            return Sessoes(
                confianca=0.0, juros=juros, erro=f"item fora da gramática: {item!r}", bruto=bruto
            )
        parsed.append(
            (
                int(g.group(1)),
                int(g.group(2)) if g.group(2) else None,
                int(g.group(3)) if g.group(3) else None,
            )
        )

    fator, avisos = 1.0, []
    datas: list[date] = []
    mes_h: int | None = None
    ano_h: int | None = None
    herdou_pagamento = False
    for dia, mes, ano in reversed(parsed):
        if mes is not None:
            mes_h, ano_h = mes, ano
        elif mes_h is None:
            mes_h, ano_h = pagamento.month, None
            herdou_pagamento = True
        mes_uso, ano_uso = (mes if mes is not None else mes_h), (ano if mes is not None else ano_h)
        if ano_uso is not None:
            d = _valida(ano_uso, mes_uso, dia)
        else:
            d = _ano_mais_proximo(dia, mes_uso, pagamento)
        if d is None:
            return Sessoes(
                confianca=0.0, juros=juros, erro=f"data impossível: {dia}/{mes_uso}", bruto=bruto
            )
        datas.append(d)
    datas.reverse()
    if herdou_pagamento:
        fator = min(fator, 0.7)
        avisos.append("mês herdado do mês do pagamento")
    for d in datas:
        f, aviso = _fator_distancia(d, pagamento)
        fator = min(fator, f)
        if aviso:
            avisos.append(aviso)
    if len(set(datas)) != len(datas):
        fator = min(fator, 0.5)
        avisos.append("datas repetidas")
    if len(datas) > 8:
        fator = min(fator, 0.6)
        avisos.append("mais de 8 sessões em um pagamento")
    return Sessoes(
        datas=tuple(sorted(set(datas))),
        confianca=fator,
        juros=juros,
        avisos=tuple(avisos),
        bruto=bruto,
    )


def interpretar(celula: object, pagamento: date) -> Sessoes:
    if celula is None or (isinstance(celula, str) and not celula.strip()):
        return Sessoes(bruto="")
    if isinstance(celula, datetime | date):
        return interpretar_data(celula, pagamento)
    if isinstance(celula, bool):
        return Sessoes(confianca=0.0, erro="valor lógico na coluna de sessões", bruto=str(celula))
    if isinstance(celula, int):
        return interpretar_texto(str(celula), pagamento)
    if isinstance(celula, str):
        return interpretar_texto(celula, pagamento)
    return Sessoes(confianca=0.0, erro="tipo de célula não suportado", bruto=repr(celula))


def formatar(datas: tuple[date, ...]) -> str:
    """Forma compacta para exportação; reimportada pelo mesmo parser sem perda."""
    if not datas:
        return ""
    return ",".join(d.strftime("%d/%m/%Y") for d in datas)


__all__ = ["LIMIAR_PADRAO", "Sessoes", "formatar", "interpretar"]
