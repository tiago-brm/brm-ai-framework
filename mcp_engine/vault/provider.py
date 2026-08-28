from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from mcp_engine.core.models import VaultNote


@runtime_checkable
class VaultProvider(Protocol):
    def search(self, client_id: str, query: str, limit: int = 10) -> tuple[VaultNote, ...]: ...

    def get_note(self, client_id: str, note_id: str) -> VaultNote | None: ...

    def add_note(
        self,
        client_id: str,
        title: str,
        content: str,
        frontmatter: dict[str, Any] | None = None,
    ) -> VaultNote: ...

    def get_related(self, client_id: str, note_id: str) -> tuple[VaultNote, ...]: ...

    def list_notes(self, client_id: str) -> tuple[VaultNote, ...]: ...

    def reindex(self, client_id: str) -> int: ...
