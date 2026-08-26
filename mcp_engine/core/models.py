from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuleEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class Rule(_StrictModel):
    id: str
    description: str
    condition: dict[str, Any]
    effect: RuleEffect
    message: str | None = None


class RuleSet(_StrictModel):
    schema_version: Literal[1]
    client_id: str
    rules: tuple[Rule, ...] = ()


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class SkillDefinition(_StrictModel):
    name: str
    description: str
    method: HttpMethod
    url_template: str
    allowed_domains: tuple[str, ...] = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = 30.0


class SkillSet(_StrictModel):
    schema_version: Literal[1]
    client_id: str
    skills: tuple[SkillDefinition, ...] = ()


class AuditDecision(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    PENDING_APPROVAL = "pending_approval"


class AuditEvent(_StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    client_id: str
    occurred_at: datetime
    actor: str
    action: str
    decision: AuditDecision
    rule_id: str | None = None
    payload_digest: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
