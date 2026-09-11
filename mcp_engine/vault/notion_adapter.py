from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import httpx

from mcp_engine.core.config_loader import FileSystemClientConfigProvider
from mcp_engine.core.contracts import AuditSink
from mcp_engine.core.models import AuditDecision, AuditEvent, VaultNote
from mcp_engine.vault.embeddings import EmbeddingProvider, LocalEmbeddingProvider
from mcp_engine.vault.index import VaultIndex

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionConfigError(Exception):
    def __init__(self, client_id: str, missing_field: str) -> None:
        super().__init__(
            f"client {client_id!r} is configured for vault_provider=notion but is "
            f"missing {missing_field!r} in client_config.yaml"
        )
        self.client_id = client_id
        self.missing_field = missing_field


def _plain_text(rich_text: list[dict[str, Any]] | None) -> str:
    return "".join(fragment.get("plain_text", "") for fragment in rich_text or [])


_HEADING_PREFIX = {"heading_1": "# ", "heading_2": "## ", "heading_3": "### "}


def _block_to_text(block: dict[str, Any]) -> str | None:
    block_type = block.get("type")
    data = block.get(block_type, {}) if block_type else {}

    if block_type == "paragraph":
        return _plain_text(data.get("rich_text"))
    if block_type in _HEADING_PREFIX:
        return _HEADING_PREFIX[block_type] + _plain_text(data.get("rich_text"))
    if block_type == "bulleted_list_item":
        return "- " + _plain_text(data.get("rich_text"))
    if block_type == "numbered_list_item":
        return "1. " + _plain_text(data.get("rich_text"))
    if block_type == "quote":
        return "> " + _plain_text(data.get("rich_text"))
    if block_type == "code":
        return _plain_text(data.get("rich_text"))
    if block_type == "to_do":
        box = "[x]" if data.get("checked") else "[ ]"
        return f"- {box} " + _plain_text(data.get("rich_text"))
    return None


def _extract_title(properties: dict[str, Any]) -> str:
    for prop in properties.values():
        if prop.get("type") == "title":
            return _plain_text(prop.get("title"))
    return ""


def _parse_notion_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _slugify(title: str) -> str:
    # Unlike obsidian_adapter's _slugify, underscores pass through unchanged
    # (only whitespace collapses to "-") — a Notion "folder" page titled
    # e.g. "config_drafts" must produce that exact note_id segment, since
    # mcp_engine/vault/interpreter.py's CONFIG_DRAFTS_PREFIX is a literal
    # string match against the Obsidian directory name convention.
    slug = re.sub(r"[^\w\s-]", "", title.lower()).strip()
    slug = re.sub(r"\s+", "-", slug)
    return slug or "nota"


