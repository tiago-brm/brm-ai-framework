from pathlib import Path

import pytest

from audit.sink import FileSystemAuditSink
from mcp_engine.vault.index import VaultIndex
from mcp_engine.vault.markdown import NoteParseError, parse_note, serialize_note
from mcp_engine.vault.obsidian_adapter import ObsidianVaultProvider, VaultRootNotFoundError


class FakeEmbeddingProvider:
    @property
    def dim(self) -> int:
        return 384

    def encode(self, texts: list[str]) -> list[list[float]]:
        result = []
        for text in texts:
            h = hash(text) & 0xFFFFFFFF
            vec = [(float((h >> (i % 16)) & 1) - 0.5) for i in range(384)]
            result.append(vec)
        return result


class TestMarkdownParsing:
    def test_parse_note_with_frontmatter_and_wikilinks(self, tmp_path: Path) -> None:
        vault_root = tmp_path / "vault"
        vault_root.mkdir()

        note_file = vault_root / "test.md"
        note_file.write_text(
            """\
---
title: Test Note
tags:
  - tag1
  - tag2
---

Content here with [[another-note]] and [[third]].

Some #inline-tag in the content.
"""
        )

        note = parse_note(note_file, vault_root, "example")

        assert note.note_id == "test.md"
        assert note.client_id == "example"
        assert note.title == "Test Note"
        assert "Content here" in note.content
        assert "tag1" in note.tags
        assert "tag2" in note.tags
        assert "inline-tag" in note.tags
        assert "another-note" in note.links
        assert "third" in note.links

    def test_parse_note_fallback_title_to_filename(self, tmp_path: Path) -> None:
        vault_root = tmp_path
        note_file = vault_root / "myfile.md"
        note_file.write_text("---\ntags: []\n---\nContent")

        note = parse_note(note_file, vault_root, "example")
        assert note.title == "myfile"

    def test_parse_note_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(NoteParseError):
            parse_note(tmp_path / "missing.md", tmp_path, "example")

    def test_serialize_note(self) -> None:
        title = "My Note"
        content = "Body text"
        frontmatter = {"tags": ["a", "b"], "custom": "value"}

        serialized = serialize_note(title, content, frontmatter)

        assert "---" in serialized
        assert "title: My Note" in serialized
        assert "- a" in serialized or '"a"' in serialized
        assert "Body text" in serialized


class TestVaultIndex:
    @pytest.fixture
    def index(self, tmp_path: Path) -> VaultIndex:
        vault_root = tmp_path / "vault"
        vault_root.mkdir()
        db_path = tmp_path / "index.sqlite3"
        embedding_provider = FakeEmbeddingProvider()
        return VaultIndex(vault_root, db_path, embedding_provider, "example")

    def test_reindex_scans_vault(self, index: VaultIndex, tmp_path: Path) -> None:
        vault_root = tmp_path / "vault"

        (vault_root / "note1.md").write_text("---\ntitle: Note 1\ntags: []\n---\nContent 1")
        (vault_root / "note2.md").write_text("---\ntitle: Note 2\ntags: []\n---\nContent 2")

        count = index.reindex()
        assert count == 2

        notes = index.list_notes()
        assert len(notes) == 2
        assert any(n.title == "Note 1" for n in notes)
        assert any(n.title == "Note 2" for n in notes)

    def test_search_exact_term(self, index: VaultIndex, tmp_path: Path) -> None:
        vault_root = tmp_path / "vault"
        (vault_root / "prescrition.md").write_text(
            "---\ntitle: Prescrition Law\ntags: []\n---\nThe law of prescrition is 3 years."
        )
        (vault_root / "other.md").write_text(
            "---\ntitle: Other Note\ntags: []\n---\nUnrelated content about contracts."
        )

        results = index.search("prescrition", limit=10)
        assert len(results) > 0
        assert results[0].title == "Prescrition Law"

    def test_search_paraphrase_vector(self, index: VaultIndex, tmp_path: Path) -> None:
        vault_root = tmp_path / "vault"
        (vault_root / "case.md").write_text(
            "---\ntitle: Similar Case\ntags: []\n---\n"
            "This case is about similar situations and circumstances."
        )

        results = index.search("like cases with similar scenarios", limit=10)
        assert len(results) > 0

    def test_get_related_via_backlinks(self, index: VaultIndex, tmp_path: Path) -> None:
        vault_root = tmp_path / "vault"
        (vault_root / "note1.md").write_text(
            "---\ntitle: Note 1\ntags: []\n---\nSee [[note2]]."
        )
        (vault_root / "note2.md").write_text(
            "---\ntitle: Note 2\ntags: []\n---\nReference [[note1]]."
        )

        index.reindex()

        related = index.get_related("note1.md")
        assert len(related) > 0
        assert any(n.note_id == "note2.md" for n in related)

        backlinks = index.get_related("note2.md")
        assert any(n.note_id == "note1.md" for n in backlinks)

    def test_reindex_incremental_skips_unchanged(self, index: VaultIndex, tmp_path: Path) -> None:
        vault_root = tmp_path / "vault"
        note_file = vault_root / "note.md"
        note_file.write_text("---\ntitle: Note\ntags: []\n---\nContent")

        count1 = index.reindex()
        assert count1 == 1

        hash1 = index.get_indexed_content_hash("note.md")

        count2 = index.reindex()
        assert count2 == 1

        hash2 = index.get_indexed_content_hash("note.md")
        assert hash1 == hash2

        note_file.write_text("---\ntitle: Note\ntags: []\n---\nNew Content")
        count3 = index.reindex()
        assert count3 == 1

        hash3 = index.get_indexed_content_hash("note.md")
        assert hash1 != hash3


