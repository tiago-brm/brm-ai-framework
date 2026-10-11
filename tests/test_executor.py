import json
from pathlib import Path

import httpx
import pytest

from mcp_engine.core.auth import Role, UserProfile, current_user_var, set_current_user
from mcp_engine.core.executor import (
    ChamadaAutorizada,
    ExecucaoFalhou,
    SecretStore,
    emitir_chamada,
)
from mcp_engine.core.mcp_server import BRMEngine
from mcp_engine.core.models import SkillDefinition, SkillExecutor

CONFIG = """\
client_id: example
block_private_ip: true
domain_allowlist:
  - api.example.com
dlp_patterns:
  - pattern: '\\d{3}\\.\\d{3}\\.\\d{3}-\\d{2}'
    label: CPF
    action: deny
"""

SKILLS = """\
schema_version: 1
client_id: example
skills:
  - name: legado
    description: skill sem bloco executor
    method: GET
    url_template: https://api.example.com/legado
    allowed_domains: [api.example.com]
  - name: registrar
    description: handler nativo
    executor: { type: native, handler: teste.registrar }
    parameters:
      valor: { type: number, required: true }
  - name: chamar_externo
    description: servico externo
    method: POST
    url_template: https://api.example.com/{recurso}
    allowed_domains: [api.example.com]
    executor:
      type: http
      headers: { Authorization: "Bearer ${secret:api_token}" }
      idempotente: true
      mapeamento_resposta: { id: "data.id" }
    parameters:
      recurso: { type: string, required: true }
      texto: { type: string }
  - name: fora_da_lista
    description: host fora da allowlist
    method: GET
    url_template: https://evil.example.org/x
    allowed_domains: [evil.example.org]
    executor: { type: http }
"""


@pytest.fixture(autouse=True)
def _reset_user():
    token = current_user_var.set(None)
    set_current_user(UserProfile(identity="lucas@c.com", role=Role.ADMIN))
    yield
    current_user_var.reset(token)


@pytest.fixture
def secrets(tmp_path: Path) -> SecretStore:
    d = tmp_path / "secrets"
    d.mkdir()
    (d / "api_token").write_text("tok-secreto\n")
    return SecretStore(d)


def _engine(tmp_path: Path, secrets: SecretStore, handler=None) -> tuple[BRMEngine, list]:
    config_root = tmp_path / "config"
    config_dir = config_root / "example" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "rules.yaml").write_text("schema_version: 1\nclient_id: example\nrules: []\n")
    (config_dir / "skills.yaml").write_text(SKILLS)
    (config_dir / "client_config.yaml").write_text(CONFIG)

    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if handler:
            return handler(request)
        return httpx.Response(200, json={"data": {"id": "abc-1"}, "extra": "ignorado"})

    client = httpx.Client(transport=httpx.MockTransport(respond))
    engine = BRMEngine(
        "example", config_root, tmp_path / "data", http_client=client, secrets=secrets
    )
    return engine, seen


class TestSkillDefinition:
    def test_skill_without_executor_block_is_mock(self, tmp_path: Path, secrets) -> None:
        engine, _ = _engine(tmp_path, secrets)
        legado = next(s for s in engine.skills if s.name == "legado")
        assert legado.executor.type == "mock"
        result = engine.call_skill("legado", {})
        assert result["allowed"] is True
        assert json.loads(result["result"])["status"] == "ok"

    def test_native_does_not_need_network_fields(self) -> None:
        skill = SkillDefinition(
            name="x",
            description="d",
            executor=SkillExecutor(type="native", handler="m.h"),
        )
        assert skill.method is None

    def test_http_requires_network_fields(self) -> None:
        with pytest.raises(ValueError):
            SkillDefinition(name="x", description="d", executor=SkillExecutor(type="http"))

    def test_native_requires_handler(self) -> None:
        with pytest.raises(ValueError):
            SkillExecutor(type="native")


