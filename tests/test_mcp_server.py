import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcp_engine.core.mcp_server import BRMEngine
from mcp_engine.core.models import AuditDecision, Rule, RuleEffect
from mcp_engine.vault.interpreter import InterpreterResult


class TestBRMEngine:
    @pytest.fixture
    def engine(self, tmp_path: Path) -> BRMEngine:
        config_root = tmp_path / "config"
        data_root = tmp_path / "data"

        config_dir = config_root / "example" / "config"
        config_dir.mkdir(parents=True)

        # rules.yaml
        rules_yaml = config_dir / "rules.yaml"
        rules_yaml.write_text(
            """\
schema_version: 1
client_id: example
rules:
  - id: max-valor-consulta
    description: Denies requests querying more than R$ 100,000
    condition: { ">": [{"var": "valor_consultado"}, 100000] }
    effect: deny
    message: Valor consultado acima do limite permitido
  - id: envio-externo-requer-aprovacao
    description: Requires human approval for external API calls
    condition: { "==": [{"var": "tipo_acao"}, "enviar_para_sistema_externo"] }
    effect: require_approval
    message: Esta ação envolve envio para sistema externo
"""
        )

        # skills.yaml
        skills_yaml = config_dir / "skills.yaml"
        skills_yaml.write_text(
            """\
schema_version: 1
client_id: example
skills:
  - name: consultar_saldo
    description: Queries account balance
    method: GET
    url_template: https://api.example.com/balance
    allowed_domains:
      - api.example.com
    timeout_seconds: 30.0
  - name: enviar_email
    description: Sends emails
    method: POST
    url_template: https://mail.example.com/send
    allowed_domains:
      - mail.example.com
    timeout_seconds: 30.0
"""
        )

        # client_config.yaml
        client_config_yaml = config_dir / "client_config.yaml"
        client_config_yaml.write_text(
            """\
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
        )

        return BRMEngine(
            client_id="example",
            config_root=config_root,
            data_root=data_root,
        )

    def test_engine_loads_config_eagerly(self, engine: BRMEngine) -> None:
        assert engine.client_id == "example"
        assert engine._ruleset.client_id == "example"
        assert len(engine._ruleset.rules) == 2
        assert engine._skillset.client_id == "example"
        assert len(engine._skillset.skills) == 2
        assert engine._client_config.client_id == "example"
        assert "api.example.com" in engine._client_config.domain_allowlist

    def test_call_skill_with_allowed_request(self, engine: BRMEngine) -> None:
        result = engine.call_skill("consultar_saldo", {})

        assert result["allowed"] is True
        assert result["reason"] is None
        assert result["result"] != ""
        assert result["payload_digest"] is not None
        assert len(result["payload_digest"]) == 64  # sha256 hex

    def test_call_skill_not_found(self, engine: BRMEngine) -> None:
        result = engine.call_skill("nonexistent_skill", {})

        assert result["allowed"] is False
        assert "skill not found" in result["reason"]
        assert result["result"] == ""
        assert result["payload_digest"] is None

    def test_call_skill_denied_by_rule(self, engine: BRMEngine) -> None:
        result = engine.call_skill("consultar_saldo", {"valor_consultado": 200000})

        assert result["allowed"] is False
        assert "Valor consultado acima do limite" in result["reason"]
        assert result["rule_id"] == "max-valor-consulta"
        assert result["result"] == ""
        assert result["payload_digest"] is None

    def test_call_skill_requires_approval(self, engine: BRMEngine) -> None:
        result = engine.call_skill("enviar_email", {"tipo_acao": "enviar_para_sistema_externo"})

        assert result["allowed"] is False
        assert result["reason"] == "human approval required"
        assert result["rule_id"] == "envio-externo-requer-aprovacao"

    def test_call_skill_denied_by_allowlist(self, engine: BRMEngine, tmp_path: Path) -> None:
        config_root = tmp_path / "config2"
        data_root = tmp_path / "data2"

        config_dir = config_root / "acme" / "config"
        config_dir.mkdir(parents=True)

        rules_yaml = config_dir / "rules.yaml"
        rules_yaml.write_text("schema_version: 1\nclient_id: acme\nrules: []")

        skills_yaml = config_dir / "skills.yaml"
        skills_yaml.write_text(
            """\
schema_version: 1
client_id: acme
skills:
  - name: evil_exfil
    description: Calls evil domain
    method: GET
    url_template: https://evil.com/exfil
    allowed_domains:
      - evil.com
    timeout_seconds: 30.0
"""
        )

        client_config_yaml = config_dir / "client_config.yaml"
        client_config_yaml.write_text(
            """\
client_id: acme
block_private_ip: true
domain_allowlist:
  - api.internal.acme.com
"""
        )

        engine = BRMEngine(client_id="acme", config_root=config_root, data_root=data_root)
        result = engine.call_skill("evil_exfil", {})

        assert result["allowed"] is False
        assert "domain not in allowlist" in result["reason"]

    def test_call_skill_blocked_by_dlp(self, engine: BRMEngine) -> None:
        cpf_number = "123.456.789-00"
        result = engine.call_skill("consultar_saldo", {"cpf": cpf_number})

        assert result["allowed"] is False
        assert "DLP" in result["reason"] and "CPF" in result["reason"]
        assert result["payload_digest"] is None

    def test_call_skill_records_audit_for_skill_not_found(self, engine: BRMEngine) -> None:
        engine.call_skill("nonexistent", {})

        events = list(engine._audit_sink.iter_events("example"))
        assert len(events) == 1
        assert events[0].action == "nonexistent"
        assert events[0].decision is AuditDecision.DENIED
        assert events[0].rule_id is None

    def test_call_skill_records_audit_for_rule_denied(self, engine: BRMEngine) -> None:
        engine.call_skill("consultar_saldo", {"valor_consultado": 200000})

        events = list(engine._audit_sink.iter_events("example"))
        assert len(events) == 1
        assert events[0].action == "consultar_saldo"
        assert events[0].decision is AuditDecision.DENIED
        assert events[0].rule_id == "max-valor-consulta"

    def test_call_skill_records_audit_for_approval_required(self, engine: BRMEngine) -> None:
        engine.call_skill("enviar_email", {"tipo_acao": "enviar_para_sistema_externo"})

        events = list(engine._audit_sink.iter_events("example"))
        assert len(events) == 1
        assert events[0].action == "enviar_email"
        assert events[0].decision is AuditDecision.PENDING_APPROVAL
        assert events[0].rule_id == "envio-externo-requer-aprovacao"

    def test_call_skill_records_audit_for_allowed_call(self, engine: BRMEngine) -> None:
        engine.call_skill("consultar_saldo", {})

        events = list(engine._audit_sink.iter_events("example"))
        assert len(events) == 1
        assert events[0].action == "consultar_saldo"
        assert events[0].decision is AuditDecision.ALLOWED

    def test_call_skill_records_audit_for_allowlist_denied(self, engine: BRMEngine) -> None:
        engine.call_skill("consultar_saldo", {})
        engine.call_skill("nonexistent", {})

        events = list(engine._audit_sink.iter_events("example"))
        assert len(events) >= 1

    def test_query_audit_log_returns_all_events(self, engine: BRMEngine) -> None:
        engine.call_skill("consultar_saldo", {})
        engine.call_skill("consultar_saldo", {"valor_consultado": 200000})
        engine.call_skill("enviar_email", {"tipo_acao": "enviar_para_sistema_externo"})

        result = engine.query_audit_log()

        assert result["total"] >= 3
        assert len(result["events"]) >= 3

    def test_query_audit_log_filters_by_action(self, engine: BRMEngine) -> None:
        engine.call_skill("consultar_saldo", {})
        engine.call_skill("enviar_email", {"tipo_acao": "enviar_para_sistema_externo"})

        result = engine.query_audit_log(action="consultar_saldo")

        assert result["total"] == 1
        assert result["events"][0]["action"] == "consultar_saldo"

    def test_query_audit_log_filters_by_decision(self, engine: BRMEngine) -> None:
        engine.call_skill("consultar_saldo", {})
        engine.call_skill("consultar_saldo", {"valor_consultado": 200000})

        result = engine.query_audit_log(decision="denied")

        assert result["total"] >= 1
        for event in result["events"]:
            assert event["decision"] == "denied"

    def test_query_audit_log_filters_by_date_range(self, engine: BRMEngine) -> None:
        engine.call_skill("consultar_saldo", {})

        now = datetime.now(UTC)
        future = now + timedelta(hours=1)
        past = now - timedelta(hours=1)

        result_past_future = engine.query_audit_log(
            start_date=past.isoformat(), end_date=future.isoformat()
        )
        assert result_past_future["total"] >= 1

        result_future = engine.query_audit_log(start_date=future.isoformat())
        assert result_future["total"] == 0

    def test_query_audit_log_event_format(self, engine: BRMEngine) -> None:
        engine.call_skill("consultar_saldo", {})

        result = engine.query_audit_log()

        assert result["total"] >= 1
        event = result["events"][0]
        assert "occurred_at" in event
        assert "actor" in event
        assert "action" in event
        assert "decision" in event
        assert event["actor"] == "claude"

    def test_query_audit_log_rule_id_field(self, engine: BRMEngine) -> None:
        engine.call_skill("consultar_saldo", {"valor_consultado": 200000})

        result = engine.query_audit_log()

        events = [e for e in result["events"] if e["action"] == "consultar_saldo"]
        assert len(events) >= 1
        assert events[0]["rule_id"] == "max-valor-consulta"

    def test_call_skill_mocked_response_structure(self, engine: BRMEngine) -> None:
        result = engine.call_skill("consultar_saldo", {"arg1": "value1", "arg2": 42})

        response_json = json.loads(result["result"])
        assert response_json["status"] == "ok"
        assert response_json["skill"] == "consultar_saldo"
        assert response_json["arguments"]["arg1"] == "value1"
        assert response_json["arguments"]["arg2"] == 42

    def test_url_template_rendering_with_no_placeholders(self, engine: BRMEngine) -> None:
        result = engine.call_skill("consultar_saldo", {"unused_arg": "value"})

        assert result["allowed"] is True
        assert result["result"] != ""

    def test_dlp_mask_pattern_allows_response(self, engine: BRMEngine, tmp_path: Path) -> None:
        config_root = tmp_path / "config3"
        data_root = tmp_path / "data3"

        config_dir = config_root / "acme2" / "config"
        config_dir.mkdir(parents=True)

        rules_yaml = config_dir / "rules.yaml"
        rules_yaml.write_text("schema_version: 1\nclient_id: acme2\nrules: []")

        skills_yaml = config_dir / "skills.yaml"
        skills_yaml.write_text(
            """\
schema_version: 1
client_id: acme2
skills:
  - name: get_account
    description: Gets account info
    method: GET
    url_template: https://api.example.com/account
    allowed_domains:
      - api.example.com
    timeout_seconds: 30.0
"""
        )

        client_config_yaml = config_dir / "client_config.yaml"
        client_config_yaml.write_text(
            """\
client_id: acme2
block_private_ip: true
domain_allowlist:
  - api.example.com
dlp_patterns:
  - pattern: 'ACCT-\\d{6}'
    label: Account
    action: mask
"""
        )

        engine = BRMEngine(client_id="acme2", config_root=config_root, data_root=data_root)
        result = engine.call_skill("get_account", {"acct": "ACCT-123456"})

        assert result["allowed"] is True
        assert result["reason"] is None


class FakeInterpreterAgent:
    def __init__(self, result: InterpreterResult) -> None:
        self.result = result
        self.prompts: list[str] = []

    def run(self, input: str, *, output_schema=None):
        self.prompts.append(input)
        return SimpleNamespace(content=self.result)


class TestVaultInterpreterWiring:
    """End-to-end through BRMEngine + the MCP tool return-shape conventions."""

    @pytest.fixture
    def roots(self, tmp_path: Path) -> tuple[Path, Path]:
        config_root = tmp_path / "config"
        data_root = tmp_path / "data"
        config_dir = config_root / "example" / "config"
        config_dir.mkdir(parents=True)
        config_dir.joinpath("rules.yaml").write_text(
            "schema_version: 1\nclient_id: example\nrules: []\n"
        )
        config_dir.joinpath("skills.yaml").write_text(
            "schema_version: 1\nclient_id: example\nskills: []\n"
        )
        config_dir.joinpath("client_config.yaml").write_text(
            "client_id: example\nblock_private_ip: true\ndomain_allowlist:\n"
            "  - api.example.com\n"
        )
        return config_root, data_root

    def _write_note(self, config_root: Path, relpath: str, title: str, content: str) -> None:
        path = config_root / "example" / "vault" / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntitle: {title}\ntags: []\n---\n\n{content}\n")

    def test_vault_interpret_and_propose_generates_pending_draft(
        self, roots: tuple[Path, Path]
    ) -> None:
        config_root, data_root = roots
        self._write_note(
            config_root, "config_drafts/regra.md", "Bloqueia valor alto",
            "Bloquear consultas acima de R$ 50.000",
        )
        rule = Rule(
            id="bloqueia-valor-alto",
            description="Bloqueia consultas acima de 50000",
            condition={">": [{"var": "valor"}, 50000]},
            effect=RuleEffect.DENY,
        )
        agent = FakeInterpreterAgent(
            InterpreterResult(success=True, kind="rule", rule=rule)
        )
        engine = BRMEngine(
            client_id="example", config_root=config_root, data_root=data_root,
            interpreter_agent=agent,
        )

        result = engine.vault_interpret_and_propose("config_drafts/regra.md")

        assert result["kind"] == "rule"
        assert "draft_id" in result

        listing = engine.vault_list_drafts()
        assert listing["total"] == 1
        assert listing["drafts"][0]["status"] == "pending"

    def test_vault_interpret_and_propose_rejects_outside_folder_convention(
        self, roots: tuple[Path, Path]
    ) -> None:
        config_root, data_root = roots
        self._write_note(config_root, "faq/duvida.md", "Dúvida comum", "Resposta aqui")
        agent = FakeInterpreterAgent(InterpreterResult(success=True))
        engine = BRMEngine(
            client_id="example", config_root=config_root, data_root=data_root,
            interpreter_agent=agent,
        )

        result = engine.vault_interpret_and_propose("faq/duvida.md")

        assert result["allowed"] is False
        assert agent.prompts == []

    def test_vault_promote_draft_writes_to_active_rules(
        self, roots: tuple[Path, Path]
    ) -> None:
        config_root, data_root = roots
        self._write_note(
            config_root, "config_drafts/regra.md", "Bloqueia valor alto",
            "Bloquear consultas acima de R$ 50.000",
        )
        rule = Rule(
            id="bloqueia-valor-alto",
            description="Bloqueia consultas acima de 50000",
            condition={">": [{"var": "valor"}, 50000]},
            effect=RuleEffect.DENY,
        )
        agent = FakeInterpreterAgent(
            InterpreterResult(success=True, kind="rule", rule=rule)
        )
        engine = BRMEngine(
            client_id="example", config_root=config_root, data_root=data_root,
            interpreter_agent=agent,
        )
        proposal = engine.vault_interpret_and_propose("config_drafts/regra.md")

        result = engine.vault_promote_draft(proposal["draft_id"], "rule")

        assert result["promoted"] is True

        # Reflects immediately for a freshly constructed engine reading the same config_root.
        second_engine = BRMEngine(client_id="example", config_root=config_root, data_root=data_root)
        assert any(r.id == "bloqueia-valor-alto" for r in second_engine._ruleset.rules)
