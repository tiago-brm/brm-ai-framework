from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Sequence

from mcp.server.fastmcp import FastMCP
from mcp.types import ContentBlock
from mcp.types import Tool as MCPTool

from mcp_engine.core.auth import get_current_user, is_tool_allowed
from mcp_engine.core.contracts import AuditSink
from mcp_engine.core.models import AuditDecision, AuditEvent

if TYPE_CHECKING:
    from mcp_engine.core.user_store import SQLUserStore


class RBACFastMCP(FastMCP):
    """FastMCP com controle de acesso baseado em perfil (RBAC).

    Dois pontos de enforcement, pedidos explicitamente:
    1. `list_tools()` — filtra o catálogo pelo perfil da identidade atual
       (lida via `get_current_user()`, nunca de header/env var direto —
       ver `mcp_engine/core/auth.py` para o porquê do isolamento).
    2. `call_tool()` — valida de novo antes de delegar pro tool real. Isso
       cobre uma chamada forçada a uma tool que nem apareceu em
       `list_tools()`: nada executa, e a negativa fica registrada no audit
       sink do engine, com o mesmo formato de resposta
       (`{"allowed": False, "reason": ...}`) usado em `call_skill` e nas
       outras tools do motor.
    """

    def __init__(
        self,
        *args: Any,
        audit_sink: AuditSink,
        client_id: str,
        user_store: SQLUserStore | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._audit_sink = audit_sink
        self._client_id = client_id
        self._user_store = user_store

    async def list_tools(self) -> list[MCPTool]:
        tools = await super().list_tools()
        role = get_current_user().role
        return [t for t in tools if is_tool_allowed(role, t.name, self._user_store)]

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        user = get_current_user()
        if not is_tool_allowed(user.role, name, self._user_store):
            self._audit_sink.record(
                AuditEvent(
                    client_id=self._client_id,
                    occurred_at=datetime.now(UTC),
                    actor=user.identity,
                    action=name,
                    decision=AuditDecision.DENIED,
                    details={"reason": "rbac_denied", "role": user.role.value},
                )
            )
            return {
                "allowed": False,
                "reason": f"Acesso negado: perfil '{user.role.value}' não pode executar '{name}'.",
            }
        return await super().call_tool(name, arguments)
