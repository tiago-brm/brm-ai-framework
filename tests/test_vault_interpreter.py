from pathlib import Path
from types import SimpleNamespace

import pytest

from audit.sink import FileSystemAuditSink
from mcp_engine.core.config_loader import FileSystemRuleProvider, FileSystemSkillProvider
from mcp_engine.core.models import (
    ClientConfig,
    DLPPattern,
    HttpMethod,
    Rule,
    RuleEffect,
    SkillDefinition,
)
from mcp_engine.vault.draft_store import DraftStore
from mcp_engine.vault.interpreter import InterpreterResult, promote_draft, propose_draft
from mcp_engine.vault.obsidian_adapter import ObsidianVaultProvider

CPF_DENY = DLPPattern(pattern=r"\d{3}\.\d{3}\.\d{3}-\d{2}", label="CPF", action="deny")
ACCOUNT_MASK = DLPPattern(pattern=r"ACCT-\d{6}", label="Account", action="mask")

SAMPLE_RULE = Rule(
    id="bloqueia-valor-alto",
    description="Bloqueia consultas acima de 50000",
    condition={">": [{"var": "valor"}, 50000]},
    effect=RuleEffect.DENY,
    message="Valor acima do limite",
)

SAMPLE_SKILL = SkillDefinition(
    name="consultar_processo",
    description="Consulta andamento processual",
    method=HttpMethod.GET,
    url_template="https://api.tribunal.example.com/processos/{numero}",
    allowed_domains=("api.tribunal.example.com",),
)


class FakeEmbeddingProvider:
    @property
    def dim(self) -> int:
        return 384

    def encode(self, texts: list[str]) -> list[list[float]]:
        result = []
        for text in texts:
            h = hash(text) & 0xFFFFFFFF
            vec = [(float((h >> (i % 16)) & 1) - 0.5) for i in range(384)]
            result.append(vec)
        return result


class FakeInterpreterAgent:
    def __init__(self, result: InterpreterResult) -> None:
        self.result = result
        self.prompts: list[str] = []

    def run(self, input: str, *, output_schema=None):
        self.prompts.append(input)
        return SimpleNamespace(content=self.result)


def _write_note(vault_root: Path, relpath: str, title: str, content: str) -> None:
    path = vault_root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntitle: {title}\ntags: []\n---\n\n{content}\n")


class Setup(SimpleNamespace):
    pass


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    config_root = tmp_path / "config"
    data_root = tmp_path / "data"

    client_config_dir = config_root / "example" / "config"
    client_config_dir.mkdir(parents=True)
    client_config_dir.joinpath("rules.yaml").write_text(
        "schema_version: 1\nclient_id: example\nrules: []\n"
    )
    client_config_dir.joinpath("skills.yaml").write_text(
        "schema_version: 1\nclient_id: example\nskills: []\n"
    )

    audit_sink = FileSystemAuditSink(data_root)
    vault_provider = ObsidianVaultProvider(
        config_root, data_root, audit_sink, FakeEmbeddingProvider()
    )
    vault_root = config_root / "example" / "vault"
    (vault_root / "config_drafts").mkdir(parents=True)

    return Setup(
        config_root=config_root,
        data_root=data_root,
        audit_sink=audit_sink,
        vault_provider=vault_provider,
        vault_root=vault_root,
        draft_store=DraftStore(config_root),
        rule_provider=FileSystemRuleProvider(config_root),
        skill_provider=FileSystemSkillProvider(config_root),
        client_config=ClientConfig(client_id="example", domain_allowlist=("api.example.com",)),
    )


