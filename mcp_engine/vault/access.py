from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from json_logic import jsonLogic

from mcp_engine.core.models import VaultAccess, VaultNote, VaultPolicy


@dataclass
class AccessResult:
    visible: list[VaultNote] = field(default_factory=list)
    hidden: int = 0
    policies: list[str] = field(default_factory=list)
    privileged: list[str] = field(default_factory=list)


def _facts(note: VaultNote, actor: str, role: str) -> dict[str, Any]:
    frontmatter = dict(note.frontmatter)
    return {
        "actor": actor,
        "actor_role": role,
        "note": {**frontmatter, "note_id": note.note_id},
        "vinculo": str(frontmatter.get("psicologo_responsavel")) == actor,
    }


def _match(policy: VaultPolicy, facts: dict[str, Any]) -> bool:
    try:
        return bool(jsonLogic(policy.when, facts))
    except Exception:
        return False


def _restrict(note: VaultNote, expose: Sequence[str]) -> VaultNote:
    frontmatter = {k: v for k, v in note.frontmatter.items() if k in expose}
    return note.model_copy(
        update={
            "title": str(frontmatter.get("paciente_id") or "(restrito)"),
            "content": "",
            "frontmatter": frontmatter,
            "links": (),
            "tags": (),
            "content_hash": "",
        }
    )


def filter_notes(
    access: VaultAccess | None, notes: Sequence[VaultNote], actor: str, role: str
) -> AccessResult:
    result = AccessResult()
    if access is None:
        result.visible = list(notes)
        return result

    for note in notes:
        facts = _facts(note, actor, role)
        policy = next((p for p in access.policies if _match(p, facts)), None)

        effect = policy.effect if policy else access.default
        if policy:
            result.policies.append(policy.id)
        if effect == "deny":
            result.hidden += 1
            continue
        if effect == "metadata_only" and policy is not None:
            result.visible.append(_restrict(note, policy.expose))
        else:
            result.visible.append(note)
        if policy is not None and policy.audit:
            result.privileged.append(note.note_id)

    result.policies = sorted(set(result.policies))
    return result
