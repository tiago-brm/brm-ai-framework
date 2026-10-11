"""Dinheiro em centavos inteiros: entrada em reais (parse_brl) e saída formatada."""

from __future__ import annotations

import re
from decimal import Decimal

MAX_CENTAVOS = 10_000_000_00

_SO_DIGITOS = re.compile(r"\d+")
_MILHAR = re.compile(r"\d{1,3}(\.\d{3})+")


class ValorInvalido(ValueError):
    pass


def parse_brl(texto: object) -> int:
    """'R$ 1.234,56', '275', '275,5' ou '275.50' viram centavos; ambiguidade é recusada."""
    if isinstance(texto, bool) or isinstance(texto, float):
        raise ValorInvalido("valor deve ser texto em reais, nunca número de ponto flutuante")
    if isinstance(texto, int):
        centavos = texto * 100
    elif isinstance(texto, Decimal):
        centavos = int((texto * 100).to_integral_value())
        if texto * 100 != centavos:
            raise ValorInvalido("valor com mais de duas casas decimais")
    elif isinstance(texto, str):
        centavos = _parse_texto(texto)
    else:
        raise ValorInvalido("valor inválido")
    if not 0 <= centavos <= MAX_CENTAVOS:
        raise ValorInvalido("valor fora do intervalo permitido")
    return centavos


def _parse_texto(texto: str) -> int:
    s = re.sub(r"\s+", "", texto.strip())
    if s[:2].upper() == "R$":
        s = s[2:]
    if not s or s.startswith("-") or s.startswith("+"):
        raise ValorInvalido(f"valor inválido: {texto!r}")
    if "," in s:
        inteiro, _, frac = s.rpartition(",")
        if "," in inteiro or not 1 <= len(frac) <= 2 or not _SO_DIGITOS.fullmatch(frac):
            raise ValorInvalido(f"valor ambíguo ou inválido: {texto!r}")
        if "." in inteiro and not _MILHAR.fullmatch(inteiro):
            raise ValorInvalido(f"separador de milhar inválido: {texto!r}")
        inteiro = inteiro.replace(".", "")
    elif "." in s:
        if _MILHAR.fullmatch(s):
            if s.count(".") == 1:
                raise ValorInvalido(f"valor ambíguo (milhar ou decimal): {texto!r}")
            inteiro, frac = s.replace(".", ""), "0"
        else:
            inteiro, _, frac = s.partition(".")
            if "." in frac or not 1 <= len(frac) <= 2:
                raise ValorInvalido(f"valor ambíguo ou inválido: {texto!r}")
    else:
        inteiro, frac = s, "0"
    if not _SO_DIGITOS.fullmatch(inteiro) or not _SO_DIGITOS.fullmatch(frac):
        raise ValorInvalido(f"valor inválido: {texto!r}")
    return int(inteiro) * 100 + int(frac.ljust(2, "0"))


def formatar_brl(centavos: int) -> str:
    sinal = "-" if centavos < 0 else ""
    reais, cent = divmod(abs(centavos), 100)
    return f"{sinal}R$ {reais:,}".replace(",", ".") + f",{cent:02d}"


def meio_para_cima(numerador: int, denominador: int) -> int:
    """Divisão inteira com arredondamento meio para cima (valores não negativos)."""
    return (2 * numerador + denominador) // (2 * denominador)


def bp_de_percentual(texto: object) -> int:
    """'40', '40%', '12,5%' viram pontos-base (4000, 1250)."""
    if isinstance(texto, bool) or isinstance(texto, float):
        raise ValorInvalido("percentual deve ser texto")
    s = str(texto).strip().rstrip("%").strip().replace(",", ".")
    if not re.fullmatch(r"\d{1,3}(\.\d{1,2})?", s):
        raise ValorInvalido(f"percentual inválido: {texto!r}")
    bp = int((Decimal(s) * 100).to_integral_value())
    if not 0 <= bp <= 10000:
        raise ValorInvalido("percentual fora de 0 a 100")
    return bp
