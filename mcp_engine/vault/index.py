from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import sqlite_vec

from mcp_engine.core.models import VaultNote
from mcp_engine.vault.embeddings import EmbeddingProvider
from mcp_engine.vault.markdown import NoteParseError, link_key, parse_note

RRF_CONSTANT = 60


class VaultIndexError(Exception):
    pass


class VaultIndex:
    def __init__(
        self,
        vault_root: Path,
        vault_db_path: Path,
        embedding_provider: EmbeddingProvider,
        client_id: str,
    ) -> None:
        self._vault_root = Path(vault_root)
        self._db_path = Path(vault_db_path)
        self._embedding_provider = embedding_provider
        self._client_id = client_id

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

        # check_same_thread=False: the watcher reindexes from a debounce timer
        # thread, distinct from whatever thread constructed this index. All
        # access is still serialized through self._lock.
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        try:
            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)
        except (AttributeError, sqlite3.OperationalError) as e:
            raise VaultIndexError(
                "SQLite extensions not supported. "
                "Use: uv run (reinstalls Python), or sudo apt-get install python3-dev && rebuild, "
                "or Docker with base python:3.11 official"
            ) from e

        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes_meta (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_id TEXT UNIQUE NOT NULL,
                link_key TEXT NOT NULL,
                title TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                tags TEXT NOT NULL
            )
            """
        )

        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_meta_link_key ON notes_meta(link_key)"
        )

        # target_key is a resolved wikilink name (basename, no extension, lowercase),
        # never a note_id — see markdown.link_key.
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS note_links (
                source TEXT NOT NULL,
                target_key TEXT NOT NULL
            )
            """
        )

        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_links_target ON note_links(target_key)"
        )

        self._conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts
            USING fts5(note_id UNINDEXED, title, content, tags)
            """
        )

        self._conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_vec
            USING vec0(embedding FLOAT[384])
            """
        )

        self._conn.commit()

    def reindex(self) -> int:
        with self._lock:
            if not self._vault_root.exists():
                return 0

            note_files = list(self._vault_root.rglob("*.md"))
            disk_note_ids: set[str] = set()

            for note_file in note_files:
                try:
                    note = parse_note(note_file, self._vault_root, self._client_id)
                    disk_note_ids.add(note.note_id)
                    self._index_note(note)
                except NoteParseError:
                    pass

            cursor = self._conn.execute("SELECT id, note_id FROM notes_meta")
            for row in cursor.fetchall():
                if row["note_id"] not in disk_note_ids:
                    self._remove_note(row["id"], row["note_id"])

            self._conn.commit()

            count = self._conn.execute("SELECT COUNT(*) FROM notes_meta").fetchone()[0]
            return int(count)

    def _index_note(self, note: VaultNote) -> None:
        cursor = self._conn.execute(
            "SELECT id, content_hash FROM notes_meta WHERE note_id = ?",
            (note.note_id,),
        )
        row = cursor.fetchone()

        if row and row["content_hash"] == note.content_hash:
            return

        if row:
            self._remove_note(row["id"], note.note_id)

        tags_json = json.dumps(list(note.tags))
        cursor = self._conn.execute(
            """
            INSERT INTO notes_meta (note_id, link_key, title, content_hash, updated_at, tags)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                note.note_id,
                link_key(note.note_id),
                note.title,
                note.content_hash,
                note.updated_at.isoformat(),
                tags_json,
            ),
        )
        note_id_pk = cursor.lastrowid

        self._conn.execute(
            """
            INSERT INTO notes_fts (rowid, note_id, title, content, tags)
            VALUES (?, ?, ?, ?, ?)
            """,
            (note_id_pk, note.note_id, note.title, note.content, tags_json),
        )

        embedding = self._embedding_provider.encode([note.content])[0]
        self._conn.execute(
            "INSERT INTO notes_vec (rowid, embedding) VALUES (?, ?)",
            (note_id_pk, sqlite_vec.serialize_float32(embedding)),
        )

        for link in note.links:
            self._conn.execute(
                "INSERT INTO note_links (source, target_key) VALUES (?, ?)",
                (note.note_id, link_key(link)),
            )

    def _remove_note(self, note_id_pk: int, note_id: str) -> None:
        self._conn.execute("DELETE FROM notes_meta WHERE id = ?", (note_id_pk,))
        self._conn.execute("DELETE FROM notes_fts WHERE rowid = ?", (note_id_pk,))
        self._conn.execute("DELETE FROM notes_vec WHERE rowid = ?", (note_id_pk,))
        self._conn.execute("DELETE FROM note_links WHERE source = ?", (note_id,))

    def search(self, query: str, limit: int = 10) -> tuple[VaultNote, ...]:
        with self._lock:
            self.reindex()

            rowids_with_scores = self._search_rowids(query, limit)
            result = []

            for rowid, _score in rowids_with_scores:
                cursor = self._conn.execute(
                    "SELECT note_id FROM notes_meta WHERE id = ?", (rowid,)
                )
                row = cursor.fetchone()
                if row:
                    note = self.get_note(row["note_id"])
                    if note:
                        result.append(note)

            return tuple(result)

    def _search_rowids(self, query: str, limit: int) -> list[tuple[int, float]]:
        query_embedding = self._embedding_provider.encode([query])[0]

        fts_results = []
        try:
            cursor = self._conn.execute(
                f"SELECT rowid FROM notes_fts WHERE notes_fts MATCH ? LIMIT {limit * 2}",
                (query,),
            )
            fts_results = [(row[0], float(i)) for i, row in enumerate(cursor.fetchall())]
        except Exception:
            pass

        vec_results = []
        try:
            vec_limit = limit * 2
            cursor = self._conn.execute(
                f"SELECT rowid FROM notes_vec WHERE embedding MATCH ? "
                f"ORDER BY distance LIMIT {vec_limit}",
                [sqlite_vec.serialize_float32(query_embedding)],
            )
            vec_results = [(row[0], float(i)) for i, row in enumerate(cursor.fetchall())]
        except Exception:
            pass

        scores: dict[int, float] = {}
        for rowid, rank in fts_results:
            scores[rowid] = scores.get(rowid, 0) + 1.0 / (RRF_CONSTANT + rank)
        for rowid, rank in vec_results:
            scores[rowid] = scores.get(rowid, 0) + 1.0 / (RRF_CONSTANT + rank)

        sorted_rowids = sorted(scores.keys(), key=lambda r: scores[r], reverse=True)[:limit]
        return [(rowid, scores[rowid]) for rowid in sorted_rowids]

    def get_note(self, note_id: str) -> VaultNote | None:
        note_path = self._vault_root / note_id
        if not note_path.exists():
            return None

        try:
            return parse_note(note_path, self._vault_root, self._client_id)
        except NoteParseError:
            return None

    def get_indexed_content_hash(self, note_id: str) -> str | None:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT content_hash FROM notes_meta WHERE note_id = ?", (note_id,)
            )
            row = cursor.fetchone()
            return row["content_hash"] if row else None

    def get_related(self, note_id: str) -> tuple[VaultNote, ...]:
        with self._lock:
            related_ids: set[str] = set()

            # Forward: this note's wikilinks are keys, resolved to whichever
            # note in the vault carries that basename.
            cursor = self._conn.execute(
                """
                SELECT m.note_id FROM note_links l
                JOIN notes_meta m ON m.link_key = l.target_key
                WHERE l.source = ?
                """,
                (note_id,),
            )
            for row in cursor.fetchall():
                related_ids.add(row[0])

            # Backward: notes whose wikilinks resolve to this note's own key.
            cursor = self._conn.execute(
                "SELECT source FROM note_links WHERE target_key = ?",
                (link_key(note_id),),
            )
            for row in cursor.fetchall():
                related_ids.add(row[0])

            related_ids.discard(note_id)

            result = []
            for related_note_id in sorted(related_ids):
                note = self.get_note(related_note_id)
                if note:
                    result.append(note)

            return tuple(result)

    def list_notes(self) -> tuple[VaultNote, ...]:
        with self._lock:
            cursor = self._conn.execute("SELECT note_id FROM notes_meta ORDER BY note_id")
            result = []
            for row in cursor.fetchall():
                note = self.get_note(row[0])
                if note:
                    result.append(note)
            return tuple(result)

    def close(self) -> None:
        self._conn.close()
