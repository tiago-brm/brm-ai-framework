"""Persistência de usuários/perfis — um SQLite por cliente.

`SQLUserStore` é o "banco de dados" real por trás do RBAC (ver
mcp_engine/core/auth.py): substitui o dicionário em memória que a spec 010
usava para demonstrar o mecanismo. SQLAlchemy síncrono, um arquivo por
cliente em `clients/<cliente>/config/users.db` — mesma convenção de
`config_root / client_id / "config" / <algo>` que `FileSystemRuleProvider`/
`DraftStore`/etc. já seguem (ver mcp_engine/core/config_loader.py).
"""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from sqlalchemy import UniqueConstraint, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from mcp_engine.core.auth import Role, UserProfile


class Base(DeclarativeBase):
    pass


_TOKEN_PREFIX = "sha256:"
IDENTIDADE_SISTEMA = "sistema:"


def hash_token(token: str) -> str:
    """Forma em que um token é guardado: só o hash chega ao banco (spec 020)."""
    return _TOKEN_PREFIX + hashlib.sha256(token.encode("utf-8")).hexdigest()


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(unique=True, index=True)
    api_token: Mapped[str | None] = mapped_column(unique=True, index=True, nullable=True)
    role: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))

    def to_profile(self) -> UserProfile:
        return UserProfile(identity=self.email, role=Role(self.role))


