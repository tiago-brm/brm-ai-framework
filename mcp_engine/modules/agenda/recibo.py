"""Emissão do PDF do recibo de pagamento a partir do modelo do segundo cérebro.

A Agenda é dona do número, do valor por extenso e dos dados do recibo (CPF sempre mascarado). O
framework relê o recibo de um pagamento já confirmado e preenche o modelo `recibo-pagamento` do
vault (aprovado pelo gestor, com hash), guardando o PDF emitido. O texto do recibo vive no modelo,
não no código: mudar o texto é editar o modelo.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mcp_engine.core.acoes import CampoFaltante, ContextoAcao, Previa, TipoAcao
from mcp_engine.core.documentos import DocumentoErro
from mcp_engine.core.executor import ExecucaoFalhou
from mcp_engine.modules import ModuleHost
from mcp_engine.modules.agenda.pagamento import ROTULOS

MODELO_RECIBO = "recibo-pagamento"


def referencia_pagamento(pagamento_id: str) -> str:
    return f"agenda:pagamento:{pagamento_id}"


def _primeiro(corpo: Any, *chaves: str) -> Any:
    if not isinstance(corpo, dict):
        return None
    return next((corpo[c] for c in chaves if corpo.get(c) not in (None, "")), None)


def _centavos(valor: Any) -> int | None:
    try:
        return round(float(valor) * 100)
    except (TypeError, ValueError):
        return None


def _dia(valor: Any) -> str | None:
    texto = str(valor or "")[:10]
    try:
        return date.fromisoformat(texto).isoformat()
    except ValueError:
        return None


def _lista(valor: Any, *chaves: str) -> list[str]:
    itens = valor if isinstance(valor, list) else []
    return [
        str(_primeiro(i, *chaves) if isinstance(i, dict) else i)
        for i in itens
        if (_primeiro(i, *chaves) if isinstance(i, dict) else i)
    ]


def montar_fontes_recibo(recibo: dict[str, Any]) -> dict[str, Any]:
    pagador = _primeiro(recibo, "payer", "pagador")
    profissionais = []
    for p in recibo.get("professionals") or recibo.get("profissionais") or []:
        nome = _primeiro(p, "name", "nome")
        crp = _primeiro(p, "crp")
        if nome:
            profissionais.append(
                f"{nome} (CRP {str(crp).removeprefix('CRP').strip()})" if crp else nome
            )
    itens = _lista(recibo.get("items") or recibo.get("itens"), "description", "descricao", "name")
    forma = _primeiro(recibo, "method", "forma")
    return {
        "recibo": {
            "numero": _primeiro(recibo, "number", "receipt_number", "numero"),
            "valor": _centavos(_primeiro(recibo, "amount", "valor")),
            "extenso": _primeiro(recibo, "amount_in_words", "amount_words", "valor_por_extenso"),
            "data": _dia(_primeiro(recibo, "paid_at", "received_at", "issued_at", "date")),
            "forma": ROTULOS.get(str(forma), forma),
            "pagador_nome": _primeiro(pagador, "name", "nome"),
            "pagador_cpf": _primeiro(pagador, "cpf", "cpf_masked"),
            "profissionais": ", ".join(profissionais) or None,
            "descricao": "; ".join(itens) or "atendimento clínico",
            "registrado_por": _primeiro(recibo, "registered_by", "issued_by", "registrado_por"),
        }
    }


class EmitirReciboArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pagamento_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    modelo: str = Field(default=MODELO_RECIBO, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")


def tipo_emitir_recibo(host: ModuleHost, abrir_provider: Callable[[ModuleHost], Any]) -> TipoAcao:
    def ler(ctx: ContextoAcao, args: dict[str, Any]) -> dict[str, Any]:
        with abrir_provider(host) as provider:
            recibo = provider.recibo_pagamento(
                args["pagamento_id"], ator=ctx.ator, trace_id=ctx.trace_id
            )
        if recibo is None:
            raise ExecucaoFalhou(
                "Este pagamento não tem recibo (ainda aguarda conferência da gerência) ou não"
                " existe na Agenda."
            )
        return montar_fontes_recibo(recibo)

    def resumir(args: dict[str, Any]) -> str:
        return f"Emitir o PDF do recibo do pagamento {args['pagamento_id']} ({args['modelo']})"

    def carregar(ctx: ContextoAcao, args: dict[str, Any]) -> Previa:
        fontes = ler(ctx, args)
        try:
            linhas = ctx.documentos.previa(args["modelo"], fontes)
        except DocumentoErro as exc:
            raise ExecucaoFalhou(str(exc)) from None
        faltantes = tuple(
            CampoFaltante(
                nome=linha["campo"], rotulo=linha["rotulo"], tipo=linha["tipo"], editavel=False
            )
            for linha in linhas
            if linha["faltante"] or linha.get("invalido")
        )
        recibo = fontes["recibo"]
        return Previa(
            resumo=f"Recibo nº {recibo['numero']} de {recibo['pagador_nome']}",
            dados={"modelo": args["modelo"], "campos": linhas},
            faltantes=faltantes,
        )

    def executar(ctx: ContextoAcao, args: dict[str, Any]) -> dict[str, Any]:
        fontes = ler(ctx, args)
        try:
            doc = ctx.emitir_documento(
                args["modelo"],
                fontes,
                referencia=referencia_pagamento(args["pagamento_id"]),
                metadados={"pagamento_id": args["pagamento_id"]},
            )
        except DocumentoErro as exc:
            raise ExecucaoFalhou(str(exc)) from None
        return {"resumo": "Recibo emitido.", "documento_id": doc.id, "sha256": doc.sha256}

    return TipoAcao(
        nome="emitir_recibo_pagamento",
        titulo="Emitir recibo de pagamento",
        descricao=(
            "Gera o PDF do recibo de um pagamento já confirmado na Agenda, a partir do modelo"
            " aprovado do segundo cérebro (recibo-pagamento) e dos dados do recibo da Agenda."
            " Informe o `pagamento_id` devolvido ao registrar ou confirmar o pagamento."
        ),
        esquema=EmitirReciboArgs,
        resumir=resumir,
        carregar=carregar,
        executar=executar,
    )
