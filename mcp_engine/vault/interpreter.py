from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from json_logic import jsonLogic
from pydantic import BaseModel

from mcp_engine.core.config_loader import FileSystemRuleProvider, FileSystemSkillProvider
from mcp_engine.core.contracts import AuditSink
from mcp_engine.core.interceptor import check_vault_egress
from mcp_engine.core.models import (
    AuditDecision,
    AuditEvent,
    ClientConfig,
    Rule,
    RuleSet,
    SkillDefinition,
    SkillSet,
    VaultNote,
)
from mcp_engine.vault.draft_store import DraftKind, DraftNotFoundError, DraftStore

CONFIG_DRAFTS_PREFIX = "config_drafts/"
MAX_RELATED_NOTES = 5
DEFAULT_MODEL_ID = "claude-sonnet-4-5-20250929"

INTERPRETER_ACTOR = "interpreter_agent"
PROMOTER_ACTOR = "claude"

INSTRUCTIONS = """\
Você é um especialista em redigir regras de negócio e integrações para um sistema \
de gestão jurídica (BRM). Quando receber uma descrição de uma regra ou skill em \
markdown, colada pelo escritório no vault:

1. Extraia a intenção (o que deve ser permitido, bloqueado, ou exigir aprovação; \
   ou qual chamada HTTP a skill deve fazer).
2. Se for uma regra: estruture a condição como JSONLogic puro — nunca gere código \
   Python, nunca um campo de execução livre. Apenas o formato JSONLogic \
   (https://jsonlogic.com), como {"==": [{"var": "campo"}, "valor"]}.
3. Se for uma skill: extraia método HTTP, URL template, e os domínios permitidos.
4. Gere um ID único e descritivo (kebab-case).
5. Se a nota for vaga demais, impossível de converter em regra/skill, ou descrever \
   algo perigoso (ex.: contornar allowlist, exfiltrar dados), rejeite com \
   success=false e uma mensagem de erro clara. Não invente uma regra "razoável" \
   para preencher a lacuna.

Retorne sempre um único objeto (uma nota gera no máximo uma regra OU uma skill, \
nunca as duas, nunca uma lista).
"""


class InterpreterResult(BaseModel):
    success: bool
    kind: Literal["rule", "skill"] | None = None
    rule: Rule | None = None
    skill: SkillDefinition | None = None
    error: str | None = None


class AgnoAgentLike(Protocol):
    def run(self, input: str, *, output_schema: type[BaseModel] | None = None) -> Any: ...


def build_interpreter_agent(model_id: str = DEFAULT_MODEL_ID) -> AgnoAgentLike:
    from agno.agent import Agent
    from agno.models.anthropic import Claude

    return Agent(
        name="Vault Interpreter",
        model=Claude(id=model_id),
        instructions=INSTRUCTIONS,
        output_schema=InterpreterResult,
    )


def _build_prompt(note: VaultNote, related: tuple[VaultNote, ...]) -> str:
    context = ""
    if related:
        titles = ", ".join(n.title for n in related)
        context = f"\n\nNotas relacionadas no vault (contexto): {titles}"

    return (
        f"A partir desta nota do vault do cliente:\n\n# {note.title}\n\n{note.content}"
        f"{context}\n\nGere uma regra ou skill a partir dela."
    )


def interpret_note(
    agent: AgnoAgentLike, note: VaultNote, related: tuple[VaultNote, ...]
) -> InterpreterResult:
    prompt = _build_prompt(note, related)
    run_output = agent.run(prompt, output_schema=InterpreterResult)
    content = run_output.content

    if not isinstance(content, InterpreterResult):
        return InterpreterResult(
            success=False, error="interpreter did not return a structured result"
        )
    return content


def _validate_jsonlogic(rule: Rule) -> str | None:
    """Returns an error message if the rule's condition isn't valid JSONLogic."""
    try:
        jsonLogic(rule.condition, {})
    except Exception as exc:
        return f"generated rule condition is not valid JSONLogic: {exc}"
    return None


