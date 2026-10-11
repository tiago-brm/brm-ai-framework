"""Fuso do estabelecimento: a Agenda guarda UTC; o usuário fala em dia local."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

FUSO_PADRAO = ZoneInfo("America/Sao_Paulo")


def parse_dia(valor: object, padrao: date) -> date:
    if valor is None or str(valor).strip() == "":
        return padrao
    try:
        return date.fromisoformat(str(valor).strip())
    except ValueError:
        raise ValueError(f"Data inválida: {valor!r} (use AAAA-MM-DD).") from None


def limites_utc(de: date, ate: date, fuso: ZoneInfo = FUSO_PADRAO) -> tuple[datetime, datetime]:
    """Intervalo [início do dia `de`, início do dia seguinte a `ate`) em UTC."""
    inicio = datetime.combine(de, time.min, tzinfo=fuso).astimezone(UTC)
    fim = datetime.combine(ate + timedelta(days=1), time.min, tzinfo=fuso).astimezone(UTC)
    return inicio, fim


def para_local(instante: datetime, fuso: ZoneInfo = FUSO_PADRAO) -> datetime:
    return instante.astimezone(fuso)
