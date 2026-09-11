from pathlib import Path

import pytest

from mcp_engine.core.auth import Role
from mcp_engine.core.user_store import (
    SQLUserStore,
    UserAlreadyExistsError,
    UserNotFoundError,
    seed_demo_users,
)


@pytest.fixture
def store(tmp_path: Path) -> SQLUserStore:
    return SQLUserStore(tmp_path, "example")


class TestCreateUser:
    def test_creates_user_with_generated_token(self, store: SQLUserStore) -> None:
        profile, token = store.create_user("nova@escritorio.com.br", Role.VIEWER)

        assert profile.identity == "nova@escritorio.com.br"
        assert profile.role is Role.VIEWER
        assert len(token) > 20

    def test_creates_user_with_explicit_token(self, store: SQLUserStore) -> None:
        profile, token = store.create_user(
            "nova@escritorio.com.br", Role.ADMIN, api_token="meu-token"
        )

        assert token == "meu-token"
        assert store.get_by_token("meu-token") == profile

    def test_duplicate_email_raises(self, store: SQLUserStore) -> None:
        store.create_user("dup@escritorio.com.br", Role.VIEWER)

        with pytest.raises(UserAlreadyExistsError):
            store.create_user("dup@escritorio.com.br", Role.ADMIN)


class TestLookups:
    def test_get_by_email_found_and_not_found(self, store: SQLUserStore) -> None:
        store.create_user("x@escritorio.com.br", Role.ADMIN)

        assert store.get_by_email("x@escritorio.com.br") is not None
        assert store.get_by_email("nao-existe@escritorio.com.br") is None

    def test_get_by_token_found_and_not_found(self, store: SQLUserStore) -> None:
        store.create_user("x@escritorio.com.br", Role.ADMIN, api_token="tok-x")

        assert store.get_by_token("tok-x") is not None
        assert store.get_by_token("nao-existe") is None


class TestListUsers:
    def test_list_users_never_includes_token(self, store: SQLUserStore) -> None:
        store.create_user("a@escritorio.com.br", Role.ADMIN, api_token="secret-a")
        store.create_user("b@escritorio.com.br", Role.VIEWER, api_token="secret-b")

        users = store.list_users()

        assert {u.identity for u in users} == {"a@escritorio.com.br", "b@escritorio.com.br"}
        for u in users:
            assert not hasattr(u, "api_token")


class TestUpdateRole:
    def test_updates_role_of_existing_user(self, store: SQLUserStore) -> None:
        store.create_user("x@escritorio.com.br", Role.VIEWER)

        profile = store.update_role("x@escritorio.com.br", Role.SUPER_ADMIN)

        assert profile.role is Role.SUPER_ADMIN
        assert store.get_by_email("x@escritorio.com.br").role is Role.SUPER_ADMIN

    def test_unknown_email_raises(self, store: SQLUserStore) -> None:
        with pytest.raises(UserNotFoundError):
            store.update_role("nao-existe@escritorio.com.br", Role.ADMIN)


class TestSeedDemoUsers:
    def test_seeds_the_three_demo_users(self, store: SQLUserStore) -> None:
        seed_demo_users(store)

        brm = store.get_by_email("brm@brm.com.br")
        socio = store.get_by_email("socio@escritorio.com.br")
        estagiario = store.get_by_email("estagiario@escritorio.com.br")

        assert brm is not None and brm.role is Role.SUPER_ADMIN
        assert socio is not None and socio.role is Role.ADMIN
        assert estagiario is not None and estagiario.role is Role.VIEWER
        assert store.get_by_token("super-admin-token-demo") == brm
        assert store.get_by_token("admin-token-demo") == socio
        assert store.get_by_token("viewer-token-demo") == estagiario

    def test_is_idempotent(self, store: SQLUserStore) -> None:
        seed_demo_users(store)
        seed_demo_users(store)  # não deve levantar UserAlreadyExistsError

        assert len(store.list_users()) == 3


class TestToolPermissions:
    def test_default_permissions_match_todays_role_tools(self, store: SQLUserStore) -> None:
        assert store.is_tool_allowed(Role.VIEWER, "consultar_dados") is True
        assert store.is_tool_allowed(Role.ADMIN, "consultar_dados") is True
        assert store.is_tool_allowed(Role.VIEWER, "deletar_banco") is False
        assert store.is_tool_allowed(Role.ADMIN, "deletar_banco") is True
        assert store.is_tool_allowed(Role.ADMIN, "atualizar_perfil_usuario") is False

    def test_unregistered_tool_is_open_to_everyone(self, store: SQLUserStore) -> None:
        assert store.is_tool_allowed(Role.VIEWER, "vault_search") is True
        assert store.is_tool_allowed(Role.ADMIN, "vault_search") is True

    def test_set_permission_can_open_a_previously_open_tool(self, store: SQLUserStore) -> None:
        # Tool hoje livre a todo mundo (nenhuma linha registrada) pode virar
        # restrita em runtime: super_admin liga vault_search só pra admin.
        store.set_permission(Role.ADMIN, "vault_search", True)

        assert store.is_tool_allowed(Role.ADMIN, "vault_search") is True
        assert store.is_tool_allowed(Role.VIEWER, "vault_search") is False

    def test_set_permission_updates_existing_row(self, store: SQLUserStore) -> None:
        assert store.is_tool_allowed(Role.VIEWER, "deletar_banco") is False

        store.set_permission(Role.VIEWER, "deletar_banco", True)

        assert store.is_tool_allowed(Role.VIEWER, "deletar_banco") is True

    def test_list_permissions_includes_seeded_defaults(self, store: SQLUserStore) -> None:
        perms = store.list_permissions()

        assert (Role.ADMIN, "deletar_banco", True) in perms
        assert (Role.VIEWER, "consultar_dados", True) in perms

    def test_permissions_persist_across_instances(self, tmp_path: Path) -> None:
        SQLUserStore(tmp_path, "example").set_permission(Role.VIEWER, "vault_search", False)

        reopened = SQLUserStore(tmp_path, "example")
        assert reopened.is_tool_allowed(Role.VIEWER, "vault_search") is False


def test_store_persists_across_instances(tmp_path: Path) -> None:
    SQLUserStore(tmp_path, "example").create_user("perm@escritorio.com.br", Role.VIEWER)

    reopened = SQLUserStore(tmp_path, "example")
    assert reopened.get_by_email("perm@escritorio.com.br") is not None


def test_different_clients_have_isolated_databases(tmp_path: Path) -> None:
    store_a = SQLUserStore(tmp_path, "cliente-a")
    store_b = SQLUserStore(tmp_path, "cliente-b")

    store_a.create_user("x@a.com.br", Role.ADMIN)

    assert store_a.get_by_email("x@a.com.br") is not None
    assert store_b.get_by_email("x@a.com.br") is None
