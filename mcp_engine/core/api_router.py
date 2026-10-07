"""Superfície REST usada só pelo painel super_admin (dashboard React/Vite).

Não substitui o protocolo MCP — é uma casca fina em cima do mesmo
`BRMEngine`/`RBACFastMCP` que stdio e SSE já usam (ver mcp_server.py,
http_app.py), porque um browser fetch() consome JSON simples muito mais
fácil que abrir uma conexão MCP/SSE. Toda rota aqui é restrita a
`Role.SUPER_ADMIN` — o painel inteiro é ferramenta de administração, não
tem uma versão "viewer" ou "admin comum".
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Iterator

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from mcp_engine.core.auth import (
    Role,
    UserProfile,
    current_user_var,
    get_current_user,
    set_current_user,
)
from mcp_engine.core.models import AuditDecision, AuditEvent

if TYPE_CHECKING:
    from mcp_engine.core.mcp_server import BRMEngine
    from mcp_engine.core.rbac_server import RBACFastMCP


def _require_super_admin() -> None:
    if get_current_user().role is not Role.SUPER_ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "esta API é restrita ao perfil super_admin")


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


class SimulateSkillRequest(BaseModel):
    perfil_email: str
    skill: str
    argumentos: dict[str, Any] = Field(default_factory=dict)


class SimulateVaultRequest(BaseModel):
    perfil_email: str
    consulta: str
    limite: int = 6


@contextmanager
def _acting_as(profile: UserProfile) -> Iterator[None]:
    token = set_current_user(profile)
    try:
        yield
    finally:
        current_user_var.reset(token)


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

    def _require_simulator() -> None:
        if not engine.simulador_habilitado:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "o simulador está desligado neste cliente"
            )

    def _profile_or_404(email: str) -> UserProfile:
        profile = engine.user_store.get_by_email(email)
        if profile is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"perfil desconhecido: {email}")
        return profile

    def _record_simulation(action: str, como: UserProfile, details: dict[str, Any]) -> None:
        real = get_current_user()
        engine._audit_sink.record(
            AuditEvent(
                client_id=engine.client_id,
                occurred_at=datetime.now(UTC),
                actor=real.identity,
                action=f"simulacao:{action}",
                decision=AuditDecision.ALLOWED,
                details={"executado_por": real.identity, "como": como.identity, **details},
            )
        )

    @router.get("/simulador/perfis")
    def simulator_profiles() -> dict[str, Any]:
        _require_simulator()
        labels = engine.demo_scenarios().profiles
        users = engine.user_store.list_users()
        return {
            "total": len(users),
            "perfis": [
                {
                    "identity": u.identity,
                    "role": u.role.value,
                    "rotulo": labels.get(u.identity, u.identity.split("@")[0]),
                }
                for u in users
            ],
        }

    @router.get("/simulador/skills")
    def simulator_skills() -> dict[str, Any]:
        _require_simulator()
        skills = []
        for skill in engine.skills:
            params = {
                name: {
                    "type": (spec or {}).get("type", "string"),
                    "required": bool((spec or {}).get("required")),
                    "description": (spec or {}).get("description"),
                }
                for name, spec in skill.parameters.items()
                if name not in skill.derived_facts
            }
            skills.append(
                {
                    "name": skill.name,
                    "description": skill.description,
                    "examples": list(skill.examples),
                    "parameters": params,
                }
            )
        return {"total": len(skills), "skills": skills}

    @router.get("/simulador/cenarios")
    def simulator_scenarios() -> dict[str, Any]:
        _require_simulator()
        scenarios = engine.demo_scenarios().scenarios
        return {
            "total": len(scenarios),
            "cenarios": [
                {
                    "id": sc.id,
                    "titulo": sc.title,
                    "skill": sc.skill,
                    "argumentos": sc.arguments,
                    "perfil": sc.profile,
                    "nota": sc.note,
                }
                for sc in scenarios
            ],
        }

    @router.post("/simulador/executar")
    def simulator_run(payload: SimulateSkillRequest) -> dict[str, Any]:
        _require_simulator()
        profile = _profile_or_404(payload.perfil_email)
        trace: dict[str, Any] = {}
        with _acting_as(profile):
            result = engine.call_skill(
                payload.skill, payload.argumentos, simulacao=True, trace=trace
            )
        _record_simulation(payload.skill, profile, {"allowed": result.get("allowed")})
        decision = "allowed"
        if not result.get("allowed"):
            decision = (
                "pending_approval"
                if result.get("reason") == "human approval required"
                else "denied"
            )
        return {
            "perfil": {"identity": profile.identity, "role": profile.role.value},
            "decisao": decision,
            "resultado": result,
            "fatos_derivados": trace.get("fatos_derivados", {}),
            "dlp": trace.get("dlp", {}),
        }

    @router.post("/simulador/vault")
    def simulator_vault(payload: SimulateVaultRequest) -> dict[str, Any]:
        _require_simulator()
        profile = _profile_or_404(payload.perfil_email)
        with _acting_as(profile):
            result = engine.vault_search(payload.consulta, payload.limite)
        _record_simulation("vault", profile, {"consulta": payload.consulta})
        notes = [
            {
                "note_id": n["note_id"],
                "title": n["title"],
                "tipo": n["frontmatter"].get("tipo"),
                "modo": "completa" if n["content"] else "so_ficha",
                "trecho": n["content"][:220],
            }
            for n in result["notes"]
        ]
        return {
            "perfil": {"identity": profile.identity, "role": profile.role.value},
            "total": result["total"],
            "ocultas": result.get("ocultas", 0),
            "politicas": result.get("politicas", []),
            "notas": notes,
        }

    @router.get("/simulador/matriz")
    def simulator_matrix() -> dict[str, Any]:
        _require_simulator()
        users = engine.user_store.list_users()
        labels = engine.demo_scenarios().profiles
        rows = []
        with engine.vault_snapshot():
            for sc in engine.demo_scenarios().scenarios:
                if not sc.in_matrix:
                    continue
                cells = []
                for user in users:
                    with _acting_as(user):
                        verdict = engine.avaliar_skill(sc.skill, sc.arguments)
                    cells.append(
                        {
                            "perfil": user.identity,
                            "efeito": verdict["efeito"],
                            "rule_id": verdict["rule_id"],
                        }
                    )
                rows.append({"id": sc.id, "titulo": sc.title, "celulas": cells})
        return {
            "perfis": [
                {
                    "identity": u.identity,
                    "role": u.role.value,
                    "rotulo": labels.get(u.identity, u.identity.split("@")[0]),
                }
                for u in users
            ],
            "cenarios": rows,
        }

    return router
