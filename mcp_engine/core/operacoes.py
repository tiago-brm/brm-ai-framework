"""Operações com confirmação e aprovação (spec 022).

Uma operação é uma chamada de skill com efeito que ainda não foi executada:
o motor a grava com um resumo gerado por ele mesmo, e ela só executa depois
da confirmação de quem propôs (A2) ou da aprovação de outro perfil (A3 ou
regra `require_approval`). As transições são atômicas (comparar e trocar).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from mcp_engine.core.state import OperacaoRow, StateStore

PROPOSTA = "proposta"
AGUARDANDO = "aguardando_aprovacao"
CONFIRMADA = "confirmada"
EXECUTADA = "executada"
FALHOU = "falhou"
EXPIRADA = "expirada"
CANCELADA = "cancelada"
REJEITADA = "rejeitada"

TRANSICOES: dict[str, frozenset[str]] = {
    PROPOSTA: frozenset({CONFIRMADA, CANCELADA, EXPIRADA}),
    AGUARDANDO: frozenset({CONFIRMADA, REJEITADA, CANCELADA, EXPIRADA}),
    CONFIRMADA: frozenset({EXECUTADA, FALHOU}),
}
ESTADOS = frozenset(
    {PROPOSTA, AGUARDANDO, CONFIRMADA, EXECUTADA, FALHOU, EXPIRADA, CANCELADA, REJEITADA}
)
RANK = {"viewer": 0, "admin": 1, "super_admin": 2}


class Operacao(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    skill: str
    argumentos: dict[str, Any]
    digest_argumentos: str
    resumo: str
    status: str
    proposto_por: str
    proposto_papel: str
    confirmado_por: str | None = None
    aprovado_por: str | None = None
    aprovador_papel: str | None = None
    regra_id: str | None = None
    motivo: str | None = None
    resultado: dict[str, Any] | None = None
    criado_em: datetime
    expira_em: datetime
    executada_em: datetime | None = None


def digest_argumentos(argumentos: dict[str, Any]) -> str:
    canon = json.dumps(argumentos, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _to_model(row: OperacaoRow) -> Operacao:
    return Operacao(
        id=row.id,
        skill=row.skill,
        argumentos=json.loads(row.argumentos),
        digest_argumentos=row.digest_argumentos,
        resumo=row.resumo,
        status=row.status,
        proposto_por=row.proposto_por,
        proposto_papel=row.proposto_papel,
        confirmado_por=row.confirmado_por,
        aprovado_por=row.aprovado_por,
        aprovador_papel=row.aprovador_papel,
        regra_id=row.regra_id,
        motivo=row.motivo,
        resultado=json.loads(row.resultado) if row.resultado else None,
        criado_em=_utc(row.criado_em),
        expira_em=_utc(row.expira_em),
        executada_em=_utc(row.executada_em) if row.executada_em else None,
    )


class OperacaoStore:
    def __init__(self, state: StateStore) -> None:
        self._engine = state.engine

    def criar(
        self,
        *,
        skill: str,
        argumentos: dict[str, Any],
        resumo: str,
        status: str,
        proposto_por: str,
        proposto_papel: str,
        aprovador_papel: str | None,
        regra_id: str | None,
        ttl_horas: int,
    ) -> Operacao:
        if status not in (PROPOSTA, AGUARDANDO):
            raise ValueError(f"estado inicial inválido: {status}")
        now = datetime.now(UTC)
        row = OperacaoRow(
            id=uuid.uuid4().hex,
            skill=skill,
            argumentos=json.dumps(argumentos, sort_keys=True, default=str, ensure_ascii=False),
            digest_argumentos=digest_argumentos(argumentos),
            resumo=resumo,
            status=status,
            proposto_por=proposto_por,
            proposto_papel=proposto_papel,
            aprovador_papel=aprovador_papel,
            regra_id=regra_id,
            criado_em=now,
            expira_em=now + timedelta(hours=ttl_horas),
        )
        with Session(self._engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return _to_model(row)

    def get(self, operacao_id: str) -> Operacao | None:
        with Session(self._engine) as session:
            row = session.get(OperacaoRow, operacao_id)
            return _to_model(row) if row else None

    def transicao(self, operacao_id: str, de: str, para: str, **campos: Any) -> bool:
        """Troca o estado só se ainda estiver em `de`. Falso quando perdeu a corrida."""
        if para not in TRANSICOES.get(de, frozenset()):
            raise ValueError(f"transição inválida: {de} -> {para}")
        for key in ("resultado",):
            if key in campos and campos[key] is not None:
                campos[key] = json.dumps(campos[key], sort_keys=True, default=str)
        with Session(self._engine) as session:
            result = session.execute(
                update(OperacaoRow)
                .where(OperacaoRow.id == operacao_id, OperacaoRow.status == de)
                .values(status=para, **campos)
            )
            session.commit()
            return result.rowcount == 1

    def listar(self, status: str | None = None) -> list[Operacao]:
        with Session(self._engine) as session:
            stmt = select(OperacaoRow).order_by(OperacaoRow.criado_em.desc())
            if status:
                stmt = stmt.where(OperacaoRow.status == status)
            return [_to_model(r) for r in session.scalars(stmt)]

    def orfas(self) -> list[Operacao]:
        return self.listar(CONFIRMADA)

    def contagens(self) -> dict[str, Any]:
        """Visão agregada e somente leitura, sem argumentos nem resumos (para os módulos)."""
        agora = datetime.now(UTC).replace(tzinfo=None)
        por_estado: dict[str, int] = {}
        por_skill: dict[str, dict[str, int]] = {}
        with Session(self._engine) as session:
            expirada = (
                OperacaoRow.status.in_((PROPOSTA, AGUARDANDO)) & (OperacaoRow.expira_em <= agora)
            ).label("expirada")
            linhas = session.execute(
                select(OperacaoRow.skill, OperacaoRow.status, expirada, func.count()).group_by(
                    OperacaoRow.skill, OperacaoRow.status, expirada
                )
            ).all()
            mais_antiga = session.scalar(
                select(func.min(OperacaoRow.criado_em)).where(
                    OperacaoRow.status == AGUARDANDO, OperacaoRow.expira_em > agora
                )
            )
        for skill, status, vencida, total in linhas:
            estado = EXPIRADA if vencida else status
            por_estado[estado] = por_estado.get(estado, 0) + total
            do_skill = por_skill.setdefault(skill, {})
            do_skill[estado] = do_skill.get(estado, 0) + total
        horas = None
        if mais_antiga is not None:
            horas = round((agora - mais_antiga.replace(tzinfo=None)).total_seconds() / 3600, 2)
        return {
            "por_estado": dict(sorted(por_estado.items())),
            "por_skill": {k: dict(sorted(v.items())) for k, v in sorted(por_skill.items())},
            "aguardando_aprovacao": por_estado.get(AGUARDANDO, 0),
            "aguardando_mais_antiga_horas": horas,
        }
