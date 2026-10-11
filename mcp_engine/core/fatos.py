"""Provedores de fatos de módulo (spec 031, parte A1).

Um provedor é uma função somente leitura, sem efeito colateral e rápida (meta: menos de 50 ms)
que calcula sinais a partir dos dados do módulo. O motor injeta o resultado nas regras JSONLogic
depois dos argumentos do chamador, que por isso nunca consegue forçar um fato.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

FATOS_DO_MOTOR = frozenset({"skill_name", "actor", "actor_role", "origem_sistema"})
_NOME_PROVEDOR = re.compile(r"^[a-z][a-z0-9_]{0,31}\.[a-z][a-z0-9_]{0,31}$")
_NOME_FATO = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_ESCALARES = (bool, int, float, str, type(None))


class FatosErro(Exception):
    pass


class FatosIndisponiveis(FatosErro):
    """O provedor falhou: o motor nega a chamada em vez de decidir sem o fato."""

    def __init__(self, provedor: str, motivo: str) -> None:
        super().__init__(f"fatos de módulo indisponíveis ({provedor}): {motivo}")
        self.provedor = provedor


@dataclass(frozen=True)
class ContextoFatos:
    client_id: str
    actor: str
    role: str
    data_root: Path


FuncaoFatos = Callable[[ContextoFatos, Mapping[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class Provedor:
    nome: str
    fn: FuncaoFatos
    fatos: frozenset[str]


class ProvedoresFatos:
    def __init__(self) -> None:
        self._provedores: dict[str, Provedor] = {}

    def registrar(self, nome: str, fn: FuncaoFatos, fatos: Iterable[str]) -> None:
        declarados = frozenset(fatos)
        if not _NOME_PROVEDOR.match(nome):
            raise FatosErro(f"nome de provedor inválido: {nome!r}")
        if nome in self._provedores:
            raise FatosErro(f"provedor já registrado: {nome!r}")
        if not declarados:
            raise FatosErro(f"provedor {nome!r} precisa declarar os fatos que produz")
        for fato in declarados:
            if not _NOME_FATO.match(fato) or fato in FATOS_DO_MOTOR:
                raise FatosErro(f"nome de fato inválido ou reservado pelo motor: {fato!r}")
        self._provedores[nome] = Provedor(nome, fn, declarados)

    def nomes(self) -> list[str]:
        return sorted(self._provedores)

    def existe(self, nome: str) -> bool:
        return nome in self._provedores

    def fatos_de(self, nomes: Iterable[str]) -> frozenset[str]:
        out: set[str] = set()
        for nome in nomes:
            provedor = self._provedores.get(nome)
            if provedor is not None:
                out |= provedor.fatos
        return frozenset(out)

    def validar_uso(
        self, nomes: Iterable[str], parametros: Iterable[str], derivados: Iterable[str]
    ) -> list[str]:
        """Erros de uma declaração `fatos_de_modulo` (provedor inexistente ou nome em conflito)."""
        erros: list[str] = []
        vistos: dict[str, str] = {}
        proibidos = set(parametros) | set(derivados)
        for nome in nomes:
            provedor = self._provedores.get(nome)
            if provedor is None:
                erros.append(f"provedor de fatos desconhecido: {nome!r}")
                continue
            for fato in sorted(provedor.fatos):
                if fato in proibidos:
                    erros.append(f"fato {fato!r} de {nome} colide com parâmetro ou fato derivado")
                if fato in vistos and vistos[fato] != nome:
                    erros.append(f"fato {fato!r} produzido por {vistos[fato]} e por {nome}")
                vistos[fato] = nome
        return erros

    def calcular(
        self, nomes: Iterable[str], ctx: ContextoFatos, argumentos: Mapping[str, Any]
    ) -> dict[str, Any]:
        fatos: dict[str, Any] = {}
        for nome in nomes:
            provedor = self._provedores.get(nome)
            if provedor is None:
                raise FatosIndisponiveis(nome, "provedor não registrado")
            try:
                bruto = provedor.fn(ctx, MappingProxyType(dict(argumentos)))
            except Exception as exc:
                raise FatosIndisponiveis(nome, type(exc).__name__) from exc
            if not isinstance(bruto, dict):
                raise FatosIndisponiveis(nome, "resultado fora do contrato")
            for fato, valor in bruto.items():
                if fato not in provedor.fatos:
                    raise FatosIndisponiveis(nome, f"fato não declarado: {fato!r}")
                if not isinstance(valor, _ESCALARES):
                    raise FatosIndisponiveis(nome, f"valor não escalar em {fato!r}")
            fatos.update(bruto)
        return fatos
