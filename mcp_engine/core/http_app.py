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

import asyncio
import contextlib
import logging
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from mcp_engine.core.acoes_router import build_acoes_router
from mcp_engine.core.api_router import build_api_router
from mcp_engine.core.auth import (
    ANONYMOUS_PROFILE,
    current_user_var,
    set_current_user,
    trace_id_var,
)
from mcp_engine.core.health import tudo_ok, verificar_integridade
from mcp_engine.core.identidade import identidade_var, set_identidade
from mcp_engine.core.mcp_server import BRMEngine, build_server

logger = logging.getLogger(__name__)


def create_app(engine: BRMEngine | None = None) -> FastAPI:
    engine = engine or BRMEngine(
        client_id=os.getenv("CLIENT_ID", "example"),
        config_root=Path(os.getenv("CONFIG_ROOT", "clients")),
        data_root=Path(os.getenv("DATA_ROOT", "data")),
    )
    # build_server() é a mesma função usada pelo modo stdio — único ponto de
    # registro de tools para os dois transportes (ver spec 010).
    mcp_server = build_server(engine)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        parar = asyncio.Event()
        agendador = None
        if engine.gatilhos.habilitado and os.getenv("BRM_GATILHOS", "on").lower() != "off":
            agendador = asyncio.create_task(engine.gatilhos.rodar(parar=parar))
        try:
            yield
        finally:
            parar.set()
            if agendador is not None:
                await agendador

    # Aquece o índice do vault no startup: a primeira indexação carrega o modelo
    # de embeddings e, sem isso, o primeiro pedido do painel demora vários segundos.
    try:
        engine._vault_provider.reindex(engine.client_id)
    except Exception:
        pass

    app = FastAPI(title="BRM Engine (remoto)", lifespan=lifespan)

    # Dashboard React/Vite roda em outra origem (localhost:5173 em dev) e
    # autentica com o mesmo Bearer token do SQLUserStore — não há cookie/
    # sessão pra CSRF proteger. As origens permitidas vêm da configuração do
    # cliente (`cors_origins`); `*` só com `cors_permitir_qualquer: true`.
    if engine.cors_permitir_qualquer:
        logger.warning("CORS aberto a qualquer origem (cors_permitir_qualquer: true)")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if engine.cors_permitir_qualquer else list(engine.cors_origins),
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
        # O resolvedor tenta os provedores do cliente (spec 035): token local e/ou JWT do
        # produto. Credencial recusada é auditada e tratada como anônima.
        trace_token = trace_id_var.set(uuid.uuid4().hex)
        try:
            identidade = engine.identidade.resolver(request.headers.get("authorization"))
            open_request = request.method == "OPTIONS" or request.url.path == "/health"
            if identidade is None and not open_request:
                return JSONResponse({"detail": "credencial ausente ou inválida"}, status_code=401)
            token = set_current_user(identidade.perfil if identidade else ANONYMOUS_PROFILE)
            ident_token = set_identidade(identidade) if identidade else None
            try:
                return await call_next(request)
            finally:
                if ident_token is not None:
                    identidade_var.reset(ident_token)
                current_user_var.reset(token)
        finally:
            trace_id_var.reset(trace_token)

    # /api/* é a superfície REST do painel super_admin (dashboard React) —
    # ver mcp_engine/core/api_router.py. Precisa ser incluída ANTES do
    # mount("/", ...) abaixo: o mount captura qualquer path que não tenha
    # casado com uma rota anterior, então uma rota /api registrada depois
    # nunca seria alcançada.
    @app.get("/health")
    async def health():
        diretorios = [
            engine._data_root / engine.client_id,
            engine.user_store.db_path.parent,
        ]
        resultado = await asyncio.to_thread(verificar_integridade, diretorios)
        modulos = await asyncio.to_thread(engine.verificar_saude_modulos)
        if not tudo_ok(resultado) or not all(m["ok"] for m in modulos.values()):
            corpo = {"status": "degraded", "bancos": resultado}
            if modulos:
                corpo["modulos"] = modulos
            return JSONResponse(corpo, status_code=503)
        return {"status": "ok"}

    app.include_router(build_acoes_router(engine), prefix="/api/central")
    app.include_router(build_api_router(engine, mcp_server), prefix="/api")

    app.mount("/", mcp_server.sse_app())
    return app


# Alvo padrão para `uvicorn mcp_engine.core.http_app:app`.
app = create_app()