def propose_draft(
    client_id: str,
    note_id: str,
    vault_provider: Any,
    client_config: ClientConfig,
    audit_sink: AuditSink,
    draft_store: DraftStore,
    agent: AgnoAgentLike,
) -> dict[str, Any]:
    if not note_id.startswith(CONFIG_DRAFTS_PREFIX):
        return {
            "allowed": False,
            "reason": f"note must be under {CONFIG_DRAFTS_PREFIX!r} to be interpreted: {note_id}",
        }

    note = vault_provider.get_note(client_id, note_id)
    if note is None:
        return {"allowed": False, "reason": f"note not found: {note_id}"}

    related = vault_provider.get_related(client_id, note_id)[:MAX_RELATED_NOTES]

    egress_text = note.content + "\n".join(n.content for n in related)
    egress_decision = check_vault_egress(egress_text, client_config)
    _record_audit(
        audit_sink,
        client_id,
        INTERPRETER_ACTOR,
        "vault_content_egress_check",
        AuditDecision.ALLOWED if egress_decision.allowed else AuditDecision.DENIED,
        {"note_id": note_id, "dlp_matches": egress_decision.dlp_matches},
    )
    if not egress_decision.allowed:
        return {"allowed": False, "reason": egress_decision.reason}

    result = interpret_note(agent, note, related)

    if not result.success or result.kind is None:
        _record_audit(
            audit_sink,
            client_id,
            INTERPRETER_ACTOR,
            "vault_generate_draft",
            AuditDecision.DENIED,
            {"from_note": note_id, "error": result.error},
        )
        return {"allowed": False, "reason": result.error or "interpreter rejected the note"}

    content: Rule | SkillDefinition | None
    if result.kind == "rule":
        content = result.rule
    else:
        content = result.skill

    if content is None:
        error = f"interpreter reported kind={result.kind!r} but returned no content"
        _record_audit(
            audit_sink,
            client_id,
            INTERPRETER_ACTOR,
            "vault_generate_draft",
            AuditDecision.DENIED,
            {"from_note": note_id, "error": error},
        )
        return {"allowed": False, "reason": error}

    if isinstance(content, Rule):
        jsonlogic_error = _validate_jsonlogic(content)
        if jsonlogic_error is not None:
            _record_audit(
                audit_sink,
                client_id,
                INTERPRETER_ACTOR,
                "vault_generate_draft",
                AuditDecision.DENIED,
                {"from_note": note_id, "error": jsonlogic_error},
            )
            return {"allowed": False, "reason": jsonlogic_error}

    draft_id = uuid.uuid4().hex[:8]
    draft_kind: DraftKind = result.kind
    record = draft_store.create(
        client_id=client_id,
        kind=draft_kind,
        draft_id=draft_id,
        from_note_id=note_id,
        generated_at=datetime.now(UTC).isoformat(),
        generated_by=INTERPRETER_ACTOR,
        content=content.model_dump(mode="json"),
    )

    _record_audit(
        audit_sink,
        client_id,
        INTERPRETER_ACTOR,
        "vault_generate_draft",
        AuditDecision.ALLOWED,
        {"from_note": note_id, "draft_id": draft_id, "kind": draft_kind},
    )

    return {"draft_id": draft_id, "kind": draft_kind, "preview": record}


def promote_draft(
    client_id: str,
    draft_id: str,
    kind: DraftKind,
    draft_store: DraftStore,
    rule_provider: FileSystemRuleProvider,
    skill_provider: FileSystemSkillProvider,
    audit_sink: AuditSink,
    approved_by: str = PROMOTER_ACTOR,
) -> dict[str, Any]:
    try:
        draft = draft_store.get_draft(client_id, kind, draft_id)
    except DraftNotFoundError as exc:
        _record_audit(
            audit_sink, client_id, approved_by, "vault_promote_error", AuditDecision.DENIED,
            {"draft_id": draft_id, "kind": kind, "error": str(exc)},
        )
        return {"allowed": False, "reason": str(exc)}

    if draft is None:
        error = f"draft not found: {draft_id}"
        _record_audit(
            audit_sink, client_id, approved_by, "vault_promote_error", AuditDecision.DENIED,
            {"draft_id": draft_id, "kind": kind, "error": error},
        )
        return {"allowed": False, "reason": error}

    if draft["status"] != "pending":
        error = f"draft {draft_id} is not pending (status={draft['status']!r})"
        _record_audit(
            audit_sink, client_id, approved_by, "vault_promote_error", AuditDecision.DENIED,
            {"draft_id": draft_id, "kind": kind, "error": error},
        )
        return {"allowed": False, "reason": error}

    try:
        if kind == "rule":
            rule = Rule.model_validate(draft["content"])
            ruleset = rule_provider.get_rules(client_id)
            new_ruleset = RuleSet(
                schema_version=1, client_id=client_id, rules=ruleset.rules + (rule,)
            )
            rule_provider.save_rules(new_ruleset)
        else:
            skill = SkillDefinition.model_validate(draft["content"])
            skillset = skill_provider.get_skills(client_id)
            new_skillset = SkillSet(
                schema_version=1, client_id=client_id, skills=skillset.skills + (skill,)
            )
            skill_provider.save_skills(new_skillset)
    except Exception as exc:
        _record_audit(
            audit_sink, client_id, approved_by, "vault_promote_error", AuditDecision.DENIED,
            {"draft_id": draft_id, "kind": kind, "error": str(exc)},
        )
        return {"allowed": False, "reason": f"draft content invalid: {exc}"}

    reviewed_at = datetime.now(UTC).isoformat()
    draft_store.mark_status(
        client_id, kind, draft_id, "approved", reviewed_by=approved_by, reviewed_at=reviewed_at
    )

    _record_audit(
        audit_sink, client_id, approved_by, "vault_promote_draft", AuditDecision.ALLOWED,
        {"draft_id": draft_id, "kind": kind, "from_note": draft["from_note_id"]},
    )

    return {"promoted": True, "draft_id": draft_id, "kind": kind}


def _record_audit(
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