class ToolPermissionRow(Base):
    """Fonte de verdade persistida de `is_tool_allowed` — substitui o dict
    fixo `ROLE_TOOLS` (mcp_engine/core/auth.py) por role/tool, editável em
    runtime via `atualizar_permissao` (super_admin-only), sem precisar de
    deploy. Semântica idêntica ao dict que substitui: ausência de QUALQUER
    linha para uma tool = tool aberta a todo mundo; presença de ao menos uma
    linha = tool restrita, e cada perfil segue sua própria linha (ou nega,
    se não tiver uma)."""

    __tablename__ = "tool_permissions"
    __table_args__ = (UniqueConstraint("role", "tool_name", name="uq_tool_permission_role_tool"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str]
    tool_name: Mapped[str]
    allowed: Mapped[bool]


class ToolPermission(NamedTuple):
    role: Role
    tool_name: str
    allowed: bool


# Espelha o ROLE_TOOLS de hoje (mcp_engine/core/auth.py) — seedado
# automaticamente (não é credencial, é config de schema, mesmo raciocínio
# de Base.metadata.create_all ser automático). super_admin nunca precisa de
# linha aqui: o bypass em is_tool_allowed() é incondicional antes de
# consultar a tabela.
_DEFAULT_PERMISSIONS: tuple[tuple[Role, str, bool], ...] = (
    (Role.VIEWER, "consultar_dados", True),
    (Role.ADMIN, "consultar_dados", True),
    (Role.ADMIN, "deletar_banco", True),
    (Role.ADMIN, "criar_usuario", True),
    (Role.ADMIN, "listar_usuarios", True),
    (Role.ADMIN, "atualizar_perfil_usuario", False),
    (Role.ADMIN, "listar_permissoes", False),
    (Role.ADMIN, "atualizar_permissao", False),
    (Role.ADMIN, "rotacionar_token", False),
    (Role.VIEWER, "confirmar_operacao", True),
    (Role.ADMIN, "confirmar_operacao", True),
    (Role.VIEWER, "cancelar_operacao", True),
    (Role.ADMIN, "cancelar_operacao", True),
    (Role.VIEWER, "listar_operacoes", True),
    (Role.ADMIN, "listar_operacoes", True),
    (Role.ADMIN, "aprovar_operacao", True),
    (Role.ADMIN, "rejeitar_operacao", True),
)


class UserAlreadyExistsError(Exception):
    def __init__(self, email: str) -> None:
        super().__init__(f"user already exists: {email!r}")
        self.email = email


class UserNotFoundError(Exception):
    def __init__(self, email: str) -> None:
        super().__init__(f"user not found: {email!r}")
        self.email = email


def _resolve_db_path(config_root: Path, client_id: str) -> Path:
    legacy = config_root / client_id / "config" / "users.db"
    data_dir = os.getenv("BRM_DATA_DIR")
    if not data_dir:
        legacy.parent.mkdir(parents=True, exist_ok=True)
        return legacy
    target = Path(data_dir) / client_id / "users.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() and legacy.exists():
        shutil.copy2(legacy, target)
    return target


class SQLUserStore:
    def __init__(self, config_root: Path, client_id: str) -> None:
        db_path = _resolve_db_path(Path(config_root), client_id)
        self.db_path = db_path
        self._engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(self._engine)
        self.fresh_permissions = False
        self._seed_default_permissions()
        self._migrate_plaintext_tokens()

    def _migrate_plaintext_tokens(self) -> None:
        """Converte tokens antigos em texto puro para hash, uma única vez."""
        with Session(self._engine) as session:
            rows = session.scalars(
                select(UserRow).where(
                    UserRow.api_token.is_not(None),
                    ~UserRow.api_token.startswith(_TOKEN_PREFIX),
                )
            ).all()
            for row in rows:
                row.api_token = hash_token(row.api_token)
            if rows:
                session.commit()

    def _seed_default_permissions(self) -> None:
        with Session(self._engine) as session:
            has_any = session.scalar(select(ToolPermissionRow.id).limit(1))
            if has_any is not None:
                return
            self.fresh_permissions = True
            for role, tool_name, allowed in _DEFAULT_PERMISSIONS:
                session.add(
                    ToolPermissionRow(role=role.value, tool_name=tool_name, allowed=allowed)
                )
            session.commit()

    def get_by_email(self, email: str) -> UserProfile | None:
        with Session(self._engine) as session:
            row = session.scalar(select(UserRow).where(UserRow.email == email))
            return row.to_profile() if row else None

    def get_by_token(self, token: str) -> UserProfile | None:
        with Session(self._engine) as session:
            row = session.scalar(select(UserRow).where(UserRow.api_token == hash_token(token)))
            return row.to_profile() if row else None

    def create_user(
        self, email: str, role: Role, api_token: str | None = None
    ) -> tuple[UserProfile, str]:
        """Cria um usuário e devolve (perfil, token).

        Gera um token com secrets.token_urlsafe(24) quando não informado.
        Esta é a única chamada que devolve o token em texto puro — não há
        como recuperá-lo depois (list_users nunca inclui token).
        """
        if email.strip().lower().startswith(IDENTIDADE_SISTEMA):
            raise ValueError("o prefixo 'sistema:' é reservado aos atores de gatilho")
        token = api_token or secrets.token_urlsafe(32)
        with Session(self._engine) as session:
            existing = session.scalar(select(UserRow).where(UserRow.email == email))
            if existing is not None:
                raise UserAlreadyExistsError(email)

            row = UserRow(email=email, api_token=hash_token(token), role=role.value)
            session.add(row)
            session.commit()
            return row.to_profile(), token

    def rotate_token(self, email: str) -> str:
        """Gera um token novo, invalida o anterior e devolve o valor uma única vez."""
        token = secrets.token_urlsafe(32)
        with Session(self._engine) as session:
            row = session.scalar(select(UserRow).where(UserRow.email == email))
            if row is None:
                raise UserNotFoundError(email)
            row.api_token = hash_token(token)
            session.commit()
        return token

    def list_users(self) -> list[UserProfile]:
        with Session(self._engine) as session:
            rows = session.scalars(select(UserRow).order_by(UserRow.email))
            return [row.to_profile() for row in rows]

    def tools_without_permissions(self, tool_names: list[str]) -> list[str]:
        """Tools que não têm nenhuma linha em `tool_permissions` (negadas em modo deny)."""
        with Session(self._engine) as session:
            known = set(session.scalars(select(ToolPermissionRow.tool_name)).all())
        return [name for name in tool_names if name not in known]

    def seed_open_tools(
        self, tool_names: list[str], roles: tuple[Role, ...] = (Role.VIEWER, Role.ADMIN)
    ) -> list[str]:
        """Cria linhas explícitas de acesso para tools sem nenhuma linha.

        Preserva, de forma visível e editável, o que antes era "aberto por
        omissão". Devolve as tools semeadas.
        """
        missing = self.tools_without_permissions(tool_names)
        if not missing:
            return []
        with Session(self._engine) as session:
            for name in missing:
                for role in roles:
                    session.add(ToolPermissionRow(role=role.value, tool_name=name, allowed=True))
            session.commit()
        return missing

    def is_tool_allowed(self, role: Role, tool_name: str, default: str = "allow") -> bool:
        """Substitui a lookup em ROLE_TOOLS por uma consulta na tabela
        `tool_permissions`. Sem linha para (role, tool_name): se NENHUMA
        linha existe pra essa tool (em qualquer role), ela é irrestrita —
        aberta a todo mundo; se existe ao menos uma (de outro role), essa
        tool é restrita e este role, sem linha própria, é negado."""
        with Session(self._engine) as session:
            row = session.scalar(
                select(ToolPermissionRow).where(
                    ToolPermissionRow.role == role.value,
                    ToolPermissionRow.tool_name == tool_name,
                )
            )
            if row is not None:
                return row.allowed

            any_row = session.scalar(
                select(ToolPermissionRow.id)
                .where(ToolPermissionRow.tool_name == tool_name)
                .limit(1)
            )
            if any_row is None:
                return default == "allow"
            return False

    def set_permission(self, role: Role, tool_name: str, allowed: bool) -> None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(ToolPermissionRow).where(
                    ToolPermissionRow.role == role.value,
                    ToolPermissionRow.tool_name == tool_name,
                )
            )
            if row is not None:
                row.allowed = allowed
            else:
                session.add(
                    ToolPermissionRow(role=role.value, tool_name=tool_name, allowed=allowed)
                )
            session.commit()

    def list_permissions(self) -> list[ToolPermission]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(ToolPermissionRow).order_by(
                    ToolPermissionRow.tool_name, ToolPermissionRow.role
                )
            )
            return [
                ToolPermission(role=Role(row.role), tool_name=row.tool_name, allowed=row.allowed)
                for row in rows
            ]

    def update_role(self, email: str, role: Role) -> UserProfile:
        """Troca o perfil de um usuário já existente. Levanta
        UserNotFoundError se o e-mail não existir — trocar perfil de
        ninguém não deve criar o usuário silenciosamente."""
        with Session(self._engine) as session:
            row = session.scalar(select(UserRow).where(UserRow.email == email))
            if row is None:
                raise UserNotFoundError(email)

            row.role = role.value
            session.commit()
            session.refresh(row)
            return row.to_profile()


def seed_demo_users(store: SQLUserStore) -> None:
    """Recria os três usuários de demonstração usados nos testes manuais
    das specs 010/011/012 (brm=super_admin, socio=admin,
    estagiario=viewer). Idempotente — ignora quem já existir. Nunca
    chamado automaticamente por main()/create_app(): auto-seedar
    credenciais conhecidas na inicialização de um cliente real seria um
    problema de segurança, não uma conveniência.
    """
    demo_users = (
        ("brm@brm.com.br", Role.SUPER_ADMIN, "super-admin-token-demo"),
        ("socio@escritorio.com.br", Role.ADMIN, "admin-token-demo"),
        ("estagiario@escritorio.com.br", Role.VIEWER, "viewer-token-demo"),
    )
    for email, role, token in demo_users:
        if store.get_by_email(email) is None:
            store.create_user(email, role, api_token=token)


if __name__ == "__main__":
    # uv run python -m mcp_engine.core.user_store
    _store = SQLUserStore(
        Path(os.getenv("CONFIG_ROOT", "clients")), os.getenv("CLIENT_ID", "example")
    )
    seed_demo_users(_store)
    print(f"seeded demo users for client {os.getenv('CLIENT_ID', 'example')!r}")
