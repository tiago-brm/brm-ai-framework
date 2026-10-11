from __future__ import annotations

from typing import Any

from mcp_engine.core.executor import HandlerContext


def _eco(nome: str):
    def handler(ctx: HandlerContext, args: dict[str, Any]) -> dict[str, Any]:
        return {"handler": nome, "executado_por": ctx.actor, "argumentos": dict(args)}

    return handler


HANDLERS = {
    "av.consultar_saldo": _eco("consultar_saldo"),
    "av.registrar_recebimento": _eco("registrar_recebimento"),
    "av.agendar_atendimento": _eco("agendar_atendimento"),
    "av.registrar_observacao": _eco("registrar_observacao"),
    "av.estornar_lancamento": _eco("estornar_lancamento"),
}


def registrar(engine: Any) -> None:
    for nome, handler in HANDLERS.items():
        engine.handlers.register(nome, handler)
