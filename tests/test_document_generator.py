from pathlib import Path
from types import SimpleNamespace

import pytest

from audit.sink import FileSystemAuditSink
from mcp_engine.core.models import ClientConfig, DLPPattern
from mcp_engine.vault.document_generator import (
    DocumentGeneratorResult,
    GeneratedDocument,
    promote_document,
    propose_document,
)
from mcp_engine.vault.draft_store import DraftStore
from mcp_engine.vault.obsidian_adapter import ObsidianVaultProvider

CPF_DENY = DLPPattern(pattern=r"\d{3}\.\d{3}\.\d{3}-\d{2}", label="CPF", action="deny")

SAMPLE_DOCUMENT = GeneratedDocument(
    title="Notificação extrajudicial de cobrança",
    content="Prezado(a) [cliente],\n\nNotificamos...",
    tags=["notificacao", "cobranca", "modelo"],
)


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


class FakeDocumentAgent:
    def __init__(self, result: DocumentGeneratorResult) -> None:
        self.result = result
        self.prompts: list[str] = []

    def run(self, input: str, *, output_schema=None):
        self.prompts.append(input)
        return SimpleNamespace(content=self.result)


class Setup(SimpleNamespace):
    pass


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    config_root = tmp_path / "config"
    data_root = tmp_path / "data"

    client_config_dir = config_root / "example" / "config"
    client_config_dir.mkdir(parents=True)

    audit_sink = FileSystemAuditSink(data_root)
    vault_provider = ObsidianVaultProvider(
        config_root, data_root, audit_sink, FakeEmbeddingProvider()
    )
    (config_root / "example" / "vault").mkdir(parents=True)

    return Setup(
        config_root=config_root,
        data_root=data_root,
        audit_sink=audit_sink,
        vault_provider=vault_provider,
        draft_store=DraftStore(config_root),
        client_config=ClientConfig(client_id="example", domain_allowlist=("api.example.com",)),
    )


class TestProposeDocument:
    def test_generates_draft_without_writing_to_vault(self, setup: Setup) -> None:
        agent = FakeDocumentAgent(
            DocumentGeneratorResult(success=True, document=SAMPLE_DOCUMENT)
        )

        result = propose_document(
            "example",
            "gere uma notificação extrajudicial de cobrança",
            setup.client_config,
            setup.audit_sink,
            setup.draft_store,
            agent,
            setup.vault_provider,
        )

        assert "draft_id" in result
        assert result["kind"] == "document"
        assert len(agent.prompts) == 1

        drafts = setup.draft_store.list_drafts("example", "document")
        assert len(drafts) == 1
        assert drafts[0]["status"] == "pending"
        assert drafts[0]["content"]["title"] == SAMPLE_DOCUMENT.title

        notes = setup.vault_provider.search("example", "cobrança")
        assert notes == ()

    def test_blocks_on_dlp_before_calling_llm(self, setup: Setup) -> None:
        config = ClientConfig(
            client_id="example", domain_allowlist=("api.example.com",), dlp_patterns=(CPF_DENY,)
        )
        agent = FakeDocumentAgent(
            DocumentGeneratorResult(success=True, document=SAMPLE_DOCUMENT)
        )

        result = propose_document(
            "example",
            "gere uma notificação citando o CPF 123.456.789-00 do devedor",
            config,
            setup.audit_sink,
            setup.draft_store,
            agent,
            setup.vault_provider,
        )

        assert result["allowed"] is False
        assert "DLP" in result["reason"]
        assert agent.prompts == []
        assert setup.draft_store.list_drafts("example", "document") == []

    def test_generator_rejection_creates_no_draft(self, setup: Setup) -> None:
        agent = FakeDocumentAgent(
            DocumentGeneratorResult(success=False, error="pedido vago demais")
        )

        result = propose_document(
            "example",
            "faz um documento aí",
            setup.client_config,
            setup.audit_sink,
            setup.draft_store,
            agent,
            setup.vault_provider,
        )

        assert result["allowed"] is False
        assert "vago" in result["reason"]
        assert setup.draft_store.list_drafts("example", "document") == []


class TestPromoteDocument:
    def test_promotes_pending_draft_to_real_vault_note(self, setup: Setup) -> None:
        agent = FakeDocumentAgent(
            DocumentGeneratorResult(success=True, document=SAMPLE_DOCUMENT)
        )
        proposed = propose_document(
            "example",
            "gere uma notificação extrajudicial de cobrança",
            setup.client_config,
            setup.audit_sink,
            setup.draft_store,
            agent,
            setup.vault_provider,
        )
        draft_id = proposed["draft_id"]

        result = promote_document(
            "example", draft_id, setup.draft_store, setup.vault_provider, setup.audit_sink
        )

        assert result["promoted"] is True
        assert result["note_id"]

        note = setup.vault_provider.get_note("example", result["note_id"])
        assert note is not None
        assert note.title == SAMPLE_DOCUMENT.title

        drafts = setup.draft_store.list_drafts("example", "document")
        assert drafts[0]["status"] == "approved"

    def test_rejects_unknown_draft_id(self, setup: Setup) -> None:
        result = promote_document(
            "example", "does-not-exist", setup.draft_store, setup.vault_provider, setup.audit_sink
        )
        assert result["allowed"] is False

    def test_rejects_already_approved_draft(self, setup: Setup) -> None:
        agent = FakeDocumentAgent(
            DocumentGeneratorResult(success=True, document=SAMPLE_DOCUMENT)
        )
        proposed = propose_document(
            "example",
            "gere uma notificação extrajudicial de cobrança",
            setup.client_config,
            setup.audit_sink,
            setup.draft_store,
            agent,
            setup.vault_provider,
        )
        draft_id = proposed["draft_id"]
        promote_document(
            "example", draft_id, setup.draft_store, setup.vault_provider, setup.audit_sink
        )

        result = promote_document(
            "example", draft_id, setup.draft_store, setup.vault_provider, setup.audit_sink
        )

        assert result["allowed"] is False
        assert "not pending" in result["reason"]
