"""Cadastro do CRP de um profissional na Agenda (tipo de ação, restrito ao gestor).

O CRP fica no cadastro do atendente da Agenda e é lido de lá ao emitir o contrato. O gestor o
informa pelo chat, confere na página e confirma com o PIN; o framework grava com ator e trace_id.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mcp_engine.core.acoes import ContextoAcao, Previa, TipoAcao
from mcp_engine.core.executor import ExecucaoFalhou
from mcp_engine.modules import ModuleHost
from mcp_engine.modules.agenda.provider import Profissional
from mcp_engine.modules.agenda.sincronizacao import _normalizar

_FORMATO = re.compile(r"^(CRP\s*)?\d{2}/\d{3,7}(-\d)?$", re.IGNORECASE)


class DefinirCrpArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profissional: str | None = Field(default=None, min_length=2, max_length=80)
    profissional_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    crp: str = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _regras(self) -> DefinirCrpArgs:
        if (self.profissional is None) == (self.profissional_id is None):
            raise ValueError("informe profissional ou profissional_id (um dos dois)")
        if not _FORMATO.match(self.crp.strip()):
            raise ValueError("CRP inválido (use um formato como 06/123456 ou CRP 06/123456)")
        return self


def _resolver(provider: Any, args: dict[str, Any]) -> Profissional:
    todos = provider.listar_profissionais()
    if args.get("profissional_id"):
        achados = [p for p in todos if p.id == args["profissional_id"]]
    else:
        alvo = _normalizar(args["profissional"])
        achados = [p for p in todos if _normalizar(p.nome) == alvo] or [
            p for p in todos if alvo in _normalizar(p.nome)
        ]
    if not achados:
        raise ExecucaoFalhou("Profissional não encontrado na Agenda.")
    if len({p.id for p in achados}) > 1:
        raise ExecucaoFalhou(
            "Mais de um profissional corresponde a esse nome; informe o nome completo."
        )
    return achados[0]


def tipo_definir_crp(host: ModuleHost, abrir_provider: Callable[[ModuleHost], Any]) -> TipoAcao:
    def resumir(args: dict[str, Any]) -> str:
        quem = args.get("profissional") or "o profissional informado"
        return f"Cadastrar o CRP {args['crp'].strip()} de {quem} na Agenda"

    def carregar(ctx: ContextoAcao, args: dict[str, Any]) -> Previa:
        with abrir_provider(host) as provider:
            profissional = _resolver(provider, args)
        dados = {"profissional": profissional.nome, "crp": args["crp"].strip().upper()}
        if profissional.crp:
            dados["crp_atual"] = profissional.crp
        return Previa(
            resumo=f"CRP {dados['crp']} para {profissional.nome}",
            dados=dados,
        )

    def executar(ctx: ContextoAcao, args: dict[str, Any]) -> dict[str, Any]:
        with abrir_provider(host) as provider:
            profissional = _resolver(provider, args)
            provider.definir_crp(
                profissional.id, args["crp"].strip(), ator=ctx.ator, trace_id=ctx.trace_id
            )
        return {"resumo": f"CRP de {profissional.nome} cadastrado na Agenda."}

    return TipoAcao(
        nome="definir_crp_profissional",
        titulo="Cadastrar CRP do profissional",
        descricao=(
            "Grava o CRP de um profissional no cadastro da Agenda (usado nos contratos). Informe"
            " `profissional` (nome) ou `profissional_id` e o `crp`, como 06/123456 ou"
            " CRP 06/123456. Restrito ao gestor."
        ),
        esquema=DefinirCrpArgs,
        resumir=resumir,
        carregar=carregar,
        executar=executar,
        papel_minimo="admin",
    )
