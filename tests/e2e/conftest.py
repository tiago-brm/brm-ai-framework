from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.sse import sse_client

from evals.ambiente import CLIENT_ID, Ambiente, montar
from mcp_engine.core.http_app import create_app

RAIZ = Path(__file__).resolve().parents[2]
LANCADOR = Path(__file__).parent / "stdio_launcher.py"


@pytest.fixture
def ambiente(tmp_path: Path) -> Ambiente:
    return montar(tmp_path)


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def servidor_http(ambiente: Ambiente) -> Iterator[str]:
    app = create_app(ambiente.engine)
    porta = _porta_livre()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=porta,
        log_level="warning",
        timeout_graceful_shutdown=1,
    )
    servidor = uvicorn.Server(config)
    thread = threading.Thread(target=servidor.run, daemon=True)
    thread.start()
    limite = time.monotonic() + 10
    while not servidor.started:
        if time.monotonic() > limite or not thread.is_alive():
            raise RuntimeError("uvicorn não subiu")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{porta}"
    finally:
        servidor.should_exit = True
        thread.join(timeout=10)


def resultado(res: Any) -> dict[str, Any]:
    if getattr(res, "structuredContent", None):
        dados = res.structuredContent
        return dados.get("result", dados) if set(dados) == {"result"} else dados
    texto = "".join(b.text for b in res.content if getattr(b, "text", None))
    try:
        return json.loads(texto)
    except ValueError:
        return {"texto": texto, "erro": bool(res.isError)}


@asynccontextmanager
async def sessao_http(base: str, token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with sse_client(f"{base}/sse", headers=headers, timeout=5) as (leitura, escrita):
        async with ClientSession(leitura, escrita) as sessao:
            await sessao.initialize()
            yield sessao


def rodar(coro_fn: Callable[[], Any], timeout: float = 20) -> Any:
    async def _com_limite():
        return await asyncio.wait_for(coro_fn(), timeout)

    return asyncio.run(_com_limite())


def eventos(ambiente: Ambiente) -> list[Any]:
    return list(ambiente.engine._audit_sink.iter_events(CLIENT_ID))
