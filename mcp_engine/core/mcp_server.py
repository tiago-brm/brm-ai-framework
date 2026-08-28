from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from audit.sink import FileSystemAuditSink
from mcp_engine.core.config_loader import (
    FileSystemClientConfigProvider,
    FileSystemRuleProvider,
    FileSystemSkillProvider,
)
from mcp_engine.core.interceptor import intercept
from mcp_engine.core.models import AuditDecision, AuditEvent, RuleEffect, SkillCall
from mcp_engine.core.rule_evaluator import evaluate
from mcp_engine.vault.obsidian_adapter import ObsidianVaultProvider

DEFAULT_ACTOR = "claude"


def _render_url(url_template: str, arguments: dict[str, Any]) -> str:
    try:
        return url_template.format(**arguments)
    except (KeyError, IndexError):
        return url_template


class BRMEngine:
    def __init__(
        self,
        client_id: str,
        config_root: Path,
        data_root: Path,
        vault_provider: Any | None = None,
        vault_watch: bool = False,
    ) -> None:
        self.client_id = client_id
        self._config_root = Path(config_root)
        self._data_root = Path(data_root)

        self._rule_provider = FileSystemRuleProvider(self._config_root)
        self._skill_provider = FileSystemSkillProvider(self._config_root)
        self._client_config_provider = FileSystemClientConfigProvider(self._config_root)
        self._audit_sink = FileSystemAuditSink(self._data_root)
        self._vault_provider = vault_provider or ObsidianVaultProvider(
            self._config_root, self._data_root, self._audit_sink
        )

        if vault_watch and hasattr(self._vault_provider, "start_watching"):
            try:
                self._vault_provider.start_watching(client_id)
            except Exception:
                pass

        # Eagerly load to catch config errors early
        self._ruleset = self._rule_provider.get_rules(client_id)
        self._skillset = self._skill_provider.get_skills(client_id)
        self._client_config = self._client_config_provider.get_client_config(client_id)

    def _find_skill(self, skill_name: str):
        for skill in self._skillset.skills:
            if skill.name == skill_name:
                return skill
        return None

    def _record_audit(
        self,
        actor: str,
        action: str,
        decision: AuditDecision,
        rule_id: str | None = None,
    ) -> None:
        self._audit_sink.record(
            AuditEvent(
                client_id=self.client_id,
                occurred_at=datetime.now(UTC),
                actor=actor,
                action=action,
                decision=decision,
                rule_id=rule_id,
            )
        )

    def call_skill(self, skill_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        actor = DEFAULT_ACTOR

        skill = self._find_skill(skill_name)
        if skill is None:
            self._record_audit(actor, skill_name, AuditDecision.DENIED)
            return {
                "allowed": False,
                "reason": f"skill not found: {skill_name}",
                "result": "",
                "payload_digest": None,
            }

        facts: dict[str, Any] = {
            "skill_name": skill_name,
            "actor": actor,
            "origem_sistema": "mcp",
            **arguments,
        }
        rule_decision = evaluate(self._ruleset, facts)

        if rule_decision.effect is RuleEffect.DENY:
            self._record_audit(actor, skill_name, AuditDecision.DENIED, rule_decision.rule_id)
            return {
                "allowed": False,
                "reason": rule_decision.message,
                "rule_id": rule_decision.rule_id,
                "result": "",
                "payload_digest": None,
            }

        if rule_decision.effect is RuleEffect.REQUIRE_APPROVAL:
            self._record_audit(
                actor, skill_name, AuditDecision.PENDING_APPROVAL, rule_decision.rule_id
            )
            return {
                "allowed": False,
                "reason": "human approval required",
                "rule_id": rule_decision.rule_id,
                "result": "",
                "payload_digest": None,
            }

        url = _render_url(skill.url_template, arguments)
        mock_response = json.dumps(
            {"status": "ok", "skill": skill_name, "arguments": arguments}, sort_keys=True
        )
        skill_call = SkillCall(
            skill_name=skill_name,
            url=url,
            method=skill.method,
            actor=actor,
            request_body=arguments,
            response_text=mock_response,
            response_status=200,
        )

        intercept_decision = intercept(skill_call, self._client_config, self._audit_sink)
        if not intercept_decision.allowed:
            return {
                "allowed": False,
                "reason": intercept_decision.reason,
                "result": "",
                "payload_digest": None,
            }

        return {
            "allowed": True,
            "reason": None,
            "result": mock_response,
            "payload_digest": hashlib.sha256(mock_response.encode("utf-8")).hexdigest(),
        }

    def query_audit_log(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        action: str | None = None,
        decision: str | None = None,
    ) -> dict[str, Any]:
        if start_date:
            start = datetime.fromisoformat(start_date)
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
        else:
            start = None

        if end_date:
            end = datetime.fromisoformat(end_date)
            if end.tzinfo is None:
                end = end.replace(tzinfo=UTC)
        else:
            end = None

        decision_filter = AuditDecision(decision) if decision else None

        events: list[dict[str, Any]] = []
        for event in self._audit_sink.iter_events(self.client_id):
            if start is not None and event.occurred_at < start:
                continue
            if end is not None and event.occurred_at > end:
                continue
            if action is not None and event.action != action:
                continue
            if decision_filter is not None and event.decision != decision_filter:
                continue

            events.append(
                {
                    "occurred_at": event.occurred_at.isoformat(),
                    "actor": event.actor,
                    "action": event.action,
                    "decision": event.decision.value,
                    "rule_id": event.rule_id,
                }
            )

        return {"total": len(events), "events": events}

    def vault_search(self, query: str, limit: int = 10) -> dict[str, Any]:
        notes = self._vault_provider.search(self.client_id, query, limit)
        return {"total": len(notes), "notes": [n.model_dump(mode="json") for n in notes]}

    def vault_add_note(
        self, title: str, content: str, tags: list[str] | None = None
    ) -> dict[str, Any]:
        frontmatter = {"tags": tags} if tags else None
        note = self._vault_provider.add_note(self.client_id, title, content, frontmatter)
        return note.model_dump(mode="json")

    def vault_get_related(self, note_id: str) -> dict[str, Any]:
        notes = self._vault_provider.get_related(self.client_id, note_id)
        return {"total": len(notes), "notes": [n.model_dump(mode="json") for n in notes]}


def build_server(engine: BRMEngine) -> FastMCP:
    server = FastMCP("brm-engine")

    @server.tool()
    def call_skill(skill_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return engine.call_skill(skill_name, arguments)

    @server.tool()
    def query_audit_log(
        start_date: str | None = None,
        end_date: str | None = None,
        action: str | None = None,
        decision: str | None = None,
    ) -> dict[str, Any]:
        return engine.query_audit_log(
            start_date=start_date,
            end_date=end_date,
            action=action,
            decision=decision,
        )

    @server.tool()
    def vault_search(query: str, limit: int = 10) -> dict[str, Any]:
        return engine.vault_search(query, limit)

    @server.tool()
    def vault_add_note(
        title: str, content: str, tags: list[str] | None = None
    ) -> dict[str, Any]:
        return engine.vault_add_note(title, content, tags)

    @server.tool()
    def vault_get_related(note_id: str) -> dict[str, Any]:
        return engine.vault_get_related(note_id)

    return server


def main() -> None:
    engine = BRMEngine(
        client_id=os.getenv("CLIENT_ID", "example"),
        config_root=Path(os.getenv("CONFIG_ROOT", "clients")),
        data_root=Path(os.getenv("DATA_ROOT", "data")),
    )
    server = build_server(engine)
    server.run()


if __name__ == "__main__":
    main()