class TestNativeHandler:
    def test_handler_receives_validated_args_and_context(self, tmp_path: Path, secrets) -> None:
        engine, _ = _engine(tmp_path, secrets)
        received = {}

        @engine.handlers.register("teste.registrar")
        def registrar(ctx, args):
            received["ctx"] = ctx
            received["args"] = args
            return {"id": 7}

        result = engine.call_skill("registrar", {"valor": 12.5})
        assert result["allowed"] is True
        assert json.loads(result["result"]) == {"id": 7}
        assert received["args"] == {"valor": 12.5}
        assert received["ctx"].actor == "lucas@c.com"
        assert received["ctx"].role == "admin"
        assert received["ctx"].trace_id

    def test_unregistered_handler_fails_closed(self, tmp_path: Path, secrets) -> None:
        engine, _ = _engine(tmp_path, secrets)
        result = engine.call_skill("registrar", {"valor": 1})
        assert result["allowed"] is False
        assert "handler não registrado" in result["reason"]

    def test_simulation_never_runs_the_handler(self, tmp_path: Path, secrets) -> None:
        engine, _ = _engine(tmp_path, secrets)
        calls = []
        engine.handlers.register("teste.registrar", lambda ctx, args: calls.append(1) or {})
        result = engine.call_skill("registrar", {"valor": 1}, simulacao=True)
        assert result["allowed"] is True
        assert calls == []

    def test_intent_is_audited_before_the_handler_runs(self, tmp_path: Path, secrets) -> None:
        engine, _ = _engine(tmp_path, secrets)
        order = []

        def handler(ctx, args):
            order.append(len(list(engine._audit_sink.iter_events("example"))))
            return {}

        engine.handlers.register("teste.registrar", handler)
        engine.call_skill("registrar", {"valor": 1})
        assert order == [1]

    def test_handler_result_with_cpf_is_blocked(self, tmp_path: Path, secrets) -> None:
        engine, _ = _engine(tmp_path, secrets)
        engine.handlers.register("teste.registrar", lambda ctx, args: {"cpf": "123.456.789-09"})
        result = engine.call_skill("registrar", {"valor": 1})
        assert result["allowed"] is False
        assert result["reason"] == "DLP: found CPF"


class TestHttpExecutor:
    def test_success_maps_the_response_and_sends_the_secret(self, tmp_path: Path, secrets) -> None:
        engine, seen = _engine(tmp_path, secrets)
        result = engine.call_skill("chamar_externo", {"recurso": "pedidos", "texto": "oi"})
        assert result["allowed"] is True
        assert json.loads(result["result"]) == {"dados": {"id": "abc-1"}, "status": 200}
        assert seen[0].url.path == "/pedidos"
        assert seen[0].headers["authorization"] == "Bearer tok-secreto"
        assert json.loads(seen[0].content)["texto"] == "oi"

    def test_secret_never_reaches_audit_or_result(self, tmp_path: Path, secrets) -> None:
        engine, _ = _engine(tmp_path, secrets)
        result = engine.call_skill("chamar_externo", {"recurso": "pedidos"})
        audit = (tmp_path / "data" / "example" / "audit.jsonl").read_text()
        assert "tok-secreto" not in audit
        assert "tok-secreto" not in json.dumps(result)

    def test_host_outside_allowlist_is_blocked_without_a_request(
        self, tmp_path: Path, secrets
    ) -> None:
        engine, seen = _engine(tmp_path, secrets)
        result = engine.call_skill("fora_da_lista", {})
        assert result["allowed"] is False
        assert "allowlist" in result["reason"]
        assert seen == []

    @pytest.mark.parametrize("status", [400, 404, 500])
    def test_http_errors_become_structured_failures(
        self, tmp_path: Path, secrets, status: int
    ) -> None:
        engine, _ = _engine(tmp_path, secrets, lambda r: httpx.Response(status, text="segredo"))
        result = engine.call_skill("chamar_externo", {"recurso": "x"})
        assert result["allowed"] is False
        assert result["reason"] == f"serviço externo respondeu {status}"
        assert "segredo" not in json.dumps(result)

    def test_idempotent_skill_retries_network_errors(self, tmp_path: Path, secrets) -> None:
        attempts = []

        def flaky(request):
            attempts.append(1)
            if len(attempts) < 3:
                raise httpx.ConnectError("boom", request=request)
            return httpx.Response(200, json={"data": {"id": "ok"}})

        engine, _ = _engine(tmp_path, secrets, flaky)
        result = engine.call_skill("chamar_externo", {"recurso": "x"})
        assert result["allowed"] is True
        assert len(attempts) == 3

    def test_persistent_network_failure_is_reported(self, tmp_path: Path, secrets) -> None:
        def down(request):
            raise httpx.ConnectTimeout("boom", request=request)

        engine, _ = _engine(tmp_path, secrets, down)
        result = engine.call_skill("chamar_externo", {"recurso": "x"})
        assert result["allowed"] is False
        assert "indisponível" in result["reason"]

    def test_oversized_response_is_rejected(self, tmp_path: Path, secrets) -> None:
        engine, _ = _engine(tmp_path, secrets, lambda r: httpx.Response(200, text="x" * 2_000_000))
        result = engine.call_skill("chamar_externo", {"recurso": "x"})
        assert result["allowed"] is False

    def test_simulation_makes_no_request(self, tmp_path: Path, secrets) -> None:
        engine, seen = _engine(tmp_path, secrets)
        result = engine.call_skill("chamar_externo", {"recurso": "x"}, simulacao=True)
        assert result["allowed"] is True
        assert seen == []

    def test_missing_secret_fails_closed(self, tmp_path: Path) -> None:
        engine, seen = _engine(tmp_path, SecretStore(tmp_path / "vazio"))
        result = engine.call_skill("chamar_externo", {"recurso": "x"})
        assert result["allowed"] is False
        assert "segredo não configurado" in result["reason"]
        assert seen == []


