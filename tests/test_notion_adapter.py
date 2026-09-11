from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from audit.sink import FileSystemAuditSink
from mcp_engine.vault.index import VaultIndex
from mcp_engine.vault.notion_adapter import (
    NOTION_API_BASE,
    NotionConfigError,
    NotionNoteSource,
    NotionVaultProvider,
)

NOW = "2024-01-01T00:00:00.000Z"


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


def _child_page_block(block_id: str, title: str, updated: str = NOW) -> dict[str, Any]:
    return {
        "object": "block",
        "id": block_id,
        "type": "child_page",
        "child_page": {"title": title},
        "last_edited_time": updated,
        "has_children": True,
    }


def _rich_text_block(
    block_type: str, text: str, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    data: dict[str, Any] = {"rich_text": [{"plain_text": text}]}
    if extra:
        data.update(extra)
    return {"object": "block", "type": block_type, block_type: data}


def _page_response(page_id: str, title: str, updated: str = NOW) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": page_id,
            "last_edited_time": updated,
            "properties": {"title": {"type": "title", "title": [{"plain_text": title}]}},
        },
    )


def _children_response(
    results: list[dict[str, Any]], has_more: bool = False, next_cursor: str | None = None
) -> httpx.Response:
    return httpx.Response(
        200, json={"results": results, "has_more": has_more, "next_cursor": next_cursor}
    )


def static_tree_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    params = dict(request.url.params)

    if path == "/v1/blocks/root-id/children":
        cursor = params.get("start_cursor")
        if cursor is None:
            return _children_response(
                [_child_page_block("page-a", "Page A")], has_more=True, next_cursor="cursor1"
            )
        if cursor == "cursor1":
            return _children_response([_child_page_block("page-b", "Page B")])

    if path == "/v1/blocks/page-a/children":
        return _children_response(
            [
                _rich_text_block("paragraph", "Intro to A"),
                _child_page_block("page-a1", "Page A1"),
            ]
        )

    if path == "/v1/blocks/page-a1/children":
        return _children_response(
            [
                _rich_text_block("heading_1", "Sub heading"),
                _rich_text_block("bulleted_list_item", "item1"),
                _rich_text_block("to_do", "task done", {"checked": True}),
            ]
        )

    if path == "/v1/blocks/page-b/children":
        return _children_response(
            [
                _rich_text_block("quote", "Quote text"),
                _rich_text_block("code", "print(1)"),
                _rich_text_block("numbered_list_item", "step 1"),
                _rich_text_block("heading_2", "H2"),
                _rich_text_block("heading_3", "H3"),
            ]
        )

    titles = {"page-a": "Page A", "page-a1": "Page A1", "page-b": "Page B"}
    if path.startswith("/v1/pages/"):
        page_id = path.split("/")[3]
        if page_id in titles:
            return _page_response(page_id, titles[page_id])
        return httpx.Response(404, json={"object": "error", "status": 404})

    raise AssertionError(f"unexpected request: {request.method} {path}")


def _static_source() -> NotionNoteSource:
    transport = httpx.MockTransport(static_tree_handler)
    client = httpx.Client(base_url=NOTION_API_BASE, transport=transport)
    return NotionNoteSource(
        token="secret", root_page_id="root-id", client_id="example", client=client
    )


class TestNotionNoteSource:
    def test_list_notes_builds_notes_with_pagination_and_nesting(self) -> None:
        source = _static_source()

        notes = list(source.list_notes())
        ids = {n.note_id for n in notes}
        assert ids == {"page-a", "page-a/page-a1", "page-b"}

        page_a = next(n for n in notes if n.note_id == "page-a")
        assert page_a.title == "Page A"
        assert page_a.client_id == "example"
        assert "Intro to A" in page_a.content

        page_a1 = next(n for n in notes if n.note_id == "page-a/page-a1")
        assert "# Sub heading" in page_a1.content
        assert "- item1" in page_a1.content
        assert "[x] task done" in page_a1.content

        page_b = next(n for n in notes if n.note_id == "page-b")
        assert "> Quote text" in page_b.content
        assert "print(1)" in page_b.content
        assert "1. step 1" in page_b.content
        assert "## H2" in page_b.content
        assert "### H3" in page_b.content

    def test_get_note_fetches_single_page(self) -> None:
        source = _static_source()

        note = source.get_note("page-b")

        assert note is not None
        assert note.title == "Page B"
        assert "> Quote text" in note.content

    def test_get_note_returns_none_on_404(self) -> None:
        source = _static_source()

        assert source.get_note("missing") is None

    def test_reindex_via_vault_index_indexes_all_pages(self, tmp_path: Path) -> None:
        source = _static_source()
        index = VaultIndex(source, tmp_path / "index.sqlite3", FakeEmbeddingProvider(), "example")

        count = index.reindex()

        assert count == 3
        note_ids = {n.note_id for n in index.list_notes()}
        assert note_ids == {"page-a", "page-a/page-a1", "page-b"}


