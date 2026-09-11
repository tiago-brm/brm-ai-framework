from __future__ import annotations

import re
from typing import Any

import httpx

DATAJUD_BASE_URL = "https://api-publica.datajud.cnj.jus.br"
# Chave pública compartilhada, documentada em datajud-wiki.cnj.jus.br/api-publica/acesso —
# o CNJ pode alterá-la a qualquer momento; não é um segredo específico deste projeto.
DATAJUD_PUBLIC_API_KEY = "cDZHYzlZa0JadVREZDJCendQbXY6SkJlTzNjLV9TRENyQk1RdnFKZGRQdw=="

_DIGITS_RE = re.compile(r"\D")

# Resolução CNJ 65/2008, Anexo IX/X — código de dois dígitos por UF, usado tanto
# para Justiça Estadual (segmento 8, tjXX) quanto Eleitoral (segmento 6, tre-XX).
_UF_CODES: dict[str, str] = {
    "01": "ac", "02": "al", "03": "ap", "04": "am", "05": "ba", "06": "ce",
    "07": "df", "08": "es", "09": "go", "10": "ma", "11": "mt", "12": "ms",
    "13": "mg", "14": "pa", "15": "pb", "16": "pr", "17": "pe", "18": "pi",
    "19": "rj", "20": "rn", "21": "rs", "22": "ro", "23": "rr", "24": "sc",
    "25": "se", "26": "sp", "27": "to",
}

_SEGMENTO_NOMES = {
    "1": "STF", "2": "CNJ", "3": "STJ", "4": "Justiça Federal",
    "5": "Justiça do Trabalho", "6": "Justiça Eleitoral",
    "7": "Justiça Militar da União", "8": "Justiça Estadual",
    "9": "Justiça Militar Estadual",
}


class DatajudError(Exception):
    pass


def _only_digits(numero: str) -> str:
    return _DIGITS_RE.sub("", numero)


def resolve_tribunal_alias(numero_processo: str) -> tuple[str, dict[str, str]]:
    """Resolve o alias de índice do DataJud a partir do número único do processo.

    Formato (Resolução CNJ 65/2008): NNNNNNN-DD.AAAA.J.TR.OOOO (20 dígitos).
    """
    digits = _only_digits(numero_processo)
    if len(digits) != 20:
        raise DatajudError(
            f"número de processo inválido (esperado 20 dígitos, recebido {len(digits)}): "
            f"{numero_processo!r}"
        )

    ano = digits[9:13]
    segmento = digits[13]
    tr = digits[14:16]
    orgao = digits[16:20]

    meta = {
        "ano": ano,
        "segmento": segmento,
        "segmento_nome": _SEGMENTO_NOMES.get(segmento, "desconhecido"),
        "tr": tr,
        "orgao": orgao,
    }

    if segmento == "3":
        return "api_publica_stj", meta
    if segmento == "4":
        regiao = int(tr)
        if 1 <= regiao <= 6:
            return f"api_publica_trf{regiao}", meta
    if segmento == "5":
        regiao = int(tr)
        if 1 <= regiao <= 24:
            return f"api_publica_trt{regiao}", meta
    if segmento == "6":
        uf = _UF_CODES.get(tr)
        if uf:
            return f"api_publica_tre-{uf}", meta
    if segmento == "8":
        uf = _UF_CODES.get(tr)
        if uf:
            return f"api_publica_tj{uf}", meta

    raise DatajudError(
        f"não foi possível identificar o tribunal para segmento={segmento} "
        f"({meta['segmento_nome']}), tr={tr} — sem cobertura na API pública do DataJud"
    )


def consultar_processo(numero_processo: str) -> dict[str, Any]:
    alias, meta = resolve_tribunal_alias(numero_processo)
    digits = _only_digits(numero_processo)

    try:
        response = httpx.post(
            f"{DATAJUD_BASE_URL}/{alias}/_search",
            headers={
                "Authorization": f"APIKey {DATAJUD_PUBLIC_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"query": {"match": {"numeroProcesso": digits}}},
            timeout=20.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise DatajudError(f"falha na consulta ao DataJud ({alias}): {exc}") from exc

    body = response.json()
    hits = body.get("hits", {}).get("hits", [])

    if not hits:
        return {
            "encontrado": False,
            "tribunal": alias,
            "numero_processo": digits,
            **meta,
        }

    source = hits[0].get("_source", {})
    return {
        "encontrado": True,
        "tribunal": alias,
        "numero_processo": source.get("numeroProcesso", digits),
        "classe": source.get("classe"),
        "orgao_julgador": source.get("orgaoJulgador"),
        "grau": source.get("grau"),
        "data_ajuizamento": source.get("dataAjuizamento"),
        "ultima_atualizacao": source.get("dataHoraUltimaAtualizacao"),
        "assuntos": source.get("assuntos"),
        "movimentos": source.get("movimentos"),
        **meta,
    }
