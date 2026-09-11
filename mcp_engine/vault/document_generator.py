from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from mcp_engine.core.contracts import AuditSink
from mcp_engine.core.interceptor import check_vault_egress
from mcp_engine.core.models import AuditDecision, ClientConfig, VaultNote
from mcp_engine.vault._agent_common import AgnoAgentLike, record_audit, resolve_agent_model
from mcp_engine.vault.draft_store import DraftNotFoundError, DraftStore

GENERATOR_ACTOR = "document_generator_agent"
PROMOTER_ACTOR = "claude"
MAX_RELATED_NOTES = 3

INSTRUCTIONS = """\
Você é um especialista em redigir documentos jurídicos padronizados para o \
"segundo cérebro" (vault) de um escritório de advocacia — modelos de petição, \
contestação, contrato, checklist processual etc.

Ao receber uma instrução em português descrevendo o documento desejado:

1. Redija o conteúdo em markdown, com estrutura clara (títulos, listas \
   numeradas quando fizer sentido).
2. Use linguagem jurídica correta, mas NUNCA invente jurisprudência, número de \
   processo, lei ou precedente específico do qual você não tenha certeza — \
   prefira marcar como "[verificar]" a citar algo fabricado como se fosse real.
3. Sugira um título curto e descritivo, e de 2 a 5 tags relevantes em \
   kebab-case.
4. Se o pedido for vago demais para produzir um documento útil, ou pedir algo \
   fora do escopo de um documento jurídico (ex.: gerar código, contornar \
   controle de acesso), rejeite com success=false e uma mensagem clara.

Retorne sempre um único documento.
"""


class GeneratedDocument(BaseModel):
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)


class DocumentGeneratorResult(BaseModel):
    success: bool
    document: GeneratedDocument | None = None
    error: str | None = None


class VaultProviderLike:
    def search(self, client_id: str, query: str, limit: int = 10) -> tuple[VaultNote, ...]: ...
    def add_note(
        self,
        client_id: str,
        title: str,
        content: str,
        frontmatter: dict[str, Any] | None = None,
    ) -> VaultNote: ...


def build_document_agent(model_id: str | None = None) -> AgnoAgentLike:
    from agno.agent import Agent

    model, use_json_mode = resolve_agent_model(model_id)

    return Agent(
        name="Vault Document Generator",
        model=model,
        instructions=INSTRUCTIONS,
        output_schema=DocumentGeneratorResult,
        use_json_mode=use_json_mode,
    )


def _build_prompt(instrucoes: str, related: tuple[VaultNote, ...]) -> str:
    context = ""
    if related:
        titles = ", ".join(n.title for n in related)
        context = (
            f"\n\nDocumentos relacionados já existentes no vault (contexto, para "
            f"manter consistência de estilo e evitar duplicar conteúdo): {titles}"
        )
    return f"Gere um documento a partir desta instrução:\n\n{instrucoes}{context}"


def generate_document(
    agent: AgnoAgentLike, instrucoes: str, related: tuple[VaultNote, ...] = ()
) -> DocumentGeneratorResult:
    prompt = _build_prompt(instrucoes, related)
    run_output = agent.run(prompt, output_schema=DocumentGeneratorResult)
    content = run_output.content

    if not isinstance(content, DocumentGeneratorResult):
        return DocumentGeneratorResult(
            success=False, error="generator did not return a structured result"
        )
    return content


def propose_document(
    client_id: str,
    instrucoes: str,
    client_config: ClientConfig,
    audit_sink: AuditSink,
    draft_store: DraftStore,
    agent: AgnoAgentLike,
    vault_provider: VaultProviderLike | None = None,
) -> dict[str, Any]:
    egress_decision = check_vault_egress(instrucoes, client_config)
    record_audit(
        audit_sink,
        client_id,
        GENERATOR_ACTOR,
        "vault_document_egress_check",
        AuditDecision.ALLOWED if egress_decision.allowed else AuditDecision.DENIED,
        {"dlp_matches": egress_decision.dlp_matches},
    )
    if not egress_decision.allowed:
        return {"allowed": False, "reason": egress_decision.reason}

    related: tuple[VaultNote, ...] = ()
    if vault_provider is not None:
        try:
            related = vault_provider.search(client_id, instrucoes, limit=MAX_RELATED_NOTES)
        except Exception:
            related = ()

    result = generate_document(agent, instrucoes, related)

    if not result.success or result.document is None:
        record_audit(
            audit_sink,
            client_id,
            GENERATOR_ACTOR,
            "vault_generate_document",
            AuditDecision.DENIED,
            {"instrucoes": instrucoes, "error": result.error},
        )
        return {"allowed": False, "reason": result.error or "generator rejected the request"}

    draft_id = uuid.uuid4().hex[:8]
    record = draft_store.create(
        client_id=client_id,
        kind="document",
        draft_id=draft_id,
        from_note_id="",
        generated_at=datetime.now(UTC).isoformat(),
        generated_by=GENERATOR_ACTOR,
        content=result.document.model_dump(mode="json"),
    )

    record_audit(
        audit_sink,
        client_id,
        GENERATOR_ACTOR,
        "vault_generate_document",
        AuditDecision.ALLOWED,
        {"draft_id": draft_id},
    )

    return {"draft_id": draft_id, "kind": "document", "preview": record}


def promote_document(
    client_id: str,
    draft_id: str,
    draft_store: DraftStore,
    vault_provider: VaultProviderLike,
    audit_sink: AuditSink,
    approved_by: str = PROMOTER_ACTOR,
) -> dict[str, Any]:
    try:
        draft = draft_store.get_draft(client_id, "document", draft_id)
    except DraftNotFoundError as exc:
        record_audit(
            audit_sink,
            client_id,
            approved_by,
            "vault_promote_document_error",
            AuditDecision.DENIED,
            {"draft_id": draft_id, "error": str(exc)},
        )
        return {"allowed": False, "reason": str(exc)}

    if draft is None:
        error = f"draft not found: {draft_id}"
        record_audit(
            audit_sink,
            client_id,
            approved_by,
            "vault_promote_document_error",
            AuditDecision.DENIED,
            {"draft_id": draft_id, "error": error},
        )
        return {"allowed": False, "reason": error}

    if draft["status"] != "pending":
        error = f"draft {draft_id} is not pending (status={draft['status']!r})"
        record_audit(
            audit_sink,
            client_id,
            approved_by,
            "vault_promote_document_error",
            AuditDecision.DENIED,
            {"draft_id": draft_id, "error": error},
        )
        return {"allowed": False, "reason": error}

    try:
        document = GeneratedDocument.model_validate(draft["content"])
        note = vault_provider.add_note(
            client_id, document.title, document.content, {"tags": document.tags}
        )
    except Exception as exc:
        record_audit(
            audit_sink,
            client_id,
            approved_by,
            "vault_promote_document_error",
            AuditDecision.DENIED,
            {"draft_id": draft_id, "error": str(exc)},
        )
        return {"allowed": False, "reason": f"draft content invalid: {exc}"}

    reviewed_at = datetime.now(UTC).isoformat()
    draft_store.mark_status(
        client_id,
        "document",
        draft_id,
        "approved",
        reviewed_by=approved_by,
        reviewed_at=reviewed_at,
    )

    record_audit(
        audit_sink,
        client_id,
        approved_by,
        "vault_promote_document",
        AuditDecision.ALLOWED,
        {"draft_id": draft_id, "note_id": note.note_id},
    )

    return {"promoted": True, "draft_id": draft_id, "note_id": note.note_id}