def _to_read_shape(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    read_blocks = []
    for block in blocks:
        block_type = block["type"]
        data = dict(block[block_type])
        rich_text = data.get("rich_text", [])
        data["rich_text"] = [
            {"plain_text": rt["text"]["content"], **rt} for rt in rich_text
        ]
        read_blocks.append({"object": "block", "type": block_type, block_type: data})
    return read_blocks


class StatefulNotionAPI:
    def __init__(self) -> None:
        self.pages: dict[str, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []
        self._counter = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path

        if request.method == "POST" and path == "/v1/pages":
            body = json.loads(request.content)
            self._counter += 1
            page_id = f"created-{self._counter}"
            title = body["properties"]["title"]["title"][0]["text"]["content"]
            self.pages[page_id] = {
                "title": title,
                "blocks": _to_read_shape(body.get("children", [])),
                "last_edited_time": NOW,
            }
            return _page_response(page_id, title)

        if path == "/v1/blocks/root-id/children":
            results = [
                _child_page_block(pid, data["title"], data["last_edited_time"])
                for pid, data in self.pages.items()
            ]
            return _children_response(results)

        if path.startswith("/v1/blocks/") and path.endswith("/children"):
            page_id = path.split("/")[3]
            data = self.pages.get(page_id)
            if data is None:
                return _children_response([])
            return _children_response(data["blocks"])

        if path.startswith("/v1/pages/"):
            page_id = path.split("/")[3]
            data = self.pages.get(page_id)
            if data is None:
                return httpx.Response(404, json={"object": "error", "status": 404})
            return _page_response(page_id, data["title"], data["last_edited_time"])

        raise AssertionError(f"unexpected request: {request.method} {path}")


def _write_notion_client_config(config_root: Path, client_id: str = "example") -> None:
    client_config_dir = config_root / client_id / "config"
    client_config_dir.mkdir(parents=True)
    client_config_dir.joinpath("client_config.yaml").write_text(
        f"client_id: {client_id}\n"
        "block_private_ip: true\n"
        "domain_allowlist:\n  - api.example.com\n"
        "vault_provider: notion\n"
        "notion_token: secret-token\n"
        "notion_root_page_id: root-id\n"
    )


class TestNotionVaultProvider:
    def test_add_note_posts_payload_and_reindexes(self, tmp_path: Path) -> None:
        config_root = tmp_path / "config"
        data_root = tmp_path / "data"
        _write_notion_client_config(config_root)

        api = StatefulNotionAPI()
        provider = NotionVaultProvider(
            config_root, data_root, FileSystemAuditSink(data_root), FakeEmbeddingProvider()
        )
        transport = httpx.MockTransport(api.handler)
        provider._sources["example"] = NotionNoteSource(
            token="secret-token",
            root_page_id="root-id",
            client_id="example",
            client=httpx.Client(base_url=NOTION_API_BASE, transport=transport),
        )

        note = provider.add_note(
            "example", "Minuta de Petição", "# Fundamentos\n\nCorpo do texto\n- item um"
        )

        assert note.title == "Minuta de Petição"
        assert "# Fundamentos" in note.content
        assert "Corpo do texto" in note.content
        assert "- item um" in note.content

        post_requests = [
            r for r in api.requests if r.method == "POST" and r.url.path == "/v1/pages"
        ]
        assert len(post_requests) == 1
        body = json.loads(post_requests[0].content)
        assert body["parent"] == {"type": "page_id", "page_id": "root-id"}
        assert body["properties"]["title"]["title"][0]["text"]["content"] == "Minuta de Petição"

        listed = provider.list_notes("example")
        assert any(n.note_id == note.note_id for n in listed)

        events = list(provider._audit_sink.iter_events("example"))
        assert any(e.action == "vault_add_note" for e in events)

    def test_search_and_reindex_delegate_to_vault_index(self, tmp_path: Path) -> None:
        config_root = tmp_path / "config"
        data_root = tmp_path / "data"
        _write_notion_client_config(config_root)

        provider = NotionVaultProvider(
            config_root, data_root, FileSystemAuditSink(data_root), FakeEmbeddingProvider()
        )
        provider._sources["example"] = _static_source()

        count = provider.reindex("example")
        assert count == 3

        results = provider.search("example", "quote", limit=10)
        assert any(n.note_id == "page-b" for n in results)

        related = provider.get_related("example", "page-a")
        assert related == ()

    def test_missing_notion_token_raises_clear_error(self, tmp_path: Path) -> None:
        config_root = tmp_path / "config"
        data_root = tmp_path / "data"
        client_config_dir = config_root / "example" / "config"
        client_config_dir.mkdir(parents=True)
        client_config_dir.joinpath("client_config.yaml").write_text(
            "client_id: example\n"
            "block_private_ip: true\n"
            "domain_allowlist:\n  - api.example.com\n"
            "vault_provider: notion\n"
            "notion_root_page_id: root-id\n"
        )

        provider = NotionVaultProvider(
            config_root, data_root, FileSystemAuditSink(data_root), FakeEmbeddingProvider()
        )

        with pytest.raises(NotionConfigError):
            provider.search("example", "anything")

    def test_missing_notion_root_page_id_raises_clear_error(self, tmp_path: Path) -> None:
        config_root = tmp_path / "config"
        data_root = tmp_path / "data"
        client_config_dir = config_root / "example" / "config"
        client_config_dir.mkdir(parents=True)
        client_config_dir.joinpath("client_config.yaml").write_text(
            "client_id: example\n"
            "block_private_ip: true\n"
            "domain_allowlist:\n  - api.example.com\n"
            "vault_provider: notion\n"
            "notion_token: secret-token\n"
        )

        provider = NotionVaultProvider(
            config_root, data_root, FileSystemAuditSink(data_root), FakeEmbeddingProvider()
        )

        with pytest.raises(NotionConfigError):
            provider.search("example", "anything")
