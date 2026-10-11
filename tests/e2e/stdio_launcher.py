"""Sobe o servidor stdio real (`mcp_server.main`) com os handlers do cliente de avaliação.

Handlers nativos vivem no processo do motor; como o stdio roda em outro
processo, este lançador os registra logo após a construção do BRMEngine e
delega para o main() de produção.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals.fixtures.handlers import registrar  # noqa: E402
from mcp_engine.core import mcp_server  # noqa: E402

_init = mcp_server.BRMEngine.__init__


def _init_com_handlers(self, *args, **kwargs):
    _init(self, *args, **kwargs)
    registrar(self)


mcp_server.BRMEngine.__init__ = _init_com_handlers

if __name__ == "__main__":
    mcp_server.main()
