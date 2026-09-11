import asyncio
import json
from pathlib import Path

import pytest

from audit.sink import FileSystemAuditSink
from mcp_engine.core.auth import Role, UserProfile, current_user_var, set_current_user
from mcp_engine.core.rbac_server import RBACFastMCP
from mcp_engine.core.user_store import SQLUserStore


@pytest.fixture(autouse=True)
def _reset_current_user():
    token = current_user_var.set(None)
    yield
    current_user_var.reset(token)


@pytest.fixture
def server(tmp_path: Path) -> RBACFastMCP:
    audit_sink = FileSystemAuditSink(tmp_path / "data")
    server = RBACFastMCP("test-server", audit_sink=audit_sink, client_id="example")

    @server.tool()
    def consultar_dados() -> dict:
        return {"allowed": True, "data": ["x"]}

    @server.tool()
    def deletar_banco() -> dict:
        return {"allowed": True, "result": "apagado"}

    @server.tool()
    def vault_search(query: str) -> dict:
        return {"allowed": True, "results": []}

    return server


@pytest.fixture
def server_with_store(tmp_path: Path) -> tuple[RBACFastMCP, SQLUserStore]:
    audit_sink = FileSystemAuditSink(tmp_path / "data")
    store = SQLUserStore(tmp_path, "example")
    server = RBACFastMCP(
        "test-server", audit_sink=audit_sink, client_id="example", user_store=store
    )

    @server.tool()
    def vault_search(query: str) -> dict:
        return {"allowed": True, "results": []}

    return server, store


def _run(coro):
    return asyncio.run(coro)


def _content_json(result):
    # A successful call_tool() goes through FastMCP's normal dispatch and
    # comes back as MCP content blocks (JSON-serialized text), not a raw
    # dict — only our RBAC denial path returns a raw dict directly (see
    # RBACFastMCP.call_tool), since it short-circuits before delegating to
    # the real tool.
    return json.loads(result[0].text)


class TestListToolsFiltering:
    def test_viewer_sees_only_unrestricted_and_own_tools(self, server: RBACFastMCP) -> None:
        set_current_user(UserProfile(identity="estagiario@escritorio.com.br", role=Role.VIEWER))

        names = {t.name for t in _run(server.list_tools())}

        assert names == {"consultar_dados", "vault_search"}

    def test_admin_sees_all_tools(self, server: RBACFastMCP) -> None:
        set_current_user(UserProfile(identity="socio@escritorio.com.br", role=Role.ADMIN))

        names = {t.name for t in _run(server.list_tools())}

        assert names == {"consultar_dados", "deletar_banco", "vault_search"}

    def test_no_identity_set_defaults_to_viewer_filtering(self, server: RBACFastMCP) -> None:
        names = {t.name for t in _run(server.list_tools())}

        assert "deletar_banco" not in names

    def test_super_admin_sees_all_tools(self, server: RBACFastMCP) -> None:
        set_current_user(UserProfile(identity="brm@brm.com.br", role=Role.SUPER_ADMIN))

        names = {t.name for t in _run(server.list_tools())}

        assert names == {"consultar_dados", "deletar_banco", "vault_search"}


class TestCallToolEnforcement:
    def test_viewer_calling_deletar_banco_directly_is_denied(
        self, server: RBACFastMCP, tmp_path: Path
    ) -> None:
        set_current_user(UserProfile(identity="estagiario@escritorio.com.br", role=Role.VIEWER))

        result = _run(server.call_tool("deletar_banco", {}))

        assert result == {
            "allowed": False,
            "reason": "Acesso negado: perfil 'viewer' não pode executar 'deletar_banco'.",
        }

    def test_denied_call_is_audited(self, server: RBACFastMCP) -> None:
        set_current_user(UserProfile(identity="estagiario@escritorio.com.br", role=Role.VIEWER))

        _run(server.call_tool("deletar_banco", {}))

        events = list(server._audit_sink.iter_events("example"))
        assert len(events) == 1
        assert events[0].action == "deletar_banco"
        assert events[0].actor == "estagiario@escritorio.com.br"
        assert events[0].decision.value == "denied"

    def test_admin_calling_deletar_banco_executes(self, server: RBACFastMCP) -> None:
        set_current_user(UserProfile(identity="socio@escritorio.com.br", role=Role.ADMIN))

        result = _run(server.call_tool("deletar_banco", {}))

        assert _content_json(result) == {"allowed": True, "result": "apagado"}

    def test_super_admin_calling_deletar_banco_executes(self, server: RBACFastMCP) -> None:
        set_current_user(UserProfile(identity="brm@brm.com.br", role=Role.SUPER_ADMIN))

        result = _run(server.call_tool("deletar_banco", {}))

        assert _content_json(result) == {"allowed": True, "result": "apagado"}

    def test_unrestricted_tool_callable_by_viewer(self, server: RBACFastMCP) -> None:
        set_current_user(UserProfile(identity="estagiario@escritorio.com.br", role=Role.VIEWER))

        result = _run(server.call_tool("vault_search", {"query": "x"}))

        assert _content_json(result) == {"allowed": True, "results": []}


class TestPersistedPermissions:
    def test_toggling_permission_takes_effect_without_restart(
        self, server_with_store: tuple[RBACFastMCP, SQLUserStore]
    ) -> None:
        server, store = server_with_store
        set_current_user(UserProfile(identity="estagiario@escritorio.com.br", role=Role.VIEWER))

        # vault_search não tem linha nenhuma ainda -> aberta a todo mundo.
        assert "vault_search" in {t.name for t in _run(server.list_tools())}

        # super_admin liga vault_search só pra admin, em runtime.
        store.set_permission(Role.ADMIN, "vault_search", True)

        assert "vault_search" not in {t.name for t in _run(server.list_tools())}
        result = _run(server.call_tool("vault_search", {"query": "x"}))
        assert result["allowed"] is False

        set_current_user(UserProfile(identity="socio@escritorio.com.br", role=Role.ADMIN))
        assert "vault_search" in {t.name for t in _run(server.list_tools())}
