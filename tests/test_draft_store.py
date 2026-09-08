from pathlib import Path

from mcp_engine.vault.draft_store import DraftStore


class TestDraftStore:
    def test_create_and_get_draft(self, tmp_path: Path) -> None:
        store = DraftStore(tmp_path)

        record = store.create(
            client_id="example",
            kind="rule",
            draft_id="abc123",
            from_note_id="config_drafts/nova_regra.md",
            generated_at="2026-08-28T10:00:00+00:00",
            generated_by="interpreter_agent",
            content={"id": "x", "condition": {}},
        )

        assert record["status"] == "pending"

        fetched = store.get_draft("example", "rule", "abc123")
        assert fetched == record

    def test_get_draft_missing_returns_none(self, tmp_path: Path) -> None:
        store = DraftStore(tmp_path)
        assert store.get_draft("example", "rule", "missing") is None

    def test_list_drafts_filters_by_status(self, tmp_path: Path) -> None:
        store = DraftStore(tmp_path)
        store.create(
            "example", "rule", "d1", "note1.md", "t1", "interpreter_agent", {"id": "r1"}
        )
        store.create(
            "example", "rule", "d2", "note2.md", "t2", "interpreter_agent", {"id": "r2"}
        )
        store.mark_status("example", "rule", "d1", "approved", reviewed_by="claude")

        pending = store.list_drafts("example", "rule", status="pending")
        approved = store.list_drafts("example", "rule", status="approved")

        assert [d["draft_id"] for d in pending] == ["d2"]
        assert [d["draft_id"] for d in approved] == ["d1"]

    def test_mark_status_preserves_content_and_updates_review_fields(
        self, tmp_path: Path
    ) -> None:
        store = DraftStore(tmp_path)
        store.create(
            "example", "skill", "s1", "note.md", "t1", "interpreter_agent", {"name": "foo"}
        )

        updated = store.mark_status(
            "example", "skill", "s1", "approved", reviewed_by="claude", reviewed_at="t2"
        )

        assert updated["content"] == {"name": "foo"}
        assert updated["status"] == "approved"
        assert updated["reviewed_by"] == "claude"

        current = store.get_draft("example", "skill", "s1")
        assert current == updated

    def test_rules_and_skills_are_separate_files(self, tmp_path: Path) -> None:
        store = DraftStore(tmp_path)
        store.create("example", "rule", "r1", "n.md", "t", "a", {})
        store.create("example", "skill", "sk1", "n.md", "t", "a", {})

        assert store.list_drafts("example", "rule") != []
        assert [d["draft_id"] for d in store.list_drafts("example", "rule")] == ["r1"]
        assert [d["draft_id"] for d in store.list_drafts("example", "skill")] == ["sk1"]

        config_dir = tmp_path / "example" / "config"
        assert (config_dir / "rules.draft.jsonl").exists()
        assert (config_dir / "skills.draft.jsonl").exists()
