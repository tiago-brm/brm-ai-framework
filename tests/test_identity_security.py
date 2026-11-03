import asyncio
import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from audit.sink import FileSystemAuditSink
from mcp_engine.core.auth import (
    Role,
    UserProfile,
    current_user_var,
    is_tool_allowed,
    set_current_user,
)
from mcp_engine.core.http_app import create_app
from mcp_engine.core.mcp_server import BRMEngine, build_server
from mcp_engine.core.models import AuditDecision, AuditEvent
from mcp_engine.core.user_store import SQLUserStore, hash_token

CONFIG = """\
client_id: example
block_private_ip: true
domain_allowlist:
  - api.example.com
  - mail.example.com
dlp_patterns:
  - pattern: '\\d{3}\\.\\d{3}\\.\\d{3}-\\d{2}'
    label: CPF
    action: deny
  - pattern: 'ACCT-\\d{6}'
    label: Account
    action: mask
"""

SKILLS = """\
schema_version: 1
client_id: example
skills:
  - name: consultar_saldo
    description: Queries account balance
    method: GET
    url_template: https://api.example.com/balance
    allowed_domains: [api.example.com]
    parameters:
      corpo: { type: string }
  - name: enviar_email
    description: Sends emails
    method: POST
    url_template: https://mail.example.com/send
    allowed_domains: [mail.example.com]
    parameters:
      corpo: { type: string }
"""


def _write_client(config_root: Path, extra_config: str = "", skills: str = SKILLS) -> None:
    config_dir = config_root / "example" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "rules.yaml").write_text("schema_version: 1\nclient_id: example\nrules: []\n")
    (config_dir / "skills.yaml").write_text(skills)
    (config_dir / "client_config.yaml").write_text(CONFIG + extra_config)


@pytest.fixture(autouse=True)
def _reset_current_user():
    token = current_user_var.set(None)
    yield
    current_user_var.reset(token)


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path]:
    config_root = tmp_path / "config"
    _write_client(config_root)
    return config_root, tmp_path / "data"


@pytest.fixture
def engine(paths: tuple[Path, Path]) -> BRMEngine:
    return BRMEngine(client_id="example", config_root=paths[0], data_root=paths[1])


def _events(engine: BRMEngine) -> list[AuditEvent]:
    return list(engine._audit_sink.iter_events("example"))


class TestAuditIdentity:
    def test_events_carry_user_role_and_one_trace_id(self, engine: BRMEngine) -> None:
        set_current_user(UserProfile(identity="lucas@c.com", role=Role.ADMIN))
        engine.call_skill("consultar_saldo", {})

        events = _events(engine)
        assert events
        assert {e.user_email for e in events} == {"lucas@c.com"}
        assert {e.role for e in events} == {"admin"}
        trace_ids = {e.trace_id for e in events}
        assert len(trace_ids) == 1 and None not in trace_ids

    def test_two_calls_get_distinct_trace_ids(self, engine: BRMEngine) -> None:
        set_current_user(UserProfile(identity="lucas@c.com", role=Role.ADMIN))
        engine.call_skill("consultar_saldo", {})
        engine.call_skill("consultar_saldo", {})
        assert len({e.trace_id for e in _events(engine)}) == 2

    def test_anonymous_session_is_labelled(self, engine: BRMEngine) -> None:
        engine.call_skill("consultar_saldo", {})
        assert {e.user_email for e in _events(engine)} == {"anonymous"}

    def test_concurrent_writes_keep_the_chain_valid(self, tmp_path: Path) -> None:
        sink = FileSystemAuditSink(tmp_path)

        def write(i: int) -> None:
            sink.record(
                AuditEvent(
                    client_id="example",
                    occurred_at=datetime.now(UTC),
                    actor=f"user-{i}",
                    action="x",
                    decision=AuditDecision.ALLOWED,
                )
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write, range(200)))

        assert len(list(sink.iter_events("example"))) == 200


