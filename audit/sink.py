from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, Iterator

from mcp_engine.core.auth import get_current_user, get_trace_id
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


def _last_line(f) -> str | None:
    """Última linha não vazia do arquivo aberto em binário, lendo do fim."""
    f.seek(0, os.SEEK_END)
    end = f.tell()
    if end == 0:
        return None
    chunk = 4096
    data = b""
    pos = end
    while pos > 0:
        step = min(chunk, pos)
        pos -= step
        f.seek(pos)
        data = f.read(step) + data
        stripped = data.rstrip(b"\n")
        if b"\n" in stripped or pos == 0:
            break
    lines = [ln for ln in data.split(b"\n") if ln]
    return lines[-1].decode("utf-8") if lines else None


class FileSystemAuditSink:
    def __init__(self, data_root: Path) -> None:
        self._data_root = Path(data_root)

    def _path(self, client_id: str) -> Path:
        return self._data_root / client_id / "audit.jsonl"

    def _with_context(self, event: AuditEvent) -> AuditEvent:
        """Garante que nenhum evento sai sem identidade e rastro (spec 020)."""
        updates: dict[str, str] = {}
        if event.user_email is None or event.role is None:
            user = get_current_user()
            if event.user_email is None:
                updates["user_email"] = user.identity
            if event.role is None:
                updates["role"] = user.role.value
        if event.trace_id is None:
            trace_id = get_trace_id()
            if trace_id is not None:
                updates["trace_id"] = trace_id
        return event.model_copy(update=updates) if updates else event

    def record(self, event: AuditEvent) -> None:
        event = self._with_context(event)
        path = self._path(event.client_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = event.model_dump(mode="json")

        with open(path, "a+b") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                last = _last_line(f)
                payload["previous_hash"] = _hash_line(last) if last else None
                f.seek(0, os.SEEK_END)
                f.write((json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

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
