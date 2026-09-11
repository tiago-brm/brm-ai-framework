from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any, Protocol

from mcp_engine.core.contracts import AuditSink
from mcp_engine.core.models import AuditDecision, AuditEvent

DEFAULT_ANTHROPIC_MODEL_ID = "claude-sonnet-4-5-20250929"
DEFAULT_OPENROUTER_MODEL_ID = "anthropic/claude-sonnet-4.5"


class AgnoAgentLike(Protocol):
    def run(self, input: str, *, output_schema: type[Any] | None = None) -> Any: ...


def resolve_agent_model(model_id: str | None = None) -> tuple[Any, bool]:
    """Picks a provider by whichever API key is set — Anthropic direct, else
    OpenRouter — so the calling agent isn't locked into one vendor.

    Returns (model, use_json_mode). use_json_mode=True for OpenRouter: its
    schema-constrained structured output collapses free-form dict/str fields
    to empty regardless of strict mode; prompt-based JSON mode doesn't.
    """
    if os.getenv("ANTHROPIC_API_KEY"):
        from agno.models.anthropic import Claude

        return Claude(id=model_id or DEFAULT_ANTHROPIC_MODEL_ID), False

    if os.getenv("OPENROUTER_API_KEY"):
        from agno.models.openrouter import OpenRouter

        # agno's OpenRouter default (1024) truncates mid-document for anything
        # longer than a short rule/skill — the cut JSON then fails to parse.
        return OpenRouter(id=model_id or DEFAULT_OPENROUTER_MODEL_ID, max_tokens=8192), True

    raise RuntimeError("no LLM credentials found: set ANTHROPIC_API_KEY or OPENROUTER_API_KEY")


def record_audit(
    audit_sink: AuditSink,
    client_id: str,
    actor: str,
    action: str,
    decision: AuditDecision,
    details: dict[str, Any],
) -> None:
    audit_sink.record(
        AuditEvent(
            client_id=client_id,
            occurred_at=datetime.now(UTC),
            actor=actor,
            action=action,
            decision=decision,
            details=details,
        )
    )