class TestTokens:
    def test_token_is_stored_hashed_and_still_authenticates(self, tmp_path: Path) -> None:
        store = SQLUserStore(tmp_path, "example")
        _, token = store.create_user("a@c.com", Role.ADMIN)

        raw = sqlite3.connect(tmp_path / "example" / "config" / "users.db")
        stored = raw.execute("select api_token from users").fetchone()[0]
        assert stored == hash_token(token)
        assert token not in stored
        assert store.get_by_token(token).identity == "a@c.com"
        assert store.get_by_token("outro") is None

    def test_plaintext_tokens_are_migrated_on_startup(self, tmp_path: Path) -> None:
        store = SQLUserStore(tmp_path, "example")
        store.create_user("a@c.com", Role.ADMIN)
        db = tmp_path / "example" / "config" / "users.db"
        raw = sqlite3.connect(db)
        raw.execute("update users set api_token = 'legado-em-texto'")
        raw.commit()
        raw.close()

        migrated = SQLUserStore(tmp_path, "example")
        assert migrated.get_by_token("legado-em-texto").identity == "a@c.com"
        raw = sqlite3.connect(db)
        assert raw.execute("select api_token from users").fetchone()[0].startswith("sha256:")

    def test_rotation_invalidates_the_old_token(self, tmp_path: Path) -> None:
        store = SQLUserStore(tmp_path, "example")
        _, old = store.create_user("a@c.com", Role.ADMIN)
        new = store.rotate_token("a@c.com")
        assert store.get_by_token(old) is None
        assert store.get_by_token(new).identity == "a@c.com"


class TestDefaultDeny:
    def test_unknown_tool_is_denied_by_default_and_allowed_when_configured(
        self, tmp_path: Path
    ) -> None:
        store = SQLUserStore(tmp_path, "example")
        assert store.is_tool_allowed(Role.VIEWER, "ferramenta_nova", "deny") is False
        assert store.is_tool_allowed(Role.VIEWER, "ferramenta_nova", "allow") is True

    def test_super_admin_bypasses_default_deny(self, tmp_path: Path) -> None:
        store = SQLUserStore(tmp_path, "example")
        assert is_tool_allowed(Role.SUPER_ADMIN, "ferramenta_nova", store, "deny") is True
        assert is_tool_allowed(Role.ADMIN, "ferramenta_nova", store, "deny") is False

    def test_fresh_store_is_seeded_so_current_behaviour_is_preserved(
        self, engine: BRMEngine
    ) -> None:
        server = build_server(engine)
        names = [t.name for t in server._tool_manager.list_tools()]
        assert engine.user_store.tools_without_permissions(names) == []
        assert engine.user_store.is_tool_allowed(Role.VIEWER, "consultar_saldo", "deny") is True

    def test_tool_added_later_is_denied_and_logged(
        self, paths: tuple[Path, Path], caplog: pytest.LogCaptureFixture
    ) -> None:
        config_root, data_root = paths
        build_server(BRMEngine("example", config_root, data_root))

        _write_client(
            config_root,
            skills=SKILLS + "  - name: nova_skill\n    description: x\n    method: GET\n"
            "    url_template: https://api.example.com/x\n    allowed_domains: [api.example.com]\n",
        )
        engine = BRMEngine("example", config_root, data_root)
        with caplog.at_level(logging.WARNING):
            build_server(engine)

        assert "nova_skill" in caplog.text
        assert engine.user_store.is_tool_allowed(Role.VIEWER, "nova_skill", "deny") is False

    def test_allow_mode_keeps_legacy_behaviour(self, paths: tuple[Path, Path]) -> None:
        config_root, data_root = paths
        _write_client(config_root, extra_config="rbac_default: allow\n")
        engine = BRMEngine("example", config_root, data_root)
        assert engine.rbac_default == "allow"


class TestBreakGlass:
    def test_super_admin_use_of_admin_only_tool_is_flagged(self, engine: BRMEngine) -> None:
        server = build_server(engine)
        set_current_user(UserProfile(identity="helena@c.com", role=Role.SUPER_ADMIN))
        asyncio.run(server.call_tool("listar_permissoes", {}))

        flagged = [e for e in _events(engine) if e.action == "break_glass"]
        assert len(flagged) == 1
        assert flagged[0].details["tool"] == "listar_permissoes"
        assert flagged[0].user_email == "helena@c.com"

    def test_admin_normal_use_is_not_flagged(self, engine: BRMEngine) -> None:
        server = build_server(engine)
        set_current_user(UserProfile(identity="lucas@c.com", role=Role.ADMIN))
        asyncio.run(server.call_tool("consultar_dados", {}))
        assert not [e for e in _events(engine) if e.action == "break_glass"]

    def test_denial_is_audited_with_the_person(self, engine: BRMEngine) -> None:
        server = build_server(engine)
        set_current_user(UserProfile(identity="camila@c.com", role=Role.VIEWER))
        result = asyncio.run(server.call_tool("listar_permissoes", {}))
        assert result["allowed"] is False
        denied = [e for e in _events(engine) if e.decision is AuditDecision.DENIED]
        assert denied and denied[-1].user_email == "camila@c.com"


