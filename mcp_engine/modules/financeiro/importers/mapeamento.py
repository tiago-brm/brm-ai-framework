"""Mapeamento da forma de pagamento da planilha para forma, meio e tipo, com vigência."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from mcp_engine.modules.financeiro.db import ErroFinanceiro, parse_data
from mcp_engine.modules.financeiro.importers.sessoes_parser import LIMIAR_PADRAO

PADRAO_FORMAS: list[dict[str, Any]] = [
    {"planilha": "PIX SICOOB", "forma": "pix", "meio": "Sicoob"},
    {"planilha": "PIX STONE", "forma": "pix", "meio": "Stone"},
    {"planilha": "PIX SICREDI", "forma": "pix", "meio": "Sicredi"},
    {"planilha": "PIX INTER", "forma": "pix", "meio": "Inter"},
    {"planilha": "DEB", "forma": "debito", "meio": "Stone"},
    {"planilha": "CRED", "forma": "credito", "meio": "Stone"},
    {"planilha": "DINH", "forma": "dinheiro", "meio": "Caixa"},
    {"planilha": "TED SICOOB", "forma": "ted", "meio": "Sicoob"},
    {"planilha": "ACORDO", "forma": "acordo", "meio": None, "tipo": "acordo"},
]
PADRAO_DESPESAS: list[dict[str, Any]] = [
    {"planilha": "PIX", "forma": "pix"},
    {"planilha": "TED", "forma": "ted"},
    {"planilha": "DINH", "forma": "dinheiro"},
    {"planilha": "DEB", "forma": "debito"},
    {"planilha": "CRED", "forma": "credito"},
    {"planilha": "HOLERITE", "forma": None},
]


def chave(texto: object) -> str:
    return re.sub(r"\s+", " ", str(texto or "").strip().upper())


@dataclass(frozen=True)
class Destino:
    forma: str | None
    meio: str | None
    tipo: str


class Mapeamento:
    def __init__(self, dados: dict[str, Any] | None = None) -> None:
        dados = dados or {}
        self.confianca_minima = float(dados.get("confianca_minima", LIMIAR_PADRAO))
        self._formas = self._indexar(PADRAO_FORMAS, dados.get("formas"))
        self._despesas = self._indexar(PADRAO_DESPESAS, dados.get("despesas_formas"))

    @staticmethod
    def _indexar(padrao: list[dict[str, Any]], custom: list[dict[str, Any]] | None):
        entradas = list(custom) if custom else []
        definidas = {chave(e["planilha"]) for e in entradas}
        entradas += [e for e in padrao if chave(e["planilha"]) not in definidas]
        indice: dict[str, list[dict[str, Any]]] = {}
        for e in entradas:
            indice.setdefault(chave(e["planilha"]), []).append(e)
        return indice

    @classmethod
    def carregar(cls, caminho: Path | None) -> Mapeamento:
        if caminho is not None and Path(caminho).is_file():
            return cls(yaml.safe_load(Path(caminho).read_text()) or {})
        return cls()

    @staticmethod
    def _vigente(e: dict[str, Any], em: date) -> bool:
        de = parse_data(e["vigente_de"]) if e.get("vigente_de") else None
        ate = parse_data(e["vigente_ate"]) if e.get("vigente_ate") else None
        return (de is None or de <= em) and (ate is None or em <= ate)

    def resolver(self, rotulo: object, em: date, *, despesa: bool = False) -> Destino:
        candidatos = [
            e
            for e in (self._despesas if despesa else self._formas).get(chave(rotulo), [])
            if self._vigente(e, em)
        ]
        if not candidatos:
            raise ErroFinanceiro(f"Forma de pagamento sem mapeamento: {str(rotulo).strip()!r}.")
        if len(candidatos) > 1:
            raise ErroFinanceiro(f"Mapeamento ambíguo para {str(rotulo).strip()!r} em {em}.")
        e = candidatos[0]
        return Destino(e.get("forma"), e.get("meio"), e.get("tipo", "sessao"))

    def rotulo_de(
        self, forma: str | None, meio: str | None, em: date, *, despesa: bool = False
    ) -> str | None:
        """Inverso para exportação: rótulo da planilha para forma e meio na data."""
        fonte = self._despesas if despesa else self._formas
        vigentes = [
            (rotulo, e)
            for rotulo, entradas in fonte.items()
            for e in entradas
            if e.get("forma") == forma and self._vigente(e, em)
        ]
        for rotulo, e in vigentes:
            if despesa or e.get("meio") == meio:
                return rotulo
        if forma in ("debito", "credito") and vigentes:
            return vigentes[0][0]
        return None
