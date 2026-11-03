"""API REST da Central de Ações (spec 035), consumida pela página `/acao/<id>`.

Toda rota exige identidade (o middleware de `http_app` já recusa anônimo) e audita. Erros
saem como `{"detail": {"motivo": ..., ...}}` com o código HTTP da recusa.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from mcp_engine.core.acoes import AcaoErro
from mcp_engine.core.atributos_usuario import PIN_BLOQUEADO, PinInvalido
from mcp_engine.core.documentos import DocumentoErro, IntegridadeViolada
from mcp_engine.core.identidade import get_identidade
from mcp_engine.core.operacoes import RANK

if TYPE_CHECKING:
    from mcp_engine.core.mcp_server import BRMEngine


class CampoPreenchido(BaseModel):
    item: int = 0
    nome: str = Field(max_length=64)
    valor: str = Field(max_length=500)


class ConfirmarRequest(BaseModel):
    pin: str = Field(max_length=16)
    campos: list[CampoPreenchido] = Field(default_factory=list, max_length=50)


class CamposRequest(BaseModel):
    pin: str = Field(max_length=16)
    campos: list[CampoPreenchido] = Field(min_length=1, max_length=50)


class DefinirPinRequest(BaseModel):
    pin_novo: str = Field(max_length=16)
    pin_atual: str | None = Field(default=None, max_length=16)


class UnidadesRequest(BaseModel):
    unidades: list[str] = Field(max_length=50)


def _erro(exc: AcaoErro) -> HTTPException:
    return HTTPException(exc.status, {"motivo": exc.motivo, **exc.extra})


def _gestor() -> None:
    if RANK.get(get_identidade().perfil.role.value, -1) < RANK["admin"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, {"motivo": "Restrito ao gestor."})


def build_acoes_router(engine: BRMEngine) -> APIRouter:
    router = APIRouter()
    central = engine.acoes

    def auditar(evento: str, ok: bool, **detalhes: Any) -> None:
        engine.auditar_evento(evento, ok, detalhes)

    @router.get("/eu")
    def eu() -> dict[str, Any]:
        ident = get_identidade()
        return {
            **ident.publica(),
            "nome": central.atributos.nome(ident.provedor, ident.id),
            "pin_definido": central.atributos.pin_definido(ident.provedor, ident.id),
            "unidades": central.atributos.unidades(ident.provedor, ident.id),
        }

    @router.put("/eu/pin")
    def definir_pin(payload: DefinirPinRequest) -> dict[str, Any]:
        ident = get_identidade()
        auditar("pin:definindo", True, pessoa=ident.perfil.identity, provedor=ident.provedor)
        try:
            resultado = central.atributos.definir_pin(
                ident.provedor,
                ident.id,
                payload.pin_novo,
                por=ident.perfil.identity,
                atual=payload.pin_atual,
            )
        except PinInvalido as exc:
            raise HTTPException(422, {"motivo": str(exc)}) from None
        if not resultado.ok:
            auditar("pin:troca_negada", False, motivo=resultado.status)
            codigo = 423 if resultado.status == PIN_BLOQUEADO else 401
            raise HTTPException(codigo, {"motivo": "PIN atual incorreto ou bloqueado."})
        auditar("pin:definido", True, pessoa=ident.perfil.identity, provedor=ident.provedor)
        return {"pin_definido": True}

    @router.get("/pessoas")
    def pessoas() -> dict[str, Any]:
        _gestor()
        lista = [
            {
                "usuario_id": u.identity,
                "provedor": "local",
                "nome": central.atributos.nome("local", u.identity),
                "papel": u.role.value,
                "pin_definido": central.atributos.pin_definido("local", u.identity),
            }
            for u in engine.user_store.list_users()
            if u.identity != "anonymous"
        ]
        return {"total": len(lista), "pessoas": lista}

    @router.delete("/usuarios/{provedor}/{usuario_id}/pin")
    def redefinir_pin(provedor: str, usuario_id: str) -> dict[str, Any]:
        _gestor()
        ident = get_identidade()
        if (provedor, usuario_id) == (ident.provedor, ident.id):
            raise HTTPException(403, {"motivo": "Troque o próprio PIN informando o atual."})
        auditar("pin:redefinindo", True, provedor=provedor, usuario_id=usuario_id)
        if not central.atributos.redefinir_pin(provedor, usuario_id, por=ident.perfil.identity):
            raise HTTPException(404, {"motivo": "Pessoa sem PIN definido."})
        auditar("pin:redefinido", True, provedor=provedor, usuario_id=usuario_id)
        return {"pin_definido": False}

    @router.put("/usuarios/{provedor}/{usuario_id}/unidades")
    def definir_unidades(provedor: str, usuario_id: str, payload: UnidadesRequest) -> dict:
        _gestor()
        ident = get_identidade()
        auditar("unidades:definindo", True, provedor=provedor, usuario_id=usuario_id)
        central.atributos.definir_unidades(
            provedor, usuario_id, payload.unidades, por=ident.perfil.identity
        )
        auditar("unidades:definidas", True, provedor=provedor, usuario_id=usuario_id)
        return {"unidades": central.atributos.unidades(provedor, usuario_id)}

    @router.get("/acoes/{acao_id}")
    def abrir(acao_id: str) -> dict[str, Any]:
        try:
            return central.abrir(get_identidade(), acao_id)
        except AcaoErro as exc:
            raise _erro(exc) from None

    @router.post("/acoes/{acao_id}/campos")
    def gravar_campos(acao_id: str, payload: CamposRequest) -> dict[str, Any]:
        try:
            return central.gravar_campos(
                get_identidade(), acao_id, payload.pin, [c.model_dump() for c in payload.campos]
            )
        except AcaoErro as exc:
            raise _erro(exc) from None

    @router.post("/acoes/{acao_id}/confirmar")
    def confirmar(acao_id: str, payload: ConfirmarRequest) -> dict[str, Any]:
        try:
            return central.confirmar(
                get_identidade(), acao_id, payload.pin, [c.model_dump() for c in payload.campos]
            )
        except AcaoErro as exc:
            raise _erro(exc) from None

    @router.post("/acoes/{acao_id}/liberar")
    def liberar(acao_id: str, payload: ConfirmarRequest) -> dict[str, Any]:
        try:
            return central.liberar(
                get_identidade(), acao_id, payload.pin, [c.model_dump() for c in payload.campos]
            )
        except AcaoErro as exc:
            raise _erro(exc) from None

    @router.post("/acoes/{acao_id}/cancelar")
    def cancelar(acao_id: str) -> dict[str, Any]:
        try:
            return central.cancelar(get_identidade(), acao_id)
        except AcaoErro as exc:
            raise _erro(exc) from None

    def _documento_autorizado(documento_id: str) -> Any:
        ident = get_identidade()
        doc = engine.documentos.store.obter(documento_id)
        if doc is None:
            raise HTTPException(404, {"motivo": "Documento não encontrado."})
        gestor = RANK.get(ident.perfil.role.value, -1) >= RANK["admin"]
        if doc.emitido_por != ident.perfil.identity and not gestor:
            auditar("documento:acesso_negado", False, documento_id=documento_id)
            raise HTTPException(404, {"motivo": "Documento não encontrado."})
        return doc

    @router.get("/documentos/{documento_id}")
    def documento(documento_id: str) -> dict[str, Any]:
        return _documento_autorizado(documento_id).publico()

    @router.get("/documentos/{documento_id}/pdf")
    def documento_pdf(documento_id: str) -> Response:
        doc = _documento_autorizado(documento_id)
        try:
            conteudo = engine.documentos.store.ler_pdf(doc.id)
        except IntegridadeViolada:
            auditar("documento:integridade_violada", False, documento_id=doc.id)
            raise HTTPException(500, {"motivo": "O arquivo não confere com o hash."}) from None
        except (DocumentoErro, OSError):
            raise HTTPException(404, {"motivo": "Arquivo do documento indisponível."}) from None
        auditar("documento:baixado", True, documento_id=doc.id, sha256=doc.sha256)
        return Response(
            conteudo,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{doc.modelo_id}-{doc.id}.pdf"',
                "Cache-Control": "no-store",
                "X-Content-SHA256": doc.sha256,
            },
        )

    return router
