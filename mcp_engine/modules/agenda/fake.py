"""Provider falso para testes: memória, sem rede, só leitura."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from mcp_engine.modules.agenda.provider import (
    AgendaErro,
    Agendamento,
    Cliente,
    Profissional,
    Servico,
)


class FakeAgendaProvider:
    def __init__(
        self,
        agendamentos: Iterable[Agendamento] = (),
        servicos: Iterable[Servico] = (),
        profissionais: Iterable[Profissional] = (),
        clientes: Iterable[Cliente] = (),
    ) -> None:
        self.agendamentos = {a.id: a for a in agendamentos}
        self.servicos = list(servicos)
        self.profissionais = list(profissionais)
        self.clientes = list(clientes)
        self.erro: AgendaErro | None = None
        self.consultas: list[dict[str, object]] = []

    def semear(self, *agendamentos: Agendamento) -> None:
        for a in agendamentos:
            self.agendamentos[a.id] = a

    def _checar(self) -> None:
        if self.erro is not None:
            raise self.erro

    def listar_agendamentos(
        self,
        *,
        de: datetime | None = None,
        ate: datetime | None = None,
        status: Iterable[str] = (),
        profissional_id: str | None = None,
        atualizado_desde: datetime | None = None,
    ) -> list[Agendamento]:
        self._checar()
        filtro = {s.upper() for s in status}
        self.consultas.append({"de": de, "ate": ate, "desde": atualizado_desde})
        return sorted(
            (
                a
                for a in self.agendamentos.values()
                if (de is None or a.inicio_utc >= de)
                and (ate is None or a.inicio_utc < ate)
                and (not filtro or a.status in filtro)
                and (profissional_id is None or a.staff_id == profissional_id)
                and (atualizado_desde is None or a.atualizado_em >= atualizado_desde)
            ),
            key=lambda a: (a.atualizado_em, a.id),
        )

    def listar_servicos(self) -> list[Servico]:
        self._checar()
        return list(self.servicos)

    def listar_profissionais(self) -> list[Profissional]:
        self._checar()
        return list(self.profissionais)

    def buscar_clientes(self, busca: str = "", limite: int = 20, offset: int = 0) -> list[Cliente]:
        self._checar()
        achados = [c for c in self.clientes if busca.lower() in c.nome.lower()]
        return achados[offset : offset + limite]