class TestObsidianVaultProvider:
    @pytest.fixture
    def provider(self, tmp_path: Path) -> ObsidianVaultProvider:
        config_root = tmp_path / "config"
        data_root = tmp_path / "data"
        audit_sink = FileSystemAuditSink(data_root)

        client_config_dir = config_root / "example" / "config"
        client_config_dir.mkdir(parents=True)
        client_config_dir.joinpath("client_config.yaml").write_text(
            "client_id: example\nblock_private_ip: true\ndomain_allowlist:\n  - api.example.com\n"
        )

        return ObsidianVaultProvider(config_root, data_root, audit_sink, FakeEmbeddingProvider())

    def test_add_note_creates_file(self, provider: ObsidianVaultProvider, tmp_path: Path) -> None:
        note = provider.add_note("example", "Test Note", "Body content", {"tags": ["a"]})

        assert note.title == "Test Note"
        assert "Body content" in note.content
        assert "a" in note.tags

        vault_root = tmp_path / "config" / "example" / "vault"
        note_file = vault_root / "test-note.md"
        assert note_file.exists()

    def test_add_note_conflict_detection(
        self, provider: ObsidianVaultProvider, tmp_path: Path
    ) -> None:
        vault_root = tmp_path / "config" / "example" / "vault"
        vault_root.mkdir(parents=True)

        note_file = vault_root / "existing.md"
        note_file.write_text("---\ntitle: Existing\ntags: []\n---\nClient content")

        note1 = provider.add_note("example", "Existing", "Engine content", None)
        assert note1.content == "Client content"

        conflicts = list(vault_root.glob("*.md-brm-conflict-*"))
        assert len(conflicts) == 1
        assert "Engine content" in conflicts[0].read_text()

        events = list(provider._audit_sink.iter_events("example"))
        assert any(e.action == "vault_write_conflict" for e in events)

    def test_unparseable_note_is_salvaged_not_overwritten(
        self, provider: ObsidianVaultProvider, tmp_path: Path
    ) -> None:
        """A note the client broke in Obsidian must never be silently discarded.

        parse_note() fails on invalid YAML, so the hash comparison that drives
        normal conflict detection cannot run. The original bytes go to a salvage
        sidecar before the engine takes the name.
        """
        vault_root = tmp_path / "config" / "example" / "vault"
        vault_root.mkdir(parents=True)

        note_file = vault_root / "quebrada.md"
        original = "---\ntitle: [unclosed\n  tabs: :: invalid\n---\n\nIRREPLACEABLE CLIENT TEXT\n"
        note_file.write_text(original)

        provider.add_note("example", "Quebrada", "Engine content", None)

        salvaged = list(vault_root.glob("*.md-brm-unparseable-*"))
        assert len(salvaged) == 1
        assert salvaged[0].read_text() == original

        events = list(provider._audit_sink.iter_events("example"))
        conflict_events = [e for e in events if e.action == "vault_write_conflict"]
        assert len(conflict_events) == 1
        assert conflict_events[0].details["reason"] == "existing note could not be parsed"

    def test_sidecar_files_are_not_indexed_as_notes(
        self, provider: ObsidianVaultProvider, tmp_path: Path
    ) -> None:
        """Sidecars must not come back from search/list as if they were notes.

        They carry the same title as the note they shadow, so indexing them
        would put a duplicate beside every real hit.
        """
        vault_root = tmp_path / "config" / "example" / "vault"
        vault_root.mkdir(parents=True)

        (vault_root / "dup.md").write_text("---\ntitle: Dup\ntags: []\n---\nClient version")
        provider.add_note("example", "Dup", "Engine version", None)

        assert list(vault_root.glob("*.md-brm-conflict-*"))

        provider.reindex("example")
        note_ids = [n.note_id for n in provider.list_notes("example")]
        assert note_ids == ["dup.md"]

        found = [n.note_id for n in provider.search("example", "version", limit=10)]
        assert found == ["dup.md"]

    def test_search_delegates_to_index(
        self, provider: ObsidianVaultProvider, tmp_path: Path
    ) -> None:
        vault_root = tmp_path / "config" / "example" / "vault"
        vault_root.mkdir(parents=True)

        (vault_root / "note.md").write_text(
            "---\ntitle: Test\ntags: []\n---\nSearchable content here"
        )

        results = provider.search("example", "searchable", limit=10)
        assert len(results) > 0
        assert results[0].title == "Test"

    def test_get_related_returns_linked_notes(
        self, provider: ObsidianVaultProvider, tmp_path: Path
    ) -> None:
        vault_root = tmp_path / "config" / "example" / "vault"
        vault_root.mkdir(parents=True)

        (vault_root / "a.md").write_text("---\ntitle: A\ntags: []\n---\nSee [[b]]")
        (vault_root / "b.md").write_text("---\ntitle: B\ntags: []\n---\nSee [[a]]")

        provider.reindex("example")

        related = provider.get_related("example", "a.md")
        assert len(related) > 0
        assert any(n.note_id == "b.md" for n in related)

    def test_explicit_vault_root_must_exist(self, tmp_path: Path) -> None:
        config_root = tmp_path / "config"
        data_root = tmp_path / "data"
        client_config_dir = config_root / "ghost" / "config"
        client_config_dir.mkdir(parents=True)
        client_config_dir.joinpath("client_config.yaml").write_text(
            "client_id: ghost\n"
            "block_private_ip: true\n"
            "domain_allowlist:\n  - api.example.com\n"
            f"vault_root: {tmp_path / 'nao-existe'}\n"
        )

        provider = ObsidianVaultProvider(
            config_root, data_root, FileSystemAuditSink(data_root), FakeEmbeddingProvider()
        )

        with pytest.raises(VaultRootNotFoundError):
            provider.search("ghost", "anything")

    def test_convention_vault_root_is_created(self, tmp_path: Path) -> None:
        config_root = tmp_path / "config"
        data_root = tmp_path / "data"
        client_config_dir = config_root / "novo" / "config"
        client_config_dir.mkdir(parents=True)
        client_config_dir.joinpath("client_config.yaml").write_text(
            "client_id: novo\nblock_private_ip: true\ndomain_allowlist:\n  - api.example.com\n"
        )

        provider = ObsidianVaultProvider(
            config_root, data_root, FileSystemAuditSink(data_root), FakeEmbeddingProvider()
        )
        provider.reindex("novo")

        assert (config_root / "novo" / "vault").is_dir()