class NotionNoteSource:
    """Notion pages, exposed with Obsidian-shaped note_ids ("folder/slug",
    no extension) instead of raw page UUIDs. This is what lets
    `mcp_engine/vault/interpreter.py`'s `config_drafts/` prefix convention
    (and any other note_id-as-path assumption inherited from the Obsidian
    adapter) keep working unmodified when a client's vault_provider is
    "notion" — Notion itself has no concept of a path, so this class
    reconstructs one from the page tree's title hierarchy and caches the
    note_id -> page_id mapping built while walking it.
    """

    def __init__(
        self,
        token: str,
        root_page_id: str,
        client_id: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._root_page_id = root_page_id
        self._client_id = client_id
        self._client = client or httpx.Client(
            base_url=NOTION_API_BASE,
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        self._path_to_id: dict[str, str] = {}

    def _iter_children(self, block_id: str) -> Iterable[dict[str, Any]]:
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            response = self._client.get(f"/blocks/{block_id}/children", params=params)
            response.raise_for_status()
            data = response.json()
            yield from data.get("results", [])
            if not data.get("has_more"):
                return
            cursor = data.get("next_cursor")

    def _walk_page_tree(self, block_id: str, prefix: str = "") -> list[tuple[str, str, str, str]]:
        pages: list[tuple[str, str, str, str]] = []
        for block in self._iter_children(block_id):
            if block.get("type") == "child_page":
                page_id = block["id"]
                title = block.get("child_page", {}).get("title", "")
                note_id = f"{prefix}{_slugify(title)}"
                updated_at = block["last_edited_time"]
                pages.append((page_id, note_id, title, updated_at))
                pages.extend(self._walk_page_tree(page_id, prefix=f"{note_id}/"))
        return pages

    def _render_page_content(self, page_id: str) -> str:
        lines = []
        for block in self._iter_children(page_id):
            if block.get("type") == "child_page":
                continue
            text = _block_to_text(block)
            if text:
                lines.append(text)
        return "\n\n".join(lines)

    def _refresh_path_index(self) -> list[tuple[str, str, str, str]]:
        entries = self._walk_page_tree(self._root_page_id)
        self._path_to_id = {note_id: page_id for page_id, note_id, _title, _ts in entries}
        return entries

    def _resolve(self, note_id: str) -> str | None:
        page_id = self._path_to_id.get(note_id)
        if page_id is not None:
            return page_id
        self._refresh_path_index()
        return self._path_to_id.get(note_id)

    def list_notes(self) -> Iterable[VaultNote]:
        for page_id, note_id, title, updated_at in self._refresh_path_index():
            content = self._render_page_content(page_id)
            yield VaultNote(
                note_id=note_id,
                client_id=self._client_id,
                title=title,
                content=content,
                frontmatter={},
                links=(),
                tags=(),
                updated_at=_parse_notion_datetime(updated_at),
                content_hash=_content_hash(content),
            )

    def get_note(self, note_id: str) -> VaultNote | None:
        page_id = self._resolve(note_id)
        if page_id is None:
            return None

        response = self._client.get(f"/pages/{page_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        page = response.json()

        content = self._render_page_content(page_id)
        return VaultNote(
            note_id=note_id,
            client_id=self._client_id,
            title=_extract_title(page.get("properties", {})),
            content=content,
            frontmatter={},
            links=(),
            tags=(),
            updated_at=_parse_notion_datetime(page["last_edited_time"]),
            content_hash=_content_hash(content),
        )

    def create_page(self, title: str, content: str) -> str:
        response = self._client.post(
            "/pages",
            json={
                "parent": {"type": "page_id", "page_id": self._root_page_id},
                "properties": {"title": {"title": [{"text": {"content": title}}]}},
                "children": _content_to_blocks(content),
            },
        )
        response.raise_for_status()
        page_id = str(response.json()["id"])

        note_id = _slugify(title)
        self._path_to_id[note_id] = page_id
        return note_id


def _content_to_blocks(content: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("### "):
            blocks.append(_rich_text_block("heading_3", line[4:]))
        elif line.startswith("## "):
            blocks.append(_rich_text_block("heading_2", line[3:]))
        elif line.startswith("# "):
            blocks.append(_rich_text_block("heading_1", line[2:]))
        elif line.startswith("- "):
            blocks.append(_rich_text_block("bulleted_list_item", line[2:]))
        else:
            blocks.append(_rich_text_block("paragraph", line))

    return blocks


def _rich_text_block(block_type: str, text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


class NotionVaultProvider:
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
        self._sources: dict[str, NotionNoteSource] = {}

    def _source(self, client_id: str) -> NotionNoteSource:
        if client_id not in self._sources:
            client_config = self._client_config_provider.get_client_config(client_id)
            # Same "env var for secrets" convention as SMTP_*/RESEND_BRM_API_KEY
            # in email_sender.py — lets client_config.yaml stay committable
            # without a live Notion integration secret in it.
            token = client_config.notion_token or os.getenv("NOTION_TOKEN")
            if not token:
                raise NotionConfigError(client_id, "notion_token")
            if not client_config.notion_root_page_id:
                raise NotionConfigError(client_id, "notion_root_page_id")

            self._sources[client_id] = NotionNoteSource(
                token=token,
                root_page_id=client_config.notion_root_page_id,
                client_id=client_id,
            )
        return self._sources[client_id]

    def _index(self, client_id: str) -> VaultIndex:
        if client_id not in self._indices:
            db_path = self._data_root / client_id / "vault_index.sqlite3"
            self._indices[client_id] = VaultIndex(
                self._source(client_id), db_path, self._embedding_provider, client_id
            )
        return self._indices[client_id]

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
        note_id = self._source(client_id).create_page(title, content)
        self._index(client_id).reindex()

        self._record_audit(
            client_id, "vault_add_note", AuditDecision.ALLOWED, {"note_id": note_id}
        )

        new_note = self._index(client_id).get_note(note_id)
        if new_note is None:
            raise RuntimeError(f"Failed to read newly created Notion page: {note_id}")
        return new_note

    def get_related(self, client_id: str, note_id: str) -> tuple[VaultNote, ...]:
        return self._index(client_id).get_related(note_id)

    def list_notes(self, client_id: str) -> tuple[VaultNote, ...]:
        return self._index(client_id).list_notes()

    def reindex(self, client_id: str) -> int:
        return self._index(client_id).reindex()
