from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

from mcp_engine.core.models import AuditEvent, RuleSet, SkillSet


@runtime_checkable
class RuleProvider(Protocol):
    def get_rules(self, client_id: str) -> RuleSet: ...


@runtime_checkable
class SkillProvider(Protocol):
    def get_skills(self, client_id: str) -> SkillSet: ...


@runtime_checkable
class AuditSink(Protocol):
    def record(self, event: AuditEvent) -> None: ...

    def iter_events(self, client_id: str) -> Iterable[AuditEvent]: ...