class TestProposeDraft:
    def test_rejects_note_outside_config_drafts_folder(self, setup: Setup) -> None:
        _write_note(setup.vault_root, "faq/como-fazer-x.md", "Como fazer X", "Passo a passo...")
        agent = FakeInterpreterAgent(InterpreterResult(success=True))

        result = propose_draft(
            "example", "faq/como-fazer-x.md", setup.vault_provider, setup.client_config,
            setup.audit_sink, setup.draft_store, agent,
        )

        assert result["allowed"] is False
        assert "config_drafts/" in result["reason"]
        assert agent.prompts == []

    def test_rejects_missing_note(self, setup: Setup) -> None:
        agent = FakeInterpreterAgent(InterpreterResult(success=True))

        result = propose_draft(
            "example", "config_drafts/missing.md", setup.vault_provider, setup.client_config,
            setup.audit_sink, setup.draft_store, agent,
        )

        assert result["allowed"] is False
        assert "not found" in result["reason"]

    def test_blocks_on_dlp_deny_before_calling_llm(self, setup: Setup) -> None:
        _write_note(
            setup.vault_root, "config_drafts/regra.md", "Nova regra",
            "Bloquear se CPF 123.456.789-00 aparecer",
        )
        config = ClientConfig(
            client_id="example", domain_allowlist=("api.example.com",), dlp_patterns=(CPF_DENY,)
        )
        agent = FakeInterpreterAgent(InterpreterResult(success=True))

        result = propose_draft(
            "example", "config_drafts/regra.md", setup.vault_provider, config,
            setup.audit_sink, setup.draft_store, agent,
        )

        assert result["allowed"] is False
        assert "DLP" in result["reason"]
        assert agent.prompts == []

        events = list(setup.audit_sink.iter_events("example"))
        egress_events = [e for e in events if e.action == "vault_content_egress_check"]
        assert len(egress_events) == 1
        assert egress_events[0].decision.value == "denied"

    def test_blocks_on_dlp_mask_pattern_too(self, setup: Setup) -> None:
        _write_note(
            setup.vault_root, "config_drafts/regra.md", "Nova regra",
            "Conta ACCT-123456 relacionada",
        )
        config = ClientConfig(
            client_id="example",
            domain_allowlist=("api.example.com",),
            dlp_patterns=(ACCOUNT_MASK,),
        )
        agent = FakeInterpreterAgent(InterpreterResult(success=True))

        result = propose_draft(
            "example", "config_drafts/regra.md", setup.vault_provider, config,
            setup.audit_sink, setup.draft_store, agent,
        )

        assert result["allowed"] is False
        assert agent.prompts == []

    def test_generates_rule_draft_and_persists_pending(self, setup: Setup) -> None:
        _write_note(
            setup.vault_root, "config_drafts/regra.md", "Bloqueia valor alto",
            "Bloquear consultas acima de R$ 50.000",
        )
        agent = FakeInterpreterAgent(
            InterpreterResult(success=True, kind="rule", rule=SAMPLE_RULE)
        )

        result = propose_draft(
            "example", "config_drafts/regra.md", setup.vault_provider, setup.client_config,
            setup.audit_sink, setup.draft_store, agent,
        )

        assert result["kind"] == "rule"
        assert "draft_id" in result
        assert len(agent.prompts) == 1
        assert "Bloqueia valor alto" in agent.prompts[0]

        draft = setup.draft_store.get_draft("example", "rule", result["draft_id"])
        assert draft is not None
        assert draft["status"] == "pending"
        assert draft["content"]["id"] == "bloqueia-valor-alto"
        assert draft["from_note_id"] == "config_drafts/regra.md"

        events = list(setup.audit_sink.iter_events("example"))
        gen_events = [e for e in events if e.action == "vault_generate_draft"]
        assert len(gen_events) == 1
        assert gen_events[0].decision.value == "allowed"
        assert gen_events[0].actor == "interpreter_agent"

    def test_generates_skill_draft_and_persists_pending(self, setup: Setup) -> None:
        _write_note(
            setup.vault_root, "config_drafts/skill.md", "Consultar processo",
            "Chamar API do tribunal para consultar andamento",
        )
        agent = FakeInterpreterAgent(
            InterpreterResult(success=True, kind="skill", skill=SAMPLE_SKILL)
        )

        result = propose_draft(
            "example", "config_drafts/skill.md", setup.vault_provider, setup.client_config,
            setup.audit_sink, setup.draft_store, agent,
        )

        assert result["kind"] == "skill"
        draft = setup.draft_store.get_draft("example", "skill", result["draft_id"])
        assert draft["content"]["name"] == "consultar_processo"

    def test_rejects_when_interpreter_reports_failure(self, setup: Setup) -> None:
        _write_note(setup.vault_root, "config_drafts/vaga.md", "Coisa vaga", "faça algo bom")
        agent = FakeInterpreterAgent(
            InterpreterResult(success=False, error="nota vaga demais para virar regra")
        )

        result = propose_draft(
            "example", "config_drafts/vaga.md", setup.vault_provider, setup.client_config,
            setup.audit_sink, setup.draft_store, agent,
        )

        assert result["allowed"] is False
        assert result["reason"] == "nota vaga demais para virar regra"
        assert setup.draft_store.list_drafts("example", "rule") == []

        events = list(setup.audit_sink.iter_events("example"))
        gen_events = [e for e in events if e.action == "vault_generate_draft"]
        assert gen_events[0].decision.value == "denied"

    def test_rejects_invalid_jsonlogic_condition(self, setup: Setup) -> None:
        _write_note(setup.vault_root, "config_drafts/ruim.md", "Regra ruim", "algo estranho")
        bad_rule = Rule(
            id="regra-ruim",
            description="Regra com JSONLogic inválido",
            condition={"nao_existe_esse_operador": [1, 2]},
            effect=RuleEffect.DENY,
        )
        agent = FakeInterpreterAgent(InterpreterResult(success=True, kind="rule", rule=bad_rule))

        result = propose_draft(
            "example", "config_drafts/ruim.md", setup.vault_provider, setup.client_config,
            setup.audit_sink, setup.draft_store, agent,
        )

        assert result["allowed"] is False
        assert "JSONLogic" in result["reason"]
        assert setup.draft_store.list_drafts("example", "rule") == []

    def test_prompt_includes_related_note_titles(self, setup: Setup) -> None:
        _write_note(
            setup.vault_root, "config_drafts/regra.md", "Nova regra", "Ver [[compliance]]"
        )
        _write_note(setup.vault_root, "compliance.md", "Compliance Internacional", "Contexto...")
        setup.vault_provider.reindex("example")

        agent = FakeInterpreterAgent(
            InterpreterResult(success=True, kind="rule", rule=SAMPLE_RULE)
        )

        propose_draft(
            "example", "config_drafts/regra.md", setup.vault_provider, setup.client_config,
            setup.audit_sink, setup.draft_store, agent,
        )

        assert "Compliance Internacional" in agent.prompts[0]


