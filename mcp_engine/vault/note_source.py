from __future__ import annotations

from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

from mcp_engine.core.models import VaultNote
from mcp_engine.vault.markdown import NoteParseError, parse_note


@runtime_checkable
class NoteSource(Protocol):
    def list_notes(self) -> Iterable[VaultNote]: ...

    def get_note(self, note_id: str) -> VaultNote | None: ...


class FilesystemNoteSource:
    def __init__(self, vault_root: Path, client_id: str) -> None:
        self._vault_root = Path(vault_root)
        self._client_id = client_id

    def list_notes(self) -> Iterable[VaultNote]:
        if not self._vault_root.exists():
            return

        for note_file in self._vault_root.rglob("*.md"):
            try:
                yield parse_note(note_file, self._vault_root, self._client_id)
            except NoteParseError:
                continue

    def get_note(self, note_id: str) -> VaultNote | None:
        note_path = self._vault_root / note_id
        if not note_path.exists():
            return None

        try:
            return parse_note(note_path, self._vault_root, self._client_id)
        except NoteParseError:
            return None
