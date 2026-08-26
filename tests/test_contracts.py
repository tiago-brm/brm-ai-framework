from mcp_engine.core.contracts import AuditSink, RuleProvider, SkillProvider
from mcp_engine.core.models import AuditEvent, RuleSet, SkillSet


class InMemoryRuleProvider:
    def __init__(self, ruleset: RuleSet) -> None:
        self._ruleset = ruleset

    def get_rules(self, client_id: str) -> RuleSet:
        return self._ruleset


class InMemorySkillProvider:
    def __init__(self, skillset: SkillSet) -> None:
        self._skillset = skillset

    def get_skills(self, client_id: str) -> SkillSet:
        return self._skillset


class InMemoryAuditSink:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self._events.append(event)

    def iter_events(self, client_id: str):
        return (e for e in self._events if e.client_id == client_id)


def test_in_memory_rule_provider_satisfies_protocol():
    provider = InMemoryRuleProvider(RuleSet(schema_version=1, client_id="acme"))
    assert isinstance(provider, RuleProvider)


def test_in_memory_skill_provider_satisfies_protocol():
    provider = InMemorySkillProvider(SkillSet(schema_version=1, client_id="acme"))
    assert isinstance(provider, SkillProvider)


def test_in_memory_audit_sink_satisfies_protocol():
    assert isinstance(InMemoryAuditSink(), AuditSink)


def test_object_missing_methods_does_not_satisfy_protocol():
    class NotAProvider:
        pass

    assert not isinstance(NotAProvider(), RuleProvider)
    assert not isinstance(NotAProvider(), SkillProvider)
    assert not isinstance(NotAProvider(), AuditSink)