class TestEmbeddingsSmoke:
    def test_fake_embedding_provider(self) -> None:
        provider = FakeEmbeddingProvider()
        assert provider.dim == 384

        vecs = provider.encode(["hello", "world"])
        assert len(vecs) == 2
        assert len(vecs[0]) == 384
        assert all(isinstance(v, float) for v in vecs[0])


class TestExampleVaultFixture:
    """Exercises the real vault the 'example' client points at via vault_root.

    Skips when that vault is absent, since the path is machine-specific.
    """

    def test_example_vault_is_indexable(self, tmp_path: Path) -> None:
        config_root = Path(__file__).parent.parent / "clients"
        provider = ObsidianVaultProvider(
            config_root, tmp_path, FileSystemAuditSink(tmp_path), FakeEmbeddingProvider()
        )

        try:
            count = provider.reindex("example")
        except VaultRootNotFoundError as exc:
            pytest.skip(f"configured vault not present on this machine: {exc.vault_root}")

        assert count > 0

        notes = provider.list_notes("example")
        assert any("prescricao" in n.note_id.lower() for n in notes)
        assert any("peticao" in n.note_id.lower() for n in notes)

        # Cross-folder wikilink: teses/prescricao.md links [[peticao-inicial]],
        # which lives under modelos/.
        related = provider.get_related("example", "teses/prescricao.md")
        assert any("peticao" in n.note_id.lower() for n in related)
