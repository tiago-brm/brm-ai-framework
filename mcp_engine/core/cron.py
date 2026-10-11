"""Expressão cron de cinco campos (minuto hora dia-do-mês mês dia-da-semana).

Cobre `*`, listas, intervalos e passos, como o crontab(5) padrão. Quando dia-do-mês e
dia-da-semana são ambos restritos, vale um OU o outro (regra do Vixie cron). Sem nomes nem
atalhos `@`: o motor só aceita o que consegue validar por inteiro.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

_LIMITES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_NOMES = ("minuto", "hora", "dia do mês", "mês", "dia da semana")
_HORIZONTE_DIAS = 366 * 5


class CronErro(ValueError):
    pass


@dataclass(frozen=True)
class Cron:
    minutos: tuple[int, ...]
    horas: tuple[int, ...]
    dias: frozenset[int]
    meses: frozenset[int]
    semanas: frozenset[int]
    dia_livre: bool
    semana_livre: bool

    def dia_casa(self, dia: date) -> bool:
        dom = dia.day in self.dias
        dow = (dia.isoweekday() % 7) in self.semanas
        if not self.dia_livre and not self.semana_livre:
            return dom or dow
        return dom and dow


def _campo(texto: str, indice: int) -> set[int]:
    inicio, fim = _LIMITES[indice]
    valores: set[int] = set()
    if not texto:
        raise CronErro(f"campo vazio em {_NOMES[indice]}")
    for parte in texto.split(","):
        base, barra, passo_txt = parte.partition("/")
        passo = 1
        if barra:
            if not passo_txt.isdigit() or int(passo_txt) < 1:
                raise CronErro(f"passo inválido em {_NOMES[indice]}: {parte!r}")
            passo = int(passo_txt)
        if base == "*":
            de, ate = inicio, fim
        else:
            lim, hifen, topo = base.partition("-")
            if not lim.isdigit() or (hifen and not topo.isdigit()):
                raise CronErro(f"valor inválido em {_NOMES[indice]}: {parte!r}")
            de = int(lim)
            ate = int(topo) if hifen else (fim if barra else de)
        if de < inicio or ate > fim or de > ate:
            raise CronErro(f"fora do intervalo {inicio}-{fim} em {_NOMES[indice]}: {parte!r}")
        valores.update(range(de, ate + 1, passo))
    return valores


def parse(expressao: str) -> Cron:
    campos = str(expressao).split()
    if len(campos) != 5:
        raise CronErro("a expressão precisa de 5 campos: minuto hora dia mês dia-da-semana")
    minutos, horas, dias, meses, semanas = (_campo(c, i) for i, c in enumerate(campos))
    return Cron(
        minutos=tuple(sorted(minutos)),
        horas=tuple(sorted(horas)),
        dias=frozenset(dias),
        meses=frozenset(meses),
        semanas=frozenset(d % 7 for d in semanas),
        dia_livre=campos[2].startswith("*"),
        semana_livre=campos[4].startswith("*"),
    )


def proxima(cron: Cron, depois: datetime, fuso: str = "America/Sao_Paulo") -> datetime:
    """Primeira ocorrência estritamente posterior a `depois`, em UTC."""
    tz = ZoneInfo(fuso)
    base = depois.astimezone(tz).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for deslocamento in range(_HORIZONTE_DIAS):
        dia = base.date() + timedelta(days=deslocamento)
        if dia.month not in cron.meses or not cron.dia_casa(dia):
            continue
        for hora in cron.horas:
            for minuto in cron.minutos:
                candidato = datetime(dia.year, dia.month, dia.day, hora, minuto, tzinfo=tz)
                if candidato >= base and candidato.astimezone(tz).hour == hora:
                    return candidato.astimezone(UTC)
    raise CronErro("a expressão nunca dispara")
