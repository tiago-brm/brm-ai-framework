from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from mcp_engine.core.models import (
    AuditDecision,
    AuditEvent,
    HttpMethod,
    Rule,
    RuleEffect,
    RuleSet,
    SkillDefinition,
    SkillSet,
)


def test_ruleset_requires_schema_version_1():
    with pytest.raises(ValidationError):
        RuleSet(schema_version=2, client_id="acme", rules=())


def test_ruleset_accepts_valid_rule():
    rule = Rule(
        id="max-valor",
        description="Bloqueia valores acima de 10000",
        condition={">": [{"var": "valor"}, 10000]},
        effect=RuleEffect.DENY,
    )
    rules = RuleSet(schema_version=1, client_id="acme", rules=(rule,))
    assert rules.rules[0].effect is RuleEffect.DENY


def test_models_reject_unknown_fields():
    with pytest.raises(ValidationError):
        Rule(
            id="x",
            description="d",
            condition={},
            effect=RuleEffect.ALLOW,
            unexpected="nope",
        )


def test_skill_definition_requires_at_least_one_allowed_domain():
    with pytest.raises(ValidationError):
        SkillDefinition(
            name="consulta-cep",
            description="Consulta CEP",
            method=HttpMethod.GET,
            url_template="https://{domain}/cep/{cep}",
            allowed_domains=(),
        )


def test_skillset_roundtrip():
    skill = SkillDefinition(
        name="consulta-cep",
        description="Consulta CEP",
        method=HttpMethod.GET,
        url_template="https://viacep.com.br/ws/{cep}/json/",
        allowed_domains=("viacep.com.br",),
    )
    skills = SkillSet(schema_version=1, client_id="acme", skills=(skill,))
    assert skills.skills[0].allowed_domains == ("viacep.com.br",)


def test_audit_event_defaults_schema_version():
    event = AuditEvent(
        client_id="acme",
        occurred_at=datetime.now(timezone.utc),
        actor="mcp_server",
        action="run_skill:consulta-cep",
        decision=AuditDecision.ALLOWED,
    )
    assert event.schema_version == 1


def test_models_are_immutable():
    rule = Rule(
        id="x",
        description="d",
        condition={},
        effect=RuleEffect.ALLOW,
    )
    with pytest.raises(ValidationError):
        rule.id = "y"
