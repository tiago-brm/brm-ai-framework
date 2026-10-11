from __future__ import annotations

import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.fixtures.handlers import registrar
from mcp_engine.core.auth import Role, UserProfile, current_user_var, set_current_user
from mcp_engine.core.mcp_server import BRMEngine, _server_instructions, build_server
from mcp_engine.core.user_store import SQLUserStore

CLIENT_ID = "avaliacao"
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "clients"

USUARIOS: dict[str, tuple[str, Role]] = {
    "viewer": ("ana@avaliacao.test", Role.VIEWER),
    "admin": ("bruno@avaliacao.test", Role.ADMIN),
    "admin2": ("caio@avaliacao.test", Role.ADMIN),
    "super_admin": ("helena@avaliacao.test", Role.SUPER_ADMIN),
}

FERRAMENTAS_VIEWER = frozenset(
    {
        "consultar_saldo",
        "registrar_recebimento",
        "agendar_atendimento",
        "registrar_observacao",
        "confirmar_operacao",
        "cancelar_operacao",
        "listar_operacoes",
        "vault_search",
        "vault_list_notes",
        "vault_get_related",
    }
)
FERRAMENTAS_ADMIN = FERRAMENTAS_VIEWER | frozenset(
    {"estornar_lancamento", "aprovar_operacao", "rejeitar_operacao", "query_audit_log"}
)


@dataclass
class Ambiente:
    engine: BRMEngine
    config_root: Path
    data_root: Path
    tokens: dict[str, str]

    @property
    def server(self) -> Any:
        return self._server

    def perfil(self, chave: str) -> UserProfile:
        email, role = USUARIOS[chave]
        return UserProfile(identity=email, role=role)

    def ferramentas(self, chave: str) -> list[dict[str, Any]]:
        """Ferramentas visíveis ao perfil, no formato de tool use da Anthropic."""
        import asyncio

        token = set_current_user(self.perfil(chave))
        try:
            tools = asyncio.run(self._server.list_tools())
        finally:
            current_user_var.reset(token)
        return [
            {
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.inputSchema,
            }
            for t in tools
        ]

    def instrucoes(self) -> str:
        return _server_instructions(self.engine)


def montar(raiz: Path) -> Ambiente:
    config_root = raiz / "config"
    data_root = raiz / "data"
    shutil.copytree(FIXTURE_ROOT, config_root)

    engine = BRMEngine(CLIENT_ID, config_root, data_root)
    registrar(engine)

    store: SQLUserStore = engine.user_store
    tokens: dict[str, str] = {}
    for chave, (email, role) in USUARIOS.items():
        tokens[chave] = secrets.token_urlsafe(24)
        store.create_user(email, role, tokens[chave])

    server = build_server(engine)
    nomes = [t.name for t in server._tool_manager.list_tools()]
    for nome in nomes:
        store.set_permission(Role.VIEWER, nome, nome in FERRAMENTAS_VIEWER)
        store.set_permission(Role.ADMIN, nome, nome in FERRAMENTAS_ADMIN)

    ambiente = Ambiente(engine, config_root, data_root, tokens)
    ambiente._server = server
    return ambiente
