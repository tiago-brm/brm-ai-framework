from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

DraftKind = Literal["rule", "skill"]


class DraftNotFoundError(Exception):
    def __init__(self, client_id: str, kind: DraftKind, draft_id: str) -> None:
        super().__init__(f"draft {draft_id!r} ({kind}) not found for client {client_id!r}")
        self.client_id = client_id
        self.kind = kind
        self.draft_id = draft_id


class DraftStore:
    """Append-only ledger of rule/skill drafts, one file per kind per client.

    Each line is a full snapshot of a draft's state at that point (creation
    or status change) — folding to "current state" is just "last line per
    draft_id wins", same last-write-wins reasoning as the vault's conflict
    sidecars, no partial-record merging.
    """

    def __init__(self, config_root: Path) -> None:
        self._config_root = Path(config_root)

    def _path(self, client_id: str, kind: DraftKind) -> Path:
        return self._config_root / client_id / "config" / f"{kind}s.draft.jsonl"

    def _append_record(self, client_id: str, kind: DraftKind, record: dict[str, Any]) -> None:
        path = self._path(client_id, kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")

    def _current_records(self, client_id: str, kind: DraftKind) -> dict[str, dict[str, Any]]:
        path = self._path(client_id, kind)
        if not path.exists():
            return {}

        current: dict[str, dict[str, Any]] = {}
        with open(path) as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                record = json.loads(line)
                current[record["draft_id"]] = record
        return current

    def create(
        self,
        client_id: str,
        kind: DraftKind,
        draft_id: str,
        from_note_id: str,
        generated_at: str,
        generated_by: str,
        content: dict[str, Any],
    ) -> dict[str, Any]:
        record = {
            "draft_id": draft_id,
            "from_note_id": from_note_id,
            "generated_at": generated_at,
            "generated_by": generated_by,
            "kind": kind,
            "content": content,
            "status": "pending",
            "reviewed_by": None,
            "reviewed_at": None,
        }
        self._append_record(client_id, kind, record)
        return record

    def get_draft(self, client_id: str, kind: DraftKind, draft_id: str) -> dict[str, Any] | None:
        return self._current_records(client_id, kind).get(draft_id)

    def list_drafts(
        self, client_id: str, kind: DraftKind, status: str | None = None
    ) -> list[dict[str, Any]]:
        records = list(self._current_records(client_id, kind).values())
        if status is not None:
            records = [r for r in records if r["status"] == status]
        records.sort(key=lambda r: r["generated_at"])
        return records

    def mark_status(
        self,
        client_id: str,
        kind: DraftKind,
        draft_id: str,
        status: str,
        reviewed_by: str | None = None,
        reviewed_at: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_draft(client_id, kind, draft_id)
        if current is None:
            raise DraftNotFoundError(client_id, kind, draft_id)

        updated = {
            **current,
            "status": status,
            "reviewed_by": reviewed_by,
            "reviewed_at": reviewed_at,
        }
        self._append_record(client_id, kind, updated)
        return updated
