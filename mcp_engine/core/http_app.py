"""Entry point remoto (SSE via FastAPI) do BRM Engine.

--- Modo SSE/HTTP (remoto) -------------------------------------------------
Ao contrário do modo stdio (mcp_engine/core/mcp_server.py::main — um
processo, um usuário, identidade lida uma vez do ambiente), aqui um único
processo atende N conexões concorrentes, cada uma potencialmente de um
usuário diferente. Por isso a identidade é resolvida A CADA REQUEST, pelo
middleware abaixo, nunca uma vez só no import do módulo.

A conexão MCP em si é `GET /sse` — uma requisição HTTP de longa duração
que abre o event stream; mensagens JSON-RPC (tools/list, tools/call) chegam
depois via `POST /messages/?session_id=...` e são processadas dentro da
task da conexão original. O middleware roda em toda requisição que passa
pela app (incluindo o `GET /sse` inicial), lê o header `Authorization` e
popula o `ContextVar` de identidade (mcp_engine.core.auth) só para a
duração daquele request — o que, para o handshake do `GET /sse`, cobre a
sessão inteira, já que `contextvars` propaga para as tasks filhas que
processam as mensagens seguintes.

Como rodar:
    CLIENT_ID=example CONFIG_ROOT=clients DATA_ROOT=data \\
        uv run uvicorn mcp_engine.core.http_app:app --host 0.0.0.0 --port 8000

Como autenticar (na conexão inicial; usuários e tokens vêm do
`SQLUserStore` do cliente — clients/<cliente>/config/users.db, ver
mcp_engine/core/user_store.py — popular com
`uv run python -m mcp_engine.core.user_store`):
    curl -N -H "Authorization: Bearer admin-token-demo" \\
        http://localhost:8000/sse
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from mcp_engine.core.api_router import build_api_router
from mcp_engine.core.auth import current_user_var, resolve_http_identity, set_current_user
from mcp_engine.core.mcp_server import BRMEngine, build_server


def create_app(engine: BRMEngine | None = None) -> FastAPI:
    engine = engine or BRMEngine(
        client_id=os.getenv("CLIENT_ID", "example"),
        config_root=Path(os.getenv("CONFIG_ROOT", "clients")),
        data_root=Path(os.getenv("DATA_ROOT", "data")),
    )
    # build_server() é a mesma função usada pelo modo stdio — único ponto de
    # registro de tools para os dois transportes (ver spec 010).
    mcp_server = build_server(engine)

    app = FastAPI(title="BRM Engine (remoto)")

    # Dashboard React/Vite roda em outra origem (localhost:5173 em dev) e
    # autentica com o mesmo Bearer token do SQLUserStore — não há cookie/
    # sessão pra CSRF proteger. allow_origins=["*"] é aceitável no estágio
    # atual (Fase 1, local-first, sem exposição pública — ver docs/spec.md);
    # revisitar quando isso for exposto fora de localhost.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def identity_middleware(request: Request, call_next):
        # Única linha de todo o app ciente de "HTTP" — resolve o perfil do
        # header desta requisição (consultando o SQLUserStore do cliente,
        # engine.user_store) e o disponibiliza via get_current_user() para
        # o resto do código (RBACFastMCP, tools, rotas /api), que não sabe e
        # não precisa saber que a identidade veio de um header.
        profile = resolve_http_identity(engine.user_store, request.headers.get("authorization"))
        token = set_current_user(profile)
        try:
            return await call_next(request)
        finally:
            current_user_var.reset(token)

    # /api/* é a superfície REST do painel super_admin (dashboard React) —
    # ver mcp_engine/core/api_router.py. Precisa ser incluída ANTES do
    # mount("/", ...) abaixo: o mount captura qualquer path que não tenha
    # casado com uma rota anterior, então uma rota /api registrada depois
    # nunca seria alcançada.
    app.include_router(build_api_router(engine, mcp_server), prefix="/api")

    app.mount("/", mcp_server.sse_app())
    return app


# Alvo padrão para `uvicorn mcp_engine.core.http_app:app`.
app = create_app()
