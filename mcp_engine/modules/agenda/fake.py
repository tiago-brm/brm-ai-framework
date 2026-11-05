"""Provider falso para testes: memória, sem rede. Implementa também o contrato de cadastro
(spec 035), registrando cada gravação com ator e trace_id."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import date, datetime
from typing import Any

from mcp_engine.modules.agenda.provider import (
    AgendaErro,
    Agendamento,
    Cliente,
    Endereco,
    FichaCliente,
    Profissional,
    Responsavel,
    Servico,
    StatusSessao,
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
        self.fichas: dict[str, FichaCliente] = {}
        self.historicos: dict[str, list[StatusSessao]] = {}
        self.escritas: list[dict[str, Any]] = []
        self.status_escritos: list[dict[str, Any]] = []
        self.pagamentos: list[dict[str, Any]] = []
        self.crps_escritos: list[dict[str, Any]] = []
        self.saldos: dict[str, dict[str, Any]] = {}
        self.recibos: dict[str, dict[str, Any]] = {}
        self.confirmacoes: list[dict[str, Any]] = []
        self.corte_bonus: str | None = None
        self.aptos_bonus: set[str] = set()
        self.bonus_escritos: list[dict[str, Any]] = []

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

    def obter_agendamento(self, agendamento_id: str) -> Agendamento | None:
        self._checar()
        return self.agendamentos.get(agendamento_id)

    def obter_cliente(
        self,
        customer_id: str,
        *,
        ator: str | None = None,
        trace_id: str | None = None,
        finalidade: str | None = None,
    ) -> FichaCliente | None:
        self._checar()
        return self.fichas.get(customer_id)

    def historico_cliente(self, customer_id: str) -> list[StatusSessao]:
        self._checar()
        if customer_id in self.historicos:
            return list(self.historicos[customer_id])
        return sorted(
            (
                StatusSessao(a.id, a.status, a.inicio_utc)
                for a in self.agendamentos.values()
                if a.customer_id == customer_id
            ),
            key=lambda s: s.inicio_utc,
        )

    def registrar_pagamento(
        self,
        customer_id: str,
        centavos: int,
        metodo: str,
        *,
        recebido_em: datetime,
        referencia: str,
        nota: str | None,
        ator: str,
        trace_id: str | None,
    ) -> dict[str, Any]:
        self._checar()
        if customer_id not in {c.id for c in self.clientes}:
            raise AgendaErro("Paciente não encontrado na Agenda.")
        anterior = next((p for p in self.pagamentos if p["referencia"] == referencia), None)
        if anterior is not None:
            return {**anterior["resposta"], "duplicate": True}
        saldo = self.saldos.get(customer_id, {})
        bate = centavos in {
            round(float(saldo.get(k) or 0) * 100) for k in ("outstanding", "accumulating")
        }
        pid = f"00000000-0000-4000-8000-{len(self.pagamentos) + 1:012d}"
        resposta = {
            "success": True,
            "payment_id": pid,
            "status": "CONFIRMED" if bate else "PENDING_REVIEW",
            "duplicate": False,
            "receipt": self._recibo(pid, centavos) if bate else None,
        }
        self.pagamentos.append(
            {
                "customer_id": customer_id,
                "centavos": centavos,
                "metodo": metodo,
                "referencia": referencia,
                "recebido_em": recebido_em,
                "ator": ator,
                "resposta": resposta,
            }
        )
        return resposta

    def _recibo(self, pagamento_id: str, centavos: int) -> dict[str, Any]:
        numero = f"2026-{len(self.recibos) + 1:06d}"
        recibo = {
            "number": numero,
            "amount": centavos / 100,
            "amount_in_words": f"{centavos // 100} reais",
            "paid_at": "2026-11-03T12:00:00-03:00",
            "method": "CASH",
            "payer": {"name": "Tiago Pereira Ramos", "cpf": "***.***.***-25"},
            "professionals": [{"name": "Ana Souza", "crp": "CRP 06/123456"}],
            "items": [{"description": "Sessão de psicoterapia"}],
            "registered_by": "sec@cli.test",
        }
        self.recibos[pagamento_id] = recibo
        return recibo

    def confirmar_pagamento(
        self, pagamento_id: str, *, ator: str, trace_id: str | None
    ) -> dict[str, Any]:
        self._checar()
        pagamento = next(
            (p for p in self.pagamentos if p["resposta"]["payment_id"] == pagamento_id), None
        )
        if pagamento is None:
            raise AgendaErro("Pagamento não encontrado na Agenda.")
        if pagamento["resposta"]["status"] != "PENDING_REVIEW":
            raise AgendaErro("Este pagamento não está aguardando conferência.")
        pagamento["resposta"].update(
            status="CONFIRMED", receipt=self._recibo(pagamento_id, pagamento["centavos"])
        )
        self.confirmacoes.append({"pagamento_id": pagamento_id, "ator": ator})
        return dict(pagamento["resposta"])

    def recibo_pagamento(
        self, pagamento_id: str, *, ator: str, trace_id: str | None
    ) -> dict[str, Any] | None:
        self._checar()
        return self.recibos.get(pagamento_id)

    def politica_bonus_indicacao(self) -> dict[str, Any]:
        self._checar()
        return {"tiers": [], "referral_bonus_new_patients_from": self.corte_bonus}

    def definir_corte_bonus_indicacao(
        self, data_corte: date | None, *, ator: str, trace_id: str | None
    ) -> None:
        self._checar()
        self.corte_bonus = data_corte.isoformat() if data_corte else None
        self.bonus_escritos.append({"corte": self.corte_bonus, "ator": ator})

    def aptidao_bonus_indicacao(self) -> list[dict[str, Any]]:
        self._checar()
        return [
            {"id": p.id, "name": p.nome, "referral_bonus_eligible": p.id in self.aptos_bonus}
            for p in self.profissionais
        ]

    def definir_aptidao_bonus_indicacao(
        self, profissional_id: str, apto: bool, *, ator: str, trace_id: str | None
    ) -> None:
        self._checar()
        if profissional_id not in {p.id for p in self.profissionais}:
            raise AgendaErro("Profissional não encontrado na Agenda.")
        (self.aptos_bonus.add if apto else self.aptos_bonus.discard)(profissional_id)
        self.bonus_escritos.append({"profissional_id": profissional_id, "apto": apto, "ator": ator})

    def definir_crp(
        self, profissional_id: str, crp: str | None, *, ator: str, trace_id: str | None
    ) -> None:
        self._checar()
        alvo = next((p for p in self.profissionais if p.id == profissional_id), None)
        if alvo is None:
            raise AgendaErro("Profissional não encontrado na Agenda.")
        self.profissionais = [
            replace(p, crp=crp) if p.id == profissional_id else p for p in self.profissionais
        ]
        self.crps_escritos.append({"profissional_id": profissional_id, "crp": crp, "ator": ator})

    def saldo_cliente(self, customer_id: str) -> dict[str, Any] | None:
        self._checar()
        return self.saldos.get(customer_id)

    def atualizar_status(
        self,
        agendamento_id: str,
        status: str,
        *,
        motivo: str | None,
        ator: str,
        trace_id: str | None,
    ) -> None:
        self._checar()
        atual = self.agendamentos.get(agendamento_id)
        if atual is None:
            raise AgendaErro("Agendamento não encontrado na Agenda.")
        self.agendamentos[agendamento_id] = replace(atual, status=status)
        self.status_escritos.append(
            {"agendamento_id": agendamento_id, "status": status, "motivo": motivo, "ator": ator}
        )

    def atualizar_cliente(
        self, customer_id: str, campos: dict[str, Any], *, ator: str, trace_id: str | None
    ) -> None:
        self._checar()
        ficha = self.fichas.get(customer_id)
        if ficha is None:
            raise AgendaErro("Cliente não encontrado na Agenda.")
        self.escritas.append(
            {"customer_id": customer_id, "campos": campos, "ator": ator, "trace_id": trace_id}
        )
        mudancas: dict[str, Any] = {}
        for chave, valor in campos.items():
            if chave == "endereco":
                mudancas["endereco"] = replace(ficha.endereco or Endereco(), **valor)
            elif chave == "responsavel":
                atual = ficha.responsaveis[0] if ficha.responsaveis else Responsavel()
                novos = dict(valor)
                if "endereco" in novos:
                    novos["endereco"] = replace(atual.endereco or Endereco(), **novos["endereco"])
                mudancas["responsaveis"] = (replace(atual, **novos), *ficha.responsaveis[1:])
            elif chave == "nascimento":
                mudancas["nascimento"] = date.fromisoformat(valor)
            else:
                mudancas[chave] = valor
        self.fichas[customer_id] = replace(ficha, **mudancas)
