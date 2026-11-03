"""Mudança de status de uma sessão na Agenda (tipo de ação da Central de Ações).

O chat prepara o pedido com o nome do paciente (ou o id do agendamento); a pessoa confirma na
página, com o PIN, e o framework altera o status na Agenda com ator e trace_id. A Agenda aceita
`CONFIRMED`, `COMPLETED`, `NO_SHOW` e `CANCELLED`; cancelar exige liberação do gestor. Depois de
alterar, a lista do financeiro é atualizada, porque concluir uma sessão a fatura.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mcp_engine.core.acoes import ContextoAcao, Preparo, Previa, TipoAcao
from mcp_engine.core.executor import ExecucaoFalhou
from mcp_engine.modules import ModuleHost
from mcp_engine.modules.agenda.contrato import sessoes_do_paciente
from mcp_engine.modules.agenda.provider import Agendamento
from mcp_engine.modules.agenda.sincronizacao import executar_sincronizacao
from mcp_engine.modules.agenda.tempo import FUSO_PADRAO, para_local

ROTULOS = {
    "CONFIRMED": "confirmada",
    "COMPLETED": "concluída",
    "NO_SHOW": "falta do paciente",
    "CANCELLED": "cancelada",
    "PENDING_CONFIRMATION": "aguardando confirmação",
    "WAITING": "na espera",
    "IN_PROGRESS": "em atendimento",
}


class AtualizarStatusArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agendamento_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    paciente: str | None = Field(default=None, min_length=2, max_length=80)
    status: Literal["CONFIRMED", "COMPLETED", "NO_SHOW", "CANCELLED"]
    motivo: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def _regras(self) -> AtualizarStatusArgs:
        if (self.agendamento_id is None) == (self.paciente is None):
            raise ValueError("informe agendamento_id ou paciente (um dos dois)")
        if self.status == "CANCELLED" and not self.motivo:
            raise ValueError("informe o motivo do cancelamento")
        if self.status != "CANCELLED" and self.motivo:
            raise ValueError("o motivo só vale para cancelamento")
        return self


def _mais_proxima(sessoes: list[Agendamento], status: str) -> Agendamento:
    agora = datetime.now(UTC)
    pendentes = [a for a in sessoes if a.status != status]
    if not pendentes:
        raise ExecucaoFalhou(f"A sessão já está {ROTULOS[status]} na Agenda.")
    return min(pendentes, key=lambda a: abs((a.inicio_utc - agora).total_seconds()))


def tipo_atualizar_status(
    host: ModuleHost, abrir_provider: Callable[[ModuleHost], Any]
) -> TipoAcao:
    def buscar(args: dict[str, Any]) -> Agendamento:
        with abrir_provider(host) as provider:
            if args.get("agendamento_id"):
                ag = provider.obter_agendamento(args["agendamento_id"])
                if ag is None:
                    raise ExecucaoFalhou("Agendamento não encontrado na Agenda.")
                if ag.status == args["status"]:
                    raise ExecucaoFalhou(f"A sessão já está {ROTULOS[ag.status]} na Agenda.")
                return ag
            return _mais_proxima(sessoes_do_paciente(provider, args["paciente"]), args["status"])

    def resumir(args: dict[str, Any]) -> str:
        alvo = args.get("agendamento_id") and f"da sessão {args['agendamento_id']}"
        alvo = alvo or f"do paciente {args['paciente']}"
        return f"Marcar a sessão {alvo} como {ROTULOS[args['status']]}"

    def preparar(ctx: ContextoAcao, args: dict[str, Any]) -> Preparo:
        return Preparo(sensivel=args["status"] == "CANCELLED")

    def carregar(ctx: ContextoAcao, args: dict[str, Any]) -> Previa:
        ag = buscar(args)
        quando = para_local(ag.inicio_utc, FUSO_PADRAO)
        resumo = (
            f"{ag.customer_name}: sessão de {quando:%d/%m/%Y} às {quando:%H:%M}"
            f" com {ag.staff_name or 'profissional não informado'}, de"
            f" {ROTULOS.get(ag.status, ag.status)} para {ROTULOS[args['status']]}"
        )
        dados: dict[str, Any] = {
            "status_atual": ag.status,
            "novo_status": args["status"],
            "aviso": "Concluir uma sessão a fatura na Agenda; a Agenda não avisa o paciente.",
        }
        if args.get("motivo"):
            dados["motivo"] = args["motivo"]
        return Previa(resumo=resumo, dados=dados)

    def executar(ctx: ContextoAcao, args: dict[str, Any]) -> dict[str, Any]:
        ag = buscar(args)
        with abrir_provider(host) as provider:
            provider.atualizar_status(
                ag.id,
                args["status"],
                motivo=args.get("motivo"),
                ator=ctx.ator,
                trace_id=ctx.trace_id,
            )
        executar_sincronizacao(host)
        return {
            "resumo": f"Sessão marcada como {ROTULOS[args['status']]}.",
            "agendamento_id": ag.id,
        }

    return TipoAcao(
        nome="atualizar_status_sessao",
        titulo="Atualizar status da sessão",
        descricao=(
            "Muda o status de uma sessão na Agenda: CONFIRMED (confirmada), COMPLETED (concluída),"
            " NO_SHOW (falta) ou CANCELLED (cancelada, exige `motivo` e a liberação do gestor)."
            " Informe o `paciente` (nome como o usuário digitou; usa a sessão mais próxima de"
            " agora) ou o `agendamento_id`. Concluir fatura a sessão na Agenda."
        ),
        esquema=AtualizarStatusArgs,
        resumir=resumir,
        carregar=carregar,
        executar=executar,
        preparar=preparar,
    )
