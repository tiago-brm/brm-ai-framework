from pathlib import Path

import pytest

from mcp_engine.core.auth import (
    ANONYMOUS_PROFILE,
    Role,
    UserProfile,
    is_tool_allowed,
    resolve_http_identity,
    resolve_stdio_identity,
)
from mcp_engine.core.user_store import SQLUserStore, seed_demo_users


@pytest.fixture
def store(tmp_path: Path) -> SQLUserStore:
    store = SQLUserStore(tmp_path, "example")
    seed_demo_users(store)
    return store


class TestResolveStdioIdentity:
    def test_resolves_admin_by_email(
        self, store: SQLUserStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_USER_EMAIL", "socio@escritorio.com.br")
        monkeypatch.delenv("MCP_API_TOKEN", raising=False)

        profile = resolve_stdio_identity(store)

        assert profile.role is Role.ADMIN
        assert profile.identity == "socio@escritorio.com.br"

    def test_resolves_viewer_by_email(
        self, store: SQLUserStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_USER_EMAIL", "estagiario@escritorio.com.br")
        monkeypatch.delenv("MCP_API_TOKEN", raising=False)

        profile = resolve_stdio_identity(store)

        assert profile.role is Role.VIEWER

    def test_resolves_super_admin_by_email(
        self, store: SQLUserStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_USER_EMAIL", "brm@brm.com.br")
        monkeypatch.delenv("MCP_API_TOKEN", raising=False)

        profile = resolve_stdio_identity(store)

        assert profile.role is Role.SUPER_ADMIN

    def test_resolves_by_token_when_email_absent(
        self, store: SQLUserStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MCP_USER_EMAIL", raising=False)
        monkeypatch.setenv("MCP_API_TOKEN", "admin-token-demo")

        profile = resolve_stdio_identity(store)

        assert profile.role is Role.ADMIN

    def test_unknown_email_falls_back_to_anonymous(
        self, store: SQLUserStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCP_USER_EMAIL", "ninguem@fora.com")
        monkeypatch.delenv("MCP_API_TOKEN", raising=False)

        profile = resolve_stdio_identity(store)

        assert profile == ANONYMOUS_PROFILE
        assert profile.role is Role.VIEWER

    def test_no_credentials_falls_back_to_anonymous(
        self, store: SQLUserStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MCP_USER_EMAIL", raising=False)
        monkeypatch.delenv("MCP_API_TOKEN", raising=False)

        assert resolve_stdio_identity(store) == ANONYMOUS_PROFILE


class TestResolveHttpIdentity:
    def test_resolves_admin_by_bearer_token(self, store: SQLUserStore) -> None:
        profile = resolve_http_identity(store, "Bearer admin-token-demo")
        assert profile.role is Role.ADMIN

    def test_resolves_viewer_by_bearer_token(self, store: SQLUserStore) -> None:
        profile = resolve_http_identity(store, "Bearer viewer-token-demo")
        assert profile.role is Role.VIEWER

    def test_resolves_super_admin_by_bearer_token(self, store: SQLUserStore) -> None:
        profile = resolve_http_identity(store, "Bearer super-admin-token-demo")
        assert profile.role is Role.SUPER_ADMIN

    def test_missing_header_falls_back_to_anonymous(self, store: SQLUserStore) -> None:
        assert resolve_http_identity(store, None) == ANONYMOUS_PROFILE

    def test_unknown_token_falls_back_to_anonymous(self, store: SQLUserStore) -> None:
        assert resolve_http_identity(store, "Bearer does-not-exist") == ANONYMOUS_PROFILE

    def test_non_bearer_scheme_falls_back_to_anonymous(self, store: SQLUserStore) -> None:
        assert resolve_http_identity(store, "Basic dXNlcjpwYXNz") == ANONYMOUS_PROFILE

    def test_malformed_header_falls_back_to_anonymous(self, store: SQLUserStore) -> None:
        assert resolve_http_identity(store, "admin-token-demo") == ANONYMOUS_PROFILE


class TestIsToolAllowed:
    def test_viewer_can_call_consultar_dados(self) -> None:
        assert is_tool_allowed(Role.VIEWER, "consultar_dados") is True

    def test_viewer_cannot_call_deletar_banco(self) -> None:
        assert is_tool_allowed(Role.VIEWER, "deletar_banco") is False

    def test_viewer_cannot_manage_users(self) -> None:
        assert is_tool_allowed(Role.VIEWER, "criar_usuario") is False
        assert is_tool_allowed(Role.VIEWER, "listar_usuarios") is False

    def test_admin_can_call_all_restricted_tools(self) -> None:
        for tool in ("consultar_dados", "deletar_banco", "criar_usuario", "listar_usuarios"):
            assert is_tool_allowed(Role.ADMIN, tool) is True

    def test_unrestricted_tool_allowed_for_any_role(self) -> None:
        assert is_tool_allowed(Role.VIEWER, "vault_search") is True
        assert is_tool_allowed(Role.ADMIN, "vault_search") is True

    def test_super_admin_can_call_all_restricted_tools(self) -> None:
        for tool in (
            "consultar_dados",
            "deletar_banco",
            "criar_usuario",
            "listar_usuarios",
            "atualizar_perfil_usuario",
        ):
            assert is_tool_allowed(Role.SUPER_ADMIN, tool) is True

    def test_super_admin_bypasses_even_unregistered_restricted_tool(self) -> None:
        # super_admin não depende de uma entrada explícita em ROLE_TOOLS —
        # o bypass em is_tool_allowed é incondicional, então cobre tools
        # restritas futuras que ainda não ganharam entrada no dict.
        assert is_tool_allowed(Role.SUPER_ADMIN, "tool_que_nao_existe_ainda") is True

    def test_defers_to_store_when_provided(self, store: SQLUserStore) -> None:
        # Com store, a decisão vem de tool_permissions (editável em
        # runtime), não do dict estático ROLE_TOOLS.
        store.set_permission(Role.ADMIN, "vault_search", True)

        assert is_tool_allowed(Role.ADMIN, "vault_search", store) is True
        assert is_tool_allowed(Role.VIEWER, "vault_search", store) is False

    def test_super_admin_bypasses_store_too(self, store: SQLUserStore) -> None:
        store.set_permission(Role.ADMIN, "vault_search", True)

        assert is_tool_allowed(Role.SUPER_ADMIN, "vault_search", store) is True


def test_anonymous_profile_is_never_admin() -> None:
    # Postura de "negar por padrão": credencial ausente/desconhecida nunca
    # deve resultar em privilégio elevado.
    assert ANONYMOUS_PROFILE.role is Role.VIEWER


def test_user_profile_is_frozen() -> None:
    profile = UserProfile(identity="x", role=Role.VIEWER)
    with pytest.raises(Exception):
        profile.role = Role.ADMIN  # type: ignore[misc]
