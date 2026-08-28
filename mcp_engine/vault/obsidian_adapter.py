from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp_engine.core.config_loader import FileSystemClientConfigProvider
from mcp_engine.core.contracts import AuditSink
from mcp_engine.core.models import AuditDecision, AuditEvent, VaultNote
from mcp_engine.vault.embeddings import EmbeddingProvider, LocalEmbeddingProvider
from mcp_engine.vault.index import VaultIndex
from mcp_engine.vault.markdown import parse_note, serialize_note
from mcp_engine.vault.watcher import VaultWatcher


class VaultRootNotFoundError(Exception):
    def __init__(self, client_id: str, vault_root: Path) -> None:
        super().__init__(
            f"client {client_id!r} declares vault_root={str(vault_root)!r}, "
            f"but that directory does not exist"
        )
        self.client_id = client_id
        self.vault_root = vault_root


def _slugify(title: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", title.lower()).strip()
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug or "nota"


class ObsidianVaultProvider:
    def __init__(
        self,
        config_root: Path,
        data_root: Path,
        audit_sink: AuditSink,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._config_root = Path(config_root)
        self._data_root = Path(data_root)
        self._audit_sink = audit_sink
        self._embedding_provider = embedding_provider or LocalEmbeddingProvider()
        self._client_config_provider = FileSystemClientConfigProvider(self._config_root)
        self._indices: dict[str, VaultIndex] = {}
        self._watchers: dict[str, VaultWatcher] = {}

    def _vault_root(self, client_id: str) -> tuple[Path, bool]:
        """Returns the vault root and whether it came from explicit config."""
        try:
            client_config = self._client_config_provider.get_client_config(client_id)
            if client_config.vault_root:
                return Path(client_config.vault_root).expanduser(), True
        except Exception:
            pass
        return self._config_root / client_id / "vault", False

    def _index(self, client_id: str) -> VaultIndex:
        if client_id not in self._indices:
            vault_root, is_explicit = self._vault_root(client_id)

            # An explicit vault_root points at a vault the client already owns;
            # creating it silently would hide a typo behind an empty vault.
            # The convention path is ours to create.
            if is_explicit and not vault_root.is_dir():
                raise VaultRootNotFoundError(client_id, vault_root)

            vault_root.mkdir(parents=True, exist_ok=True)
            db_path = self._data_root / client_id / "vault_index.sqlite3"
            self._indices[client_id] = VaultIndex(
                vault_root, db_path, self._embedding_provider, client_id
            )
        return self._indices[client_id]

    @staticmethod
    def _sidecar_path(note_path: Path, kind: str) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
        return note_path.with_name(f"{note_path.stem}.md-brm-{kind}-{stamp}")

    def _record_audit(
        self,
        client_id: str,
        action: str,
        decision: AuditDecision,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._audit_sink.record(
            AuditEvent(
                client_id=client_id,
                occurred_at=datetime.now(UTC),
                actor="vault_provider",
                action=action,
                decision=decision,
                details=details or {},
            )
        )

    def search(self, client_id: str, query: str, limit: int = 10) -> tuple[VaultNote, ...]:
        return self._index(client_id).search(query, limit)

    def get_note(self, client_id: str, note_id: str) -> VaultNote | None:
        return self._index(client_id).get_note(note_id)

    def add_note(
        self,
        client_id: str,
        title: str,
        content: str,
        frontmatter: dict[str, Any] | None = None,
    ) -> VaultNote:
        index = self._index(client_id)
        vault_root, _ = self._vault_root(client_id)
        note_id = f"{_slugify(title)}.md"
        note_path = vault_root / note_id

        serialized = serialize_note(title, content, frontmatter)

        if note_path.exists():
            try:
                disk_note = parse_note(note_path, vault_root, client_id)
            except Exception:
                # The client's file is on disk but unreadable (broken YAML, bad
                # bytes). We can't compare hashes, and leaving it in place would
                # wedge this note_id forever. Move the original aside byte-for-byte
                # and let the engine's version take the name. Distinct suffix from
                # the conflict sidecar because this backup holds the CLIENT's
                # content, not the engine's.
                salvage_path = self._sidecar_path(note_path, "unparseable")
                salvage_path.write_bytes(note_path.read_bytes())

                self._record_audit(
                    client_id,
                    "vault_write_conflict",
                    AuditDecision.DENIED,
                    {
                        "note_id": note_id,
                        "salvage_path": salvage_path.name,
                        "reason": "existing note could not be parsed",
                    },
                )
            else:
                indexed_hash = index.get_indexed_content_hash(note_id)

                if indexed_hash != disk_note.content_hash:
                    conflict_path = self._sidecar_path(note_path, "conflict")
                    conflict_path.write_text(serialized, encoding="utf-8")

                    self._record_audit(
                        client_id,
                        "vault_write_conflict",
                        AuditDecision.DENIED,
                        {"note_id": note_id, "conflict_path": conflict_path.name},
                    )

                    return disk_note

        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text(serialized, encoding="utf-8")
        index.reindex()

        self._record_audit(
            client_id, "vault_add_note", AuditDecision.ALLOWED, {"note_id": note_id}
        )

        new_note = index.get_note(note_id)
        if new_note is None:
            raise RuntimeError(f"Failed to read newly written note: {note_id}")
        return new_note

    def get_related(self, client_id: str, note_id: str) -> tuple[VaultNote, ...]:
        return self._index(client_id).get_related(note_id)

    def list_notes(self, client_id: str) -> tuple[VaultNote, ...]:
        return self._index(client_id).list_notes()

    def reindex(self, client_id: str) -> int:
        return self._index(client_id).reindex()

    def start_watching(self, client_id: str) -> None:
        if client_id in self._watchers:
            return

        # Reuses _index's validation: an explicit vault_root must already exist.
        self._index(client_id)
        vault_root, _ = self._vault_root(client_id)

        def on_change(path: Path, change: str) -> None:
            self._on_watcher_change(client_id, path, change)

        watcher = VaultWatcher(vault_root=vault_root, on_change=on_change)
        watcher.start()
        self._watchers[client_id] = watcher

    def stop_watching(self, client_id: str) -> None:
        watcher = self._watchers.pop(client_id, None)
        if watcher:
            watcher.stop()

    def _on_watcher_change(self, client_id: str, path: Path, change: str) -> None:
        index = self._index(client_id)
        vault_root, _ = self._vault_root(client_id)

        try:
            note_id = path.relative_to(vault_root).as_posix()
        except ValueError:
            return

        if change == "deleted":
            old_hash = index.get_indexed_content_hash(note_id)
            if old_hash is None:
                return
            index.reindex()
            self._record_audit(
                client_id,
                "vault_external_edit",
                AuditDecision.ALLOWED,
                {"note_id": note_id, "change": "deleted"},
            )
            return

        old_hash = index.get_indexed_content_hash(note_id)
        index.reindex()
        new_hash = index.get_indexed_content_hash(note_id)

        if old_hash == new_hash:
            return

        self._record_audit(
            client_id,
            "vault_external_edit",
            AuditDecision.ALLOWED,
            {"note_id": note_id, "change": change},
        )