class TestRequestDlp:
    def test_cpf_in_arguments_is_blocked_before_anything_runs(self, engine: BRMEngine) -> None:
        set_current_user(UserProfile(identity="helena@c.com", role=Role.SUPER_ADMIN))
        result = engine.call_skill("enviar_email", {"corpo": "CPF 123.456.789-09"})
        assert result["allowed"] is False
        assert result["reason"] == "DLP: found CPF"

    def test_audit_keeps_type_and_count_but_never_the_value(
        self, engine: BRMEngine, paths: tuple[Path, Path]
    ) -> None:
        engine.call_skill("enviar_email", {"corpo": "CPF 123.456.789-09 e 111.222.333-44"})
        audit_text = (paths[1] / "example" / "audit.jsonl").read_text()
        assert "123.456.789-09" not in audit_text
        events = [e for e in _events(engine) if e.details.get("dlp_entrada")]
        assert events[0].details["dlp_entrada"] == {"CPF": 2}

    def test_nested_arguments_are_scanned(self, engine: BRMEngine) -> None:
        result = engine.call_skill("enviar_email", {"corpo": {"anexo": ["ok", "123.456.789-09"]}})
        assert result["allowed"] is False

    def test_mask_is_applied_to_untrusted_roles_only(self, engine: BRMEngine) -> None:
        set_current_user(UserProfile(identity="camila@c.com", role=Role.VIEWER))
        masked = engine.call_skill("consultar_saldo", {"corpo": "conta ACCT-123456"})
        assert "ACCT-123456" not in masked["result"]
        assert "[REDACTED:Account]" in json.dumps(json.loads(masked["result"]))

        set_current_user(UserProfile(identity="lucas@c.com", role=Role.ADMIN))
        raw = engine.call_skill("consultar_saldo", {"corpo": "conta ACCT-123456"})
        assert "ACCT-123456" in raw["result"]


class TestHttpSurface:
    def test_cors_only_allows_configured_origins(self, engine: BRMEngine) -> None:
        client = TestClient(create_app(engine=engine))
        ok = client.options(
            "/api/me",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
        bad = client.options(
            "/api/me",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
        )
        assert "access-control-allow-origin" not in bad.headers

    def test_cors_headers_also_on_unauthorized_responses(self, engine: BRMEngine) -> None:
        client = TestClient(create_app(engine=engine))
        resposta = client.get(
            "/api/central/eu",
            headers={"Origin": "http://localhost:5173", "Authorization": "Bearer invalido"},
        )
        assert resposta.status_code == 401
        assert resposta.headers.get("access-control-allow-origin") == "http://localhost:5173"

    def test_sse_requires_a_credential(self, engine: BRMEngine) -> None:
        client = TestClient(create_app(engine=engine))
        assert client.get("/sse").status_code == 401


class TestTokenRotationTool:
    def test_admin_cannot_rotate_tokens(self, engine: BRMEngine) -> None:
        server = build_server(engine)
        engine.user_store.create_user("alvo@c.com", Role.SUPER_ADMIN)
        set_current_user(UserProfile(identity="lucas@c.com", role=Role.ADMIN))
        result = asyncio.run(server.call_tool("rotacionar_token", {"email": "alvo@c.com"}))
        assert result["allowed"] is False

    def test_engine_method_is_guarded_even_if_permissions_are_wrong(
        self, engine: BRMEngine
    ) -> None:
        engine.user_store.create_user("alvo@c.com", Role.SUPER_ADMIN)
        set_current_user(UserProfile(identity="lucas@c.com", role=Role.ADMIN))
        assert engine.rotacionar_token("alvo@c.com")["allowed"] is False

    def test_super_admin_gets_a_new_token_once(self, engine: BRMEngine) -> None:
        engine.user_store.create_user("alvo@c.com", Role.VIEWER)
        set_current_user(UserProfile(identity="helena@c.com", role=Role.SUPER_ADMIN))
        result = engine.rotacionar_token("alvo@c.com")
        assert result["allowed"] is True
        assert engine.user_store.get_by_token(result["api_token"]).identity == "alvo@c.com"
