from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Iterator

from mcp_engine.core.models import AuditEvent


class AuditChainError(Exception):
    def __init__(
        self,
        client_id: str,
        line_number: int,
        expected_hash: str | None,
        found_hash: str | None,
    ) -> None:
        super().__init__(
            f"audit chain broken for {client_id!r} at line {line_number}: "
            f"expected previous_hash={expected_hash!r}, found {found_hash!r}"
        )
        self.client_id = client_id
        self.line_number = line_number
        self.expected_hash = expected_hash
        self.found_hash = found_hash


def _hash_line(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


class FileSystemAuditSink:
    def __init__(self, data_root: Path) -> None:
        self._data_root = Path(data_root)

    def _path(self, client_id: str) -> Path:
        return self._data_root / client_id / "audit.jsonl"

    def record(self, event: AuditEvent) -> None:
        path = self._path(event.client_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        previous_hash: str | None = None
        if path.exists():
            lines = path.read_text().splitlines()
            if lines:
                previous_hash = _hash_line(lines[-1])

        payload = event.model_dump(mode="json")
        payload["previous_hash"] = previous_hash

        with open(path, "a") as f:
            f.write(json.dumps(payload, sort_keys=True) + "\n")

    def iter_events(self, client_id: str) -> Iterable[AuditEvent]:
        return self._iter_events(client_id)

    def _iter_events(self, client_id: str) -> Iterator[AuditEvent]:
        path = self._path(client_id)
        if not path.exists():
            return

        previous_hash: str | None = None
        with open(path) as f:
            for line_number, raw_line in enumerate(f, start=1):
                line = raw_line.rstrip("\n")
                if not line:
                    continue

                payload = json.loads(line)
                found_hash = payload.pop("previous_hash", None)

                if found_hash != previous_hash:
                    raise AuditChainError(client_id, line_number, previous_hash, found_hash)

                yield AuditEvent.model_validate(payload)
                previous_hash = _hash_line(line)
