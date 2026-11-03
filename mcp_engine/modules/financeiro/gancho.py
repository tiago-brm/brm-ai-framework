"""Gancho para atualizar a lista de pacientes quando um nome não é encontrado.

O financeiro só conhece os pacientes que outro módulo trouxe para o ledger. Quem os traz
registra aqui uma função de atualização (chaveada pelo ledger do cliente); ao faltar um
paciente, o handler a chama uma vez antes de recusar. A atualização roda fora de qualquer
transação aberta e tem um intervalo mínimo entre execuções, para um nome inexistente não
disparar uma atualização a cada pedido.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

INTERVALO_MIN_S = 60.0

_GANCHOS: dict[str, Callable[[], object]] = {}
_ULTIMA: dict[str, float] = {}


def registrar(ledger: Path, atualizar: Callable[[], object]) -> None:
    _GANCHOS[str(ledger)] = atualizar


def atualizar(ledger: Path, agora: Callable[[], float] = time.monotonic) -> bool:
    chave = str(ledger)
    gancho = _GANCHOS.get(chave)
    if gancho is None:
        return False
    momento = agora()
    anterior = _ULTIMA.get(chave)
    if anterior is not None and momento - anterior < INTERVALO_MIN_S:
        return False
    _ULTIMA[chave] = momento
    try:
        gancho()
    except Exception:
        logger.exception("atualização da lista de pacientes falhou")
        return False
    return True
