"""Atributos de governança por pessoa (spec 035): PIN de confirmação e unidades.

Chaveados por (provedor, id do usuário no provedor). O PIN é guardado só como hash scrypt
com sal; tentativas erradas são contadas de forma atômica e bloqueiam por um tempo.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from mcp_engine.core.state import AtributosUsuarioRow, StateStore

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_PIN_MIN = 4
_PIN_MAX = 8

PIN_OK = "ok"
PIN_INCORRETO = "incorreto"
PIN_BLOQUEADO = "bloqueado"
PIN_NAO_DEFINIDO = "nao_definido"


class PinInvalido(ValueError):
    pass


def _b64(dados: bytes) -> str:
    return base64.b64encode(dados).decode("ascii")


def validar_formato_pin(pin: str) -> None:
    if not pin.isdigit() or not _PIN_MIN <= len(pin) <= _PIN_MAX:
        raise PinInvalido(f"O PIN deve ter de {_PIN_MIN} a {_PIN_MAX} dígitos.")
    if len(set(pin)) == 1:
        raise PinInvalido("O PIN não pode repetir um único dígito.")
    passos = {(int(b) - int(a)) % 10 for a, b in zip(pin, pin[1:])}
    if passos in ({1}, {9}):
        raise PinInvalido("O PIN não pode ser uma sequência.")


def hash_pin(pin: str) -> str:
    sal = os.urandom(16)
    chave = hashlib.scrypt(
        pin.encode("utf-8"), salt=sal, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(sal)}${_b64(chave)}"


def conferir_hash_pin(pin: str, armazenado: str) -> bool:
    try:
        esquema, n, r, p, sal, chave = armazenado.split("$")
        if esquema != "scrypt":
            return False
        calculada = hashlib.scrypt(
            pin.encode("utf-8"),
            salt=base64.b64decode(sal),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(base64.b64decode(chave)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calculada, base64.b64decode(chave))


@dataclass(frozen=True)
class ResultadoPin:
    status: str
    restantes: int | None = None
    bloqueado_ate: datetime | None = None

    @property
    def ok(self) -> bool:
        return self.status == PIN_OK


def _utc(valor: datetime | None) -> datetime | None:
    if valor is None:
        return None
    return valor if valor.tzinfo else valor.replace(tzinfo=UTC)


class AtributosUsuarioStore:
    def __init__(self, state: StateStore, tentativas: int = 5, bloqueio_min: int = 15) -> None:
        self._engine = state.engine
        self._tentativas = tentativas
        self._bloqueio = timedelta(minutes=bloqueio_min)

    def _linha(self, session: Session, provedor: str, usuario_id: str) -> AtributosUsuarioRow:
        row = session.get(AtributosUsuarioRow, (provedor, usuario_id))
        if row is None:
            row = AtributosUsuarioRow(provedor=provedor, usuario_id=usuario_id)
            session.add(row)
            session.flush()
        return row

    def nome(self, provedor: str, usuario_id: str) -> str | None:
        with Session(self._engine) as session:
            row = session.get(AtributosUsuarioRow, (provedor, usuario_id))
            return row.nome if row else None

    def definir_nome(self, provedor: str, usuario_id: str, nome: str, *, por: str) -> None:
        limpo = " ".join(nome.split())
        if not 2 <= len(limpo) <= 80:
            raise ValueError("o nome deve ter entre 2 e 80 caracteres")
        with Session(self._engine) as session:
            row = self._linha(session, provedor, usuario_id)
            row.nome = limpo
            row.atualizado_em = datetime.now(UTC)
            row.atualizado_por = por
            session.commit()

    def pin_definido(self, provedor: str, usuario_id: str) -> bool:
        with Session(self._engine) as session:
            row = session.get(AtributosUsuarioRow, (provedor, usuario_id))
            return bool(row and row.pin_hash)

    def verificar_pin(
        self, provedor: str, usuario_id: str, pin: str, agora: datetime | None = None
    ) -> ResultadoPin:
        agora = agora or datetime.now(UTC)
        with Session(self._engine) as session:
            row = session.get(AtributosUsuarioRow, (provedor, usuario_id))
            if row is None or not row.pin_hash:
                return ResultadoPin(PIN_NAO_DEFINIDO)
            bloqueio = _utc(row.pin_bloqueado_ate)
            if bloqueio is not None and bloqueio > agora:
                return ResultadoPin(PIN_BLOQUEADO, 0, bloqueio)
            chave = (AtributosUsuarioRow.provedor == provedor) & (
                AtributosUsuarioRow.usuario_id == usuario_id
            )
            if conferir_hash_pin(pin, row.pin_hash):
                session.execute(
                    update(AtributosUsuarioRow)
                    .where(chave)
                    .values(pin_falhas=0, pin_bloqueado_ate=None)
                )
                session.commit()
                return ResultadoPin(PIN_OK)
            session.execute(
                update(AtributosUsuarioRow)
                .where(chave)
                .values(pin_falhas=AtributosUsuarioRow.pin_falhas + 1)
            )
            session.commit()
            session.refresh(row)
            if row.pin_falhas >= self._tentativas:
                ate = agora + self._bloqueio
                session.execute(
                    update(AtributosUsuarioRow)
                    .where(chave)
                    .values(pin_falhas=0, pin_bloqueado_ate=ate.replace(tzinfo=None))
                )
                session.commit()
                return ResultadoPin(PIN_BLOQUEADO, 0, ate)
            return ResultadoPin(PIN_INCORRETO, self._tentativas - row.pin_falhas)

    def definir_pin(
        self,
        provedor: str,
        usuario_id: str,
        novo: str,
        *,
        por: str,
        atual: str | None = None,
        agora: datetime | None = None,
    ) -> ResultadoPin:
        validar_formato_pin(novo)
        if self.pin_definido(provedor, usuario_id):
            conferido = self.verificar_pin(provedor, usuario_id, atual or "", agora)
            if not conferido.ok:
                return conferido
        with Session(self._engine) as session:
            row = self._linha(session, provedor, usuario_id)
            row.pin_hash = hash_pin(novo)
            row.pin_falhas = 0
            row.pin_bloqueado_ate = None
            row.atualizado_em = datetime.now(UTC)
            row.atualizado_por = por
            session.commit()
        return ResultadoPin(PIN_OK)

    def redefinir_pin(self, provedor: str, usuario_id: str, *, por: str) -> bool:
        with Session(self._engine) as session:
            row = session.get(AtributosUsuarioRow, (provedor, usuario_id))
            if row is None or not row.pin_hash:
                return False
            row.pin_hash = None
            row.pin_falhas = 0
            row.pin_bloqueado_ate = None
            row.atualizado_em = datetime.now(UTC)
            row.atualizado_por = por
            session.commit()
            return True

    def unidades(self, provedor: str, usuario_id: str) -> list[str]:
        with Session(self._engine) as session:
            row = session.get(AtributosUsuarioRow, (provedor, usuario_id))
            return list(json.loads(row.unidades)) if row else []

    def definir_unidades(
        self, provedor: str, usuario_id: str, unidades: list[str], *, por: str
    ) -> None:
        with Session(self._engine) as session:
            row = self._linha(session, provedor, usuario_id)
            row.unidades = json.dumps(sorted({u.strip() for u in unidades if u.strip()}))
            row.atualizado_em = datetime.now(UTC)
            row.atualizado_por = por
            session.commit()
