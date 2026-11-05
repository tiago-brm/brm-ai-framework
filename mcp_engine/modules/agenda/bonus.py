"""Bônus de indicação por aptidão na Agenda (ações restritas ao gestor).

A regra é da Agenda: o +10 só vale com profissional apto, paciente novo (primeira sessão a partir
da data de corte) e indicação para aquele profissional. O framework só lê a política e a aptidão e
grava as duas decisões do gestor (data de corte e aptidão de cada profissional), sempre depois da
confirmação com PIN e com ator e trace_id. Perder a aptidão afeta só as sessões futuras.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mcp_engine.core.acoes import ContextoAcao, Previa, TipoAcao
from mcp_engine.core.executor import ExecucaoFalhou
from mcp_engine.modules import ModuleHost
from mcp_engine.modules.agenda.crp import _resolver
from mcp_engine.modules.agenda.tempo import parse_dia


class DefinirCorteBonusArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_corte: str | None = Field(default=None, min_length=10, max_length=10)


def _corte(texto: str | None) -> date | None:
    if texto is None:
        return None
    try:
        return parse_dia(texto, date.today())
    except ValueError:
        raise ExecucaoFalhou("Data inválida (use AAAA-MM-DD).") from None


def tipo_definir_corte_bonus(
    host: ModuleHost, abrir_provider: Callable[[ModuleHost], Any]
) -> TipoAcao:
    def resumir(args: dict[str, Any]) -> str:
        if args.get("data_corte"):
            return f"Bônus de indicação só para pacientes novos a partir de {args['data_corte']}"
        return "Desligar o bônus de indicação por aptidão (sem data de corte)"

    def carregar(ctx: ContextoAcao, args: dict[str, Any]) -> Previa:
        corte = _corte(args.get("data_corte"))
        with abrir_provider(host) as provider:
            atual = provider.politica_bonus_indicacao().get("referral_bonus_new_patients_from")
        dados: dict[str, Any] = {
            "data_corte": f"{corte:%d/%m/%Y}" if corte else "sem data (desligado)",
            "data_corte_atual": atual or "sem data (desligado)",
            "aviso": (
                "O +10 só vale com profissional apto, paciente novo (primeira sessão a partir"
                " da data de corte) e indicação para aquele profissional."
            ),
        }
        return Previa(resumo=resumir(args), dados=dados)

    def executar(ctx: ContextoAcao, args: dict[str, Any]) -> dict[str, Any]:
        corte = _corte(args.get("data_corte"))
        with abrir_provider(host) as provider:
            provider.definir_corte_bonus_indicacao(corte, ator=ctx.ator, trace_id=ctx.trace_id)
        return {
            "resumo": (
                f"Data de corte do bônus de indicação definida para {corte:%d/%m/%Y}."
                if corte
                else "Bônus de indicação por aptidão desligado (sem data de corte)."
            )
        }

    return TipoAcao(
        nome="definir_corte_bonus_indicacao",
        titulo="Definir a data de corte do bônus de indicação",
        descricao=(
            "Define na Agenda a data a partir da qual o bônus de indicação vale para pacientes"
            " novos (`data_corte`, AAAA-MM-DD); sem `data_corte` desliga o bônus. Restrito ao"
            " gestor."
        ),
        esquema=DefinirCorteBonusArgs,
        resumir=resumir,
        carregar=carregar,
        executar=executar,
        papel_minimo="admin",
    )


class DefinirAptidaoBonusArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profissional: str | None = Field(default=None, min_length=2, max_length=80)
    profissional_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    apto: bool

    @model_validator(mode="after")
    def _regras(self) -> DefinirAptidaoBonusArgs:
        if (self.profissional is None) == (self.profissional_id is None):
            raise ValueError("informe profissional ou profissional_id (um dos dois)")
        return self


def tipo_definir_aptidao_bonus(
    host: ModuleHost, abrir_provider: Callable[[ModuleHost], Any]
) -> TipoAcao:
    def resumir(args: dict[str, Any]) -> str:
        quem = args.get("profissional") or "o profissional informado"
        verbo = "Tornar" if args["apto"] else "Deixar de tornar"
        return f"{verbo} {quem} apto ao bônus de indicação"

    def carregar(ctx: ContextoAcao, args: dict[str, Any]) -> Previa:
        with abrir_provider(host) as provider:
            profissional = _resolver(provider, args)
            situacao = next(
                (
                    s
                    for s in provider.aptidao_bonus_indicacao()
                    if str(s.get("id") or s.get("staff_id")) == profissional.id
                ),
                {},
            )
        dados: dict[str, Any] = {
            "profissional": profissional.nome,
            "apto_depois": "sim" if args["apto"] else "não",
        }
        if "referral_bonus_eligible" in situacao:
            dados["apto_agora"] = "sim" if situacao["referral_bonus_eligible"] else "não"
        if situacao.get("patients_6m_count") is not None:
            dados["pacientes_6_meses"] = f"{situacao['patients_6m_count']} (meta 10)"
        if not args["apto"]:
            dados["aviso"] = "Perder a aptidão afeta só as sessões futuras."
        return Previa(resumo=resumir({**args, "profissional": profissional.nome}), dados=dados)

    def executar(ctx: ContextoAcao, args: dict[str, Any]) -> dict[str, Any]:
        with abrir_provider(host) as provider:
            profissional = _resolver(provider, args)
            provider.definir_aptidao_bonus_indicacao(
                profissional.id, args["apto"], ator=ctx.ator, trace_id=ctx.trace_id
            )
        estado = "apto" if args["apto"] else "não apto"
        return {"resumo": f"{profissional.nome} agora está {estado} ao bônus de indicação."}

    return TipoAcao(
        nome="definir_aptidao_bonus_indicacao",
        titulo="Definir a aptidão de um profissional ao bônus de indicação",
        descricao=(
            "Marca na Agenda se um profissional está apto ao bônus de indicação. Informe"
            " `profissional` (nome) ou `profissional_id` e `apto` (verdadeiro ou falso). Perder a"
            " aptidão afeta só sessões futuras. Restrito ao gestor."
        ),
        esquema=DefinirAptidaoBonusArgs,
        resumir=resumir,
        carregar=carregar,
        executar=executar,
        papel_minimo="admin",
    )
