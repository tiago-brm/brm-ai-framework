from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import yaml
from pydantic import BaseModel, Field

from mcp_engine.core.contracts import AuditSink
from mcp_engine.core.documentos import ModeloInvalido, parse_modelo
from mcp_engine.core.interceptor import check_vault_egress
from mcp_engine.core.models import AuditDecision, ClientConfig, VaultNote
from mcp_engine.vault._agent_common import AgnoAgentLike, record_audit, resolve_agent_model
from mcp_engine.vault.draft_store import DraftNotFoundError, DraftStore

GENERATOR_ACTOR = "document_generator_agent"
PROMOTER_ACTOR = "claude"
MAX_RELATED_NOTES = 3

INSTRUCTIONS = """\
Você redige modelos de documento padronizados para o "segundo cérebro" (vault) de uma \
organização: contratos, termos, declarações, checklists, comunicados e afins, conforme o \
segmento descrito abaixo.

Ao receber uma instrução em português descrevendo o documento desejado:

1. Redija o conteúdo em markdown, com estrutura clara (títulos com #, listas com -).
2. Nunca preencha dados reais de pessoas. Todo dado variável vira um campo no texto, no \
   formato {{ nome_do_campo }}, e é declarado em `campos` com: nome (snake_case), tipo \
   (texto, data, dinheiro, inteiro, cpf ou booleano), origem (grupo.campo, por exemplo \
   pessoa.nome, emissor.razao_social, atendimento.data), obrigatorio (true/false) e rotulo. \
   Trechos opcionais usam {% if nome_do_campo %}...{% endif %}. O texto não pode usar campo \
   que não esteja declarado.
3. Use a linguagem técnica do segmento, mas NUNCA invente lei, norma, precedente, número de \
   registro ou referência da qual você não tenha certeza; marque como "[verificar]".
4. Sugira um título curto e descritivo, um identificador em kebab-case para o modelo e de 2 \
   a 5 tags em kebab-case.
5. Se o pedido for vago demais, ou pedir algo fora de um modelo de documento (gerar código, \
   contornar controle de acesso, preencher dados de uma pessoa real), rejeite com \
   success=false e uma mensagem clara.

Retorne sempre um único documento.
"""

SEGMENTO_PADRAO = "Segmento não informado: mantenha o texto neutro e geral."


class CampoGerado(BaseModel):
    nome: str
    tipo: str = "texto"
    origem: str
    obrigatorio: bool = False
    obrigatorio_quando: str | None = None
    rotulo: str | None = None


class GeneratedDocument(BaseModel):
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    modelo_id: str | None = None
    campos: list[CampoGerado] = Field(default_factory=list)


class DocumentGeneratorResult(BaseModel):
    success: bool
    document: GeneratedDocument | None = None
    error: str | None = None


def instrucoes_do_segmento(contexto: str | None = None, guia: str | None = None) -> str:
    partes = [INSTRUCTIONS, "Segmento do cliente: " + (contexto or SEGMENTO_PADRAO).strip()]
    if guia and guia.strip():
        partes.append("Orientações do cliente para modelos:\n" + guia.strip())
    return "\n\n".join(partes)


class VaultProviderLike:
    def search(self, client_id: str, query: str, limit: int = 10) -> tuple[VaultNote, ...]: ...
    def add_note(
        self,
        client_id: str,
        title: str,
        content: str,
        frontmatter: dict[str, Any] | None = None,
    ) -> VaultNote: ...


def build_document_agent(
    model_id: str | None = None, contexto: str | None = None, guia: str | None = None
) -> AgnoAgentLike:
    from agno.agent import Agent

    model, use_json_mode = resolve_agent_model(model_id)

    return Agent(
        name="Vault Document Generator",
        model=model,
        instructions=instrucoes_do_segmento(contexto, guia),
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


def _slug(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:64] or "modelo"


def texto_do_modelo(document: GeneratedDocument) -> tuple[str, str]:
    """Nota de `_modelos/` com o front matter que o emissor de documentos lê (spec 035)."""
    modelo_id = _slug(document.modelo_id or document.title)
    cabecalho = {
        "tipo": "modelo",
        "zona": "modelos",
        "documento": modelo_id,
        "versao": 1,
        "titulo": document.title,
        "tags": document.tags,
        "campos": [c.model_dump(exclude_none=True) for c in document.campos],
    }
    corpo = yaml.safe_dump(cabecalho, sort_keys=False, allow_unicode=True)
    return modelo_id, f"---\n{corpo}---\n{document.content.strip()}\n"


def promote_document(
    client_id: str,
    draft_id: str,
    draft_store: DraftStore,
    vault_provider: VaultProviderLike,
    audit_sink: AuditSink,
    approved_by: str = PROMOTER_ACTOR,
    salvar_modelo: Callable[[str, str], str] | None = None,
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
        if document.campos:
            modelo_id, texto = texto_do_modelo(document)
            parse_modelo(texto)
            if salvar_modelo is None:
                raise ModeloInvalido("este cliente não tem pasta de modelos configurada")
            note_id = salvar_modelo(modelo_id, texto)
        else:
            note_id = vault_provider.add_note(
                client_id, document.title, document.content, {"tags": document.tags}
            ).note_id
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
        {"draft_id": draft_id, "note_id": note_id},
    )

    return {"promoted": True, "draft_id": draft_id, "note_id": note_id}
