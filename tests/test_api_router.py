from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mcp_engine.core.auth import Role
from mcp_engine.core.http_app import create_app
from mcp_engine.core.mcp_server import BRMEngine


@pytest.fixture
def engine(tmp_path: Path) -> BRMEngine:
    config_root = tmp_path / "config"
    data_root = tmp_path / "data"
    config_dir = config_root / "example" / "config"
    config_dir.mkdir(parents=True)

    (config_dir / "rules.yaml").write_text(
        "schema_version: 1\nclient_id: example\nrules:\n"
        "  - id: r1\n    description: teste\n"
        '    condition: { "==": [1, 1] }\n    effect: allow\n'
    )
    (config_dir / "skills.yaml").write_text("schema_version: 1\nclient_id: example\nskills: []\n")
    (config_dir / "client_config.yaml").write_text(
        "client_id: example\nblock_private_ip: true\ndomain_allowlist:\n  - api.example.com\n"
    )

    return BRMEngine(client_id="example", config_root=config_root, data_root=data_root)


@pytest.fixture
def client(engine: BRMEngine) -> TestClient:
    engine.user_store.create_user("brm@brm.com.br", Role.SUPER_ADMIN, api_token="super-tok")
    engine.user_store.create_user("socio@escritorio.com.br", Role.ADMIN, api_token="admin-tok")
    return TestClient(create_app(engine=engine))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestAccessControl:
    def test_no_token_is_forbidden(self, client: TestClient) -> None:
        response = client.get("/api/me")
        assert response.status_code == 403

    def test_admin_token_is_forbidden(self, client: TestClient) -> None:
        response = client.get("/api/me", headers=_auth("admin-tok"))
        assert response.status_code == 403

    def test_super_admin_token_is_allowed(self, client: TestClient) -> None:
        response = client.get("/api/me", headers=_auth("super-tok"))
        assert response.status_code == 200
        assert response.json() == {"identity": "brm@brm.com.br", "role": "super_admin"}


class TestUsersEndpoints:
    def test_list_users(self, client: TestClient) -> None:
        response = client.get("/api/users", headers=_auth("super-tok"))
        assert response.status_code == 200
        assert response.json()["total"] == 2

    def test_create_and_update_role(self, client: TestClient) -> None:
        create_response = client.post(
            "/api/users",
            headers=_auth("super-tok"),
            json={"email": "nova@escritorio.com.br", "role": "viewer"},
        )
        assert create_response.status_code == 201
        assert create_response.json()["api_token"]

        update_response = client.patch(
            "/api/users/nova@escritorio.com.br/role",
            headers=_auth("super-tok"),
            json={"role": "admin"},
        )
        assert update_response.status_code == 200
        assert update_response.json()["role"] == "admin"

    def test_duplicate_user_returns_400(self, client: TestClient) -> None:
        response = client.post(
            "/api/users",
            headers=_auth("super-tok"),
            json={"email": "socio@escritorio.com.br", "role": "viewer"},
        )
        assert response.status_code == 400


class TestPermissionsEndpoints:
    def test_list_and_update_permissions(self, client: TestClient) -> None:
        list_response = client.get("/api/permissions", headers=_auth("super-tok"))
        assert list_response.status_code == 200
        assert list_response.json()["total"] > 0

        update_response = client.put(
            "/api/permissions",
            headers=_auth("super-tok"),
            json={"role": "viewer", "tool_name": "vault_search", "allowed": False},
        )
        assert update_response.status_code == 200

        response = client.get(
            "/api/tools", headers=_auth("admin-tok")
        )  # still forbidden for admin
        assert response.status_code == 403


class TestReadOnlyEndpoints:
    def test_list_tools(self, client: TestClient) -> None:
        response = client.get("/api/tools", headers=_auth("super-tok"))
        assert response.status_code == 200
        names = {t["name"] for t in response.json()["tools"]}
        assert "call_skill" in names
        assert "criar_usuario" in names

    def test_list_rules(self, client: TestClient) -> None:
        response = client.get("/api/rules", headers=_auth("super-tok"))
        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert response.json()["rules"][0]["id"] == "r1"

    def test_list_vault_notes(self, client: TestClient) -> None:
        response = client.get("/api/vault/notes", headers=_auth("super-tok"))
        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_list_audit(self, client: TestClient) -> None:
        response = client.get("/api/audit", headers=_auth("super-tok"))
        assert response.status_code == 200
        assert "events" in response.json()
