from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import frontmatter as fm

from mcp_engine.core.models import VaultNote

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")
INLINE_TAG_RE = re.compile(r"(?<!\S)#([A-Za-z0-9_/-]+)")


class NoteParseError(Exception):
    def __init__(self, note_id: str, path: Path, cause: Exception) -> None:
        super().__init__(f"failed to parse note {note_id!r} from {path}: {cause}")
        self.note_id = note_id
        self.path = path
        self.__cause__ = cause


def link_key(name: str) -> str:
    # Obsidian resolves [[wikilinks]] by basename: [[peticao-inicial]] finds
    # modelos/peticao-inicial.md. Folder and .md extension are not part of the key.
    return PurePosixPath(name.strip()).stem.lower()


def _extract_links(content: str) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for match in WIKILINK_RE.finditer(content):
        name = match.group(1).strip()
        if name:
            seen[name] = None
    return tuple(seen)


def _extract_tags(content: str, frontmatter_tags: Any) -> tuple[str, ...]:
    seen: dict[str, None] = {}

    if isinstance(frontmatter_tags, str):
        candidates: list[Any] = [frontmatter_tags]
    elif isinstance(frontmatter_tags, (list, tuple)):
        candidates = list(frontmatter_tags)
    else:
        candidates = []

    for tag in candidates:
        tag_str = str(tag).strip()
        if tag_str:
            seen[tag_str] = None

    for match in INLINE_TAG_RE.finditer(content):
        seen[match.group(1)] = None

    return tuple(seen)


def parse_note(path: Path, vault_root: Path, client_id: str) -> VaultNote:
    note_id = path.relative_to(vault_root).as_posix()

    try:
        post = fm.loads(path.read_text(encoding="utf-8"))
        metadata = dict(post.metadata)
        content = post.content
        title = metadata.get("title") or path.stem

        return VaultNote(
            note_id=note_id,
            client_id=client_id,
            title=str(title),
            content=content,
            frontmatter=metadata,
            links=_extract_links(content),
            tags=_extract_tags(content, metadata.get("tags")),
            updated_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
    except Exception as exc:
        raise NoteParseError(note_id, path, exc) from exc


def serialize_note(title: str, content: str, frontmatter: dict[str, Any] | None = None) -> str:
    metadata = dict(frontmatter or {})
    metadata["title"] = title
    post = fm.Post(content, **metadata)
    return fm.dumps(post) + "\n"
