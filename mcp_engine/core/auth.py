from __future__ import annotations

import os
from contextvars import ContextVar, Token
from enum import Enum
from typing import TYPE_CHECKING

from mcp_engine.core.models import _StrictModel

if TYPE_CHECKING:
    from mcp_engine.core.user_store import SQLUserStore


class Role(str, Enum):
    ADMIN = "admin"
    VIEWER = "viewer"
    SUPER_ADMIN = "super_admin"


class UserProfile(_StrictModel):
    identity: str
    role: Role


# Perfil usado quando nenhuma credencial é encontrada, ou a credencial não
# bate com nada no mock DB. Deliberadamente "viewer", nunca "admin" — negar
# por padrão é a postura segura quando a identidade é desconhecida.
ANONYMOUS_PROFILE = UserProfile(identity="anonymous", role=Role.VIEWER)

# Quais tools cada perfil enxerga em tools/list e pode executar em
# tools/call. Uma tool que não aparece em nenhum conjunto aqui continua
# visível/executável por qualquer perfil — o escopo desta primeira versão de
# RBAC é só as tools abaixo; tools existentes (call_skill, vault_*,
# consultar_processo_datajud) não são restringidas ainda. Estender o RBAC
# para elas é só adicionar entradas aqui. criar_usuario/listar_usuarios são
# admin-only: gestão de usuário não é uma ação de viewer.
# atualizar_perfil_usuario (trocar o role de alguém já existente) é restrita
# a super_admin — é um poder maior que só criar usuário novo. super_admin
# também é listado aqui (redundante com o bypass de is_tool_allowed abaixo)
# só para essas tools aparecerem corretamente em tools/list.
ROLE_TOOLS: dict[Role, frozenset[str]] = {
    Role.VIEWER: frozenset({"consultar_dados"}),
    Role.ADMIN: frozenset(
        {"consultar_dados", "deletar_banco", "criar_usuario", "listar_usuarios"}
    ),
    Role.SUPER_ADMIN: frozenset(
        {
            "consultar_dados",
            "deletar_banco",
            "criar_usuario",
            "listar_usuarios",
            "atualizar_perfil_usuario",
            "listar_permissoes",
            "atualizar_permissao",
        }
    ),
}

# ---------------------------------------------------------------------------
# Fronteira única entre "de onde veio a identidade" e "quem está logado
# agora". Só resolve_stdio_identity() e resolve_http_identity() conhecem
# transporte (env var vs. header HTTP) — tudo mais no sistema (RBACFastMCP,
# is_tool_allowed, e as próprias tools) só chama get_current_user().
# ---------------------------------------------------------------------------

current_user_var: ContextVar[UserProfile | None] = ContextVar("current_user", default=None)


def set_current_user(profile: UserProfile) -> Token[UserProfile | None]:
    return current_user_var.set(profile)


def get_current_user() -> UserProfile:
    return current_user_var.get() or ANONYMOUS_PROFILE


def resolve_stdio_identity(store: SQLUserStore) -> UserProfile:
    """Identidade do modo stdio: lida uma vez do ambiente do processo.

    Um processo stdio é uma sessão de um único usuário — não há "por
    request" aqui, diferente do modo HTTP. Tenta MCP_USER_EMAIL primeiro,
    depois MCP_API_TOKEN; sem nenhum dos dois, ou credencial desconhecida
    no banco (`store`), cai em ANONYMOUS_PROFILE (viewer).
    """
    email = os.getenv("MCP_USER_EMAIL")
    if email:
        profile = store.get_by_email(email)
        if profile is not None:
            return profile

    token = os.getenv("MCP_API_TOKEN")
    if token:
        profile = store.get_by_token(token)
        if profile is not None:
            return profile

    return ANONYMOUS_PROFILE


def resolve_http_identity(store: SQLUserStore, authorization_header: str | None) -> UserProfile:
    """Identidade do modo HTTP/SSE: lida do header Authorization de um
    request específico — chamada pelo middleware a cada conexão, nunca uma
    vez só. Espera "Bearer <token>"; header ausente, malformado, ou token
    desconhecido no banco (`store`) caem em ANONYMOUS_PROFILE (viewer).
    """
    if not authorization_header:
        return ANONYMOUS_PROFILE

    scheme, _, token = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return ANONYMOUS_PROFILE

    return store.get_by_token(token) or ANONYMOUS_PROFILE


def is_tool_allowed(role: Role, tool_name: str, store: SQLUserStore | None = None) -> bool:
    """True se `role` pode ver/executar `tool_name`.

    super_admin tem acesso irrestrito por construção — o bypass é
    verificado antes de qualquer outra checagem, então continua valendo
    mesmo para uma tool restrita nova que ainda não tenha ganhado uma
    entrada explícita em nenhum lugar.

    Quando `store` é passado (todo caminho real via stdio/HTTP passa,
    ver RBACFastMCP), a decisão vem da tabela `tool_permissions`
    (SQLUserStore.is_tool_allowed) — editável em runtime pelo super_admin
    via `atualizar_permissao`, sem deploy. `store=None` é só o fallback
    usado por quem chama esta função isoladamente (ex.: testes unitários
    de auth.py) contra o dict estático ROLE_TOOLS, com a mesma semântica:
    tool sem entrada em nenhum conjunto = aberta a todo mundo.
    """
    if role is Role.SUPER_ADMIN:
        return True

    if store is not None:
        return store.is_tool_allowed(role, tool_name)

    restricted_to = {t for tools in ROLE_TOOLS.values() for t in tools}
    if tool_name not in restricted_to:
        return True
    return tool_name in ROLE_TOOLS.get(role, frozenset())