class TestIdempotency:
    def test_same_key_runs_once(self, tmp_path: Path, secrets) -> None:
        engine, seen = _engine(tmp_path, secrets)
        first = engine.call_skill("chamar_externo", {"recurso": "x"}, idempotency_key="k1")
        second = engine.call_skill("chamar_externo", {"recurso": "x"}, idempotency_key="k1")
        assert len(seen) == 1
        assert second["idempotente_reutilizado"] is True
        assert second["result"] == first["result"]

    def test_different_keys_run_twice(self, tmp_path: Path, secrets) -> None:
        engine, seen = _engine(tmp_path, secrets)
        engine.call_skill("chamar_externo", {"recurso": "x"}, idempotency_key="k1")
        engine.call_skill("chamar_externo", {"recurso": "x"}, idempotency_key="k2")
        assert len(seen) == 2

    def test_key_survives_a_restart(self, tmp_path: Path, secrets) -> None:
        engine, seen = _engine(tmp_path, secrets)
        first = engine.call_skill("chamar_externo", {"recurso": "x"}, idempotency_key="k1")
        assert len(seen) == 1

        restarted, seen_after = _engine(tmp_path, secrets)
        again = restarted.call_skill("chamar_externo", {"recurso": "x"}, idempotency_key="k1")
        assert seen_after == []
        assert again["result"] == first["result"]


class TestAuthorizedCall:
    def test_cannot_be_built_outside_the_pipeline(self) -> None:
        skill = SkillDefinition(
            name="x", description="d", executor=SkillExecutor(type="native", handler="m.h")
        )
        with pytest.raises(TypeError):
            ChamadaAutorizada(skill=skill, arguments={}, actor="a", role="admin", trace_id=None)

    def test_pipeline_can_emit_one(self) -> None:
        skill = SkillDefinition(
            name="x", description="d", executor=SkillExecutor(type="native", handler="m.h")
        )
        chamada = emitir_chamada(skill, {"a": 1}, "a", "admin", "t1")
        assert chamada.arguments == {"a": 1}


class TestSecretStore:
    def test_reads_env_before_file(self, tmp_path: Path, monkeypatch) -> None:
        (tmp_path / "chave").write_text("do-arquivo")
        monkeypatch.setenv("BRM_SECRET_CHAVE", "do-ambiente")
        assert SecretStore(tmp_path).get("chave") == "do-ambiente"

    def test_resolves_references_in_text(self, tmp_path: Path) -> None:
        (tmp_path / "a").write_text("1")
        assert SecretStore(tmp_path).resolve("x ${secret:a} y") == "x 1 y"

    def test_missing_secret_raises_without_leaking(self, tmp_path: Path) -> None:
        with pytest.raises(ExecucaoFalhou, match="não configurado"):
            SecretStore(tmp_path).get("nada")
