"""Superfície REST usada só pelo painel super_admin (dashboard React/Vite).

Não substitui o protocolo MCP — é uma casca fina em cima do mesmo
`BRMEngine`/`RBACFastMCP` que stdio e SSE já usam (ver mcp_server.py,
http_app.py), porque um browser fetch() consome JSON simples muito mais
fácil que abrir uma conexão MCP/SSE. Toda rota aqui é restrita a
`Role.SUPER_ADMIN` — o painel inteiro é ferramenta de administração, não
tem uma versão "viewer" ou "admin comum".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from mcp_engine.core.auth import Role, get_current_user

if TYPE_CHECKING:
    from mcp_engine.core.mcp_server import BRMEngine
    from mcp_engine.core.rbac_server import RBACFastMCP


def _require_super_admin() -> None:
    if get_current_user().role is not Role.SUPER_ADMIN:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "esta API é restrita ao perfil super_admin"
        )


def _raise_if_denied(result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("allowed", True):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, result.get("reason"))
    return result


class CreateUserRequest(BaseModel):
    email: str
    role: str
    api_token: str | None = None


class UpdateRoleRequest(BaseModel):
    role: str


class UpdatePermissionRequest(BaseModel):
    role: str
    tool_name: str
    allowed: bool


def build_api_router(engine: BRMEngine, mcp_server: RBACFastMCP) -> APIRouter:
    router = APIRouter(dependencies=[Depends(_require_super_admin)])

    @router.get("/me")
    def get_me() -> dict[str, Any]:
        user = get_current_user()
        return {"identity": user.identity, "role": user.role.value}

    @router.get("/users")
    def list_users() -> dict[str, Any]:
        return engine.listar_usuarios()

    @router.post("/users", status_code=status.HTTP_201_CREATED)
    def create_user(payload: CreateUserRequest) -> dict[str, Any]:
        result = engine.criar_usuario(payload.email, payload.role, payload.api_token)
        return _raise_if_denied(result)

    @router.patch("/users/{email}/role")
    def update_user_role(email: str, payload: UpdateRoleRequest) -> dict[str, Any]:
        return _raise_if_denied(engine.atualizar_perfil_usuario(email, payload.role))

    @router.get("/permissions")
    def list_permissions() -> dict[str, Any]:
        return engine.listar_permissoes()

    @router.put("/permissions")
    def update_permission(payload: UpdatePermissionRequest) -> dict[str, Any]:
        return _raise_if_denied(
            engine.atualizar_permissao(payload.role, payload.tool_name, payload.allowed)
        )

    @router.get("/tools")
    async def list_tools() -> dict[str, Any]:
        # Chamado com identidade super_admin (gate do router) — o bypass em
        # is_tool_allowed garante que list_tools() aqui devolve TODAS as
        # tools registradas, mesmo as restritas a outros perfis, para o
        # painel poder listar/gerenciar o catálogo inteiro.
        tools = await mcp_server.list_tools()
        return {
            "total": len(tools),
            "tools": [{"name": t.name, "description": t.description} for t in tools],
        }

    @router.get("/rules")
    def list_rules() -> dict[str, Any]:
        ruleset = engine.rules
        return {
            "total": len(ruleset.rules),
            "rules": [
                {
                    "id": rule.id,
                    "description": rule.description,
                    "condition": rule.condition,
                    "effect": rule.effect.value,
                    "message": rule.message,
                }
                for rule in ruleset.rules
            ],
        }

    @router.get("/vault/notes")
    def list_vault_notes() -> dict[str, Any]:
        return engine.vault_list_notes()

    @router.get("/audit")
    def list_audit(
        start_date: str | None = None,
        end_date: str | None = None,
        action: str | None = None,
        decision: str | None = None,
    ) -> dict[str, Any]:
        return engine.query_audit_log(
            start_date=start_date, end_date=end_date, action=action, decision=decision
        )

    return router
