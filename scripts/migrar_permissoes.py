"""Semeia permissões explícitas para tools sem nenhuma linha (spec 020).

Com `rbac_default: deny`, uma tool sem permissão é negada a viewer e admin.
Este script preserva o comportamento anterior de um cliente existente
(antes "aberto por omissão") criando as linhas de forma visível e editável.

    uv run python scripts/migrar_permissoes.py <cliente> [<cliente> ...]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from mcp_engine.core.mcp_server import BRMEngine, build_server


def migrar(client_id: str, config_root: Path, data_root: Path) -> list[str]:
    engine = BRMEngine(client_id=client_id, config_root=config_root, data_root=data_root)
    server = build_server(engine)
    names = [t.name for t in server._tool_manager.list_tools()]
    return engine.user_store.seed_open_tools(names)


def main() -> None:
    clientes = sys.argv[1:]
    if not clientes:
        sys.exit("uso: migrar_permissoes.py <cliente> [<cliente> ...]")
    config_root = Path(os.getenv("CONFIG_ROOT", "clients"))
    data_root = Path(os.getenv("DATA_ROOT", "data"))
    for client_id in clientes:
        semeadas = migrar(client_id, config_root, data_root)
        print(f"{client_id}: {len(semeadas)} tool(s) liberadas explicitamente")
        for name in semeadas:
            print(f"  - {name}")


if __name__ == "__main__":
    main()