class TestPromoteDraft:
    def test_promotes_rule_to_active_config(self, setup: Setup) -> None:
        setup.draft_store.create(
            "example", "rule", "d1", "config_drafts/regra.md", "t1", "interpreter_agent",
            SAMPLE_RULE.model_dump(mode="json"),
        )

        result = promote_draft(
            "example", "d1", "rule", setup.draft_store, setup.rule_provider,
            setup.skill_provider, setup.audit_sink,
        )

        assert result["promoted"] is True
        ruleset = setup.rule_provider.get_rules("example")
        assert any(r.id == "bloqueia-valor-alto" for r in ruleset.rules)

        draft = setup.draft_store.get_draft("example", "rule", "d1")
        assert draft["status"] == "approved"
        assert draft["reviewed_by"] == "claude"

        events = list(setup.audit_sink.iter_events("example"))
        promote_events = [e for e in events if e.action == "vault_promote_draft"]
        assert len(promote_events) == 1
        assert promote_events[0].decision.value == "allowed"

    def test_promotes_skill_to_active_config(self, setup: Setup) -> None:
        setup.draft_store.create(
            "example", "skill", "d2", "config_drafts/skill.md", "t1", "interpreter_agent",
            SAMPLE_SKILL.model_dump(mode="json"),
        )

        result = promote_draft(
            "example", "d2", "skill", setup.draft_store, setup.rule_provider,
            setup.skill_provider, setup.audit_sink,
        )

        assert result["promoted"] is True
        skillset = setup.skill_provider.get_skills("example")
        assert any(s.name == "consultar_processo" for s in skillset.skills)

    def test_promote_missing_draft_returns_error(self, setup: Setup) -> None:
        result = promote_draft(
            "example", "nope", "rule", setup.draft_store, setup.rule_provider,
            setup.skill_provider, setup.audit_sink,
        )

        assert result["allowed"] is False
        assert "not found" in result["reason"]

        events = list(setup.audit_sink.iter_events("example"))
        assert events[0].action == "vault_promote_error"
        assert events[0].decision.value == "denied"

    def test_promote_already_approved_draft_rejected(self, setup: Setup) -> None:
        setup.draft_store.create(
            "example", "rule", "d1", "config_drafts/regra.md", "t1", "interpreter_agent",
            SAMPLE_RULE.model_dump(mode="json"),
        )
        promote_draft(
            "example", "d1", "rule", setup.draft_store, setup.rule_provider,
            setup.skill_provider, setup.audit_sink,
        )

        second = promote_draft(
            "example", "d1", "rule", setup.draft_store, setup.rule_provider,
            setup.skill_provider, setup.audit_sink,
        )

        assert second["allowed"] is False
        assert "not pending" in second["reason"]

        ruleset = setup.rule_provider.get_rules("example")
        assert len(ruleset.rules) == 1

    def test_promote_invalid_content_rejected(self, setup: Setup) -> None:
        setup.draft_store.create(
            "example", "rule", "d1", "config_drafts/regra.md", "t1", "interpreter_agent",
            {"id": "regra-sem-campos-obrigatorios"},
        )

        result = promote_draft(
            "example", "d1", "rule", setup.draft_store, setup.rule_provider,
            setup.skill_provider, setup.audit_sink,
        )

        assert result["allowed"] is False
        assert "invalid" in result["reason"]
