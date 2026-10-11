"""Gera as páginas de God Mode em `vault/sistema/` (spec 030 e 025).

PYTHONPATH=. uv run python scripts/gerar_mapa_do_sistema.py <cliente>

Escreve estado.md, matriz.md, mapa-de-cobertura.md e alertas.md. São
somente leitura no caderno: o motor as restaura se alguém editar à mão.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mcp_engine.core.mcp_server import BRMEngine


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cliente")
    ap.add_argument("--config-root", default=os.getenv("CONFIG_ROOT", "clients"))
    ap.add_argument("--data-root", default=os.getenv("DATA_ROOT", "data"))
    args = ap.parse_args(argv)
    engine = BRMEngine(args.cliente, Path(args.config_root), Path(args.data_root))
    resultado = engine.caderno.atualizar_paginas()
    for pagina, estado in resultado.items():
        print(f"sistema/{pagina}.md: {estado}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
