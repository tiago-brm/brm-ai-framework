"""Agendador de gatilhos para implantações só-stdio (spec 031).

No servidor HTTP o agendador sobe sozinho com o app (`create_app`). Quando o motor roda só por
stdio, mantenha este processo ativo ao lado dele:

    uv run python scripts/rodar_gatilhos.py <cliente>
    uv run python scripts/rodar_gatilhos.py <cliente> --uma-vez      # um passo e sai
    uv run python scripts/rodar_gatilhos.py <cliente> --listar
    uv run python scripts/rodar_gatilhos.py <cliente> --pausar       # pausa todos os gatilhos
    uv run python scripts/rodar_gatilhos.py <cliente> --retomar
    uv run python scripts/rodar_gatilhos.py <cliente> --simular <gatilho>
    uv run python scripts/rodar_gatilhos.py <cliente> --reprocessar <gatilho> --janela <janela>

Vários agendadores no mesmo cliente são seguros: cada janela tem chave única em `state.db`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

from mcp_engine.core.mcp_server import BRMEngine

OPERADOR = "cli:rodar_gatilhos"


def criar_motor(args: argparse.Namespace) -> BRMEngine:
    return BRMEngine(args.cliente, Path(args.config_root), Path(args.data_root))


async def _laco(engine: BRMEngine, intervalo: float) -> None:
    parar = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sinal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sinal, parar.set)
    print(f"agendador de gatilhos ativo para {engine.client_id} (Ctrl+C para sair)")
    await engine.gatilhos.rodar(intervalo=intervalo, parar=parar)


def principal(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Agendador de gatilhos (spec 031)")
    ap.add_argument("cliente")
    ap.add_argument("--config-root", default=os.getenv("CONFIG_ROOT", "clients"))
    ap.add_argument("--data-root", default=os.getenv("DATA_ROOT", "data"))
    ap.add_argument("--intervalo", type=float, default=1.0)
    ap.add_argument("--uma-vez", action="store_true")
    ap.add_argument("--listar", action="store_true")
    ap.add_argument("--pausar", action="store_true")
    ap.add_argument("--retomar", action="store_true")
    ap.add_argument("--simular", metavar="GATILHO")
    ap.add_argument("--reprocessar", metavar="GATILHO")
    ap.add_argument("--janela")
    args = ap.parse_args(argv)
    engine = criar_motor(args)
    gatilhos = engine.gatilhos
    saida: Any
    if args.pausar or args.retomar:
        gatilhos.pausar(args.pausar, OPERADOR)
        print("gatilhos pausados" if args.pausar else "gatilhos retomados")
        return 0
    if args.listar:
        painel = gatilhos.painel()
        for g in painel["gatilhos"]:
            estado = "ativo" if g["ativo"] else f"desativado ({g['motivo_desativacao']})"
            print(
                f"{g['id']}: {estado}; próxima {g['proxima_execucao']}; última {g['ultimo_status']}"
            )
        print(f"pausado: {painel['pausado']}; agendador ativo: {painel['agendador_ativo']}")
        return 0
    if args.simular:
        saida = gatilhos.simular(config_id=args.simular)
        print(json.dumps(saida, ensure_ascii=False, indent=2, default=str))
        return 0 if saida.get("allowed", True) else 1
    if args.reprocessar:
        if not args.janela:
            print("informe --janela")
            return 2
        saida = gatilhos.reprocessar(args.reprocessar, args.janela, OPERADOR)
        print(json.dumps(saida, ensure_ascii=False, default=str))
        return 0 if saida.get("status") != "janela_desconhecida" else 1
    if not gatilhos.habilitado:
        print("este cliente não usa configuração via notas ou desligou os gatilhos")
        return 1
    engine.retomar_operacoes_orfas()
    if args.uma_vez:
        print(json.dumps(gatilhos.tick(), ensure_ascii=False))
        return 0
    try:
        asyncio.run(_laco(engine, args.intervalo))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(principal())
