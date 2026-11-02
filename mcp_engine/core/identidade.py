"""Provedores de identidade (spec 035).

`local` resolve o token do `SQLUserStore` (comportamento anterior). `agenda` valida o JWT
emitido pelo provedor de identidade do produto (Supabase Auth), sem guardar senha nem
sincronizar usuários: o papel vem de um claim e é mapeado pelo `client_config`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatch
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlsplit

import jwt

from mcp_engine.core.auth import Role, UserProfile, get_current_user
from mcp_engine.core.executor import ExecucaoFalhou, SecretStore
from mcp_engine.core.models import IdentidadeAgenda, IdentidadeConfig

if TYPE_CHECKING:
    from mcp_engine.core.user_store import SQLUserStore

logger = logging.getLogger(__name__)


class IdentidadeRecusada(Exception):
    """A credencial é do provedor, mas não vale (assinatura, validade, emissor, papel...)."""

    def __init__(self, provedor: str, motivo: str) -> None:
        super().__init__(f"{provedor}: {motivo}")
        self.provedor = provedor
        self.motivo = motivo


@dataclass(frozen=True)
class Identidade:
    id: str
    perfil: UserProfile
    provedor: str
    metodo: str
    email: str | None = None
    papel_origem: str | None = None
    aal: str | None = None
    amr: tuple[str, ...] = ()
    autenticado_em: datetime | None = None
    tenant: str | None = None

    def autenticacao_recente(self, minutos: int, agora: datetime | None = None) -> bool:
        if self.autenticado_em is None:
            return False
        agora = agora or datetime.now(UTC)
        return agora - self.autenticado_em <= timedelta(minutes=minutos)

    def publica(self) -> dict[str, Any]:
        return {
            "identidade": self.perfil.identity,
            "papel": self.perfil.role.value,
            "papel_origem": self.papel_origem,
            "provedor": self.provedor,
            "metodo": self.metodo,
            "aal": self.aal,
        }


identidade_var: ContextVar[Identidade | None] = ContextVar("identidade", default=None)


def set_identidade(identidade: Identidade) -> Token[Identidade | None]:
    return identidade_var.set(identidade)


def get_identidade() -> Identidade:
    """Identidade completa da requisição; sem uma, deriva do perfil corrente (stdio, testes)."""
    atual = identidade_var.get()
    perfil = get_current_user()
    if atual is not None and atual.perfil == perfil:
        return atual
    return Identidade(
        id=perfil.identity,
        perfil=perfil,
        provedor="local",
        metodo="contexto",
        email=perfil.identity,
    )


class IdentityProvider(Protocol):
    nome: str

    def autenticar(self, credencial: str) -> Identidade | None: ...


class LocalIdentityProvider:
    nome = "local"

    def __init__(self, store: SQLUserStore) -> None:
        self._store = store

    def autenticar(self, credencial: str) -> Identidade | None:
        perfil = self._store.get_by_token(credencial)
        if perfil is None:
            return None
        return Identidade(
            id=perfil.identity,
            perfil=perfil,
            provedor=self.nome,
            metodo="token",
            email=perfil.identity,
        )


def _dig(dados: Any, caminho: str) -> Any:
    for parte in caminho.split("."):
        if not isinstance(dados, dict) or parte not in dados:
            return None
        dados = dados[parte]
    return dados


def _instante(valor: Any) -> datetime | None:
    if isinstance(valor, bool) or not isinstance(valor, int | float):
        return None
    try:
        return datetime.fromtimestamp(valor, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _amr(bruto: Any) -> tuple[tuple[str, ...], datetime | None]:
    metodos: list[str] = []
    instantes: list[datetime] = []
    if isinstance(bruto, list):
        for item in bruto:
            if isinstance(item, str):
                metodos.append(item)
            elif isinstance(item, dict):
                if isinstance(item.get("method"), str):
                    metodos.append(item["method"])
                quando = _instante(item.get("timestamp"))
                if quando is not None:
                    instantes.append(quando)
    return tuple(metodos), max(instantes) if instantes else None


class AgendaIdentityProvider:
    """Valida JWT HS256 (segredo no SecretStore) ou RS256/ES256 (JWKS)."""

    nome = "agenda"

    def __init__(
        self,
        config: IdentidadeAgenda,
        secrets: SecretStore,
        *,
        domain_allowlist: Iterable[str] = (),
        jwks_client: Any | None = None,
    ) -> None:
        self._config = config
        self._secrets = secrets
        self._jwks = jwks_client
        if config.jwks_url and jwks_client is None:
            host = (urlsplit(config.jwks_url).hostname or "").lower()
            if not any(fnmatch(host, p.lower()) for p in domain_allowlist):
                raise ValueError("o host de jwks_url precisa estar na domain_allowlist")
            self._jwks = jwt.PyJWKClient(config.jwks_url, cache_keys=True, lifespan=300, timeout=5)

    def _chave(self, token: str, alg: str) -> Any:
        if alg == "HS256":
            try:
                return self._secrets.get(self._config.segredo or "")
            except ExecucaoFalhou:
                raise IdentidadeRecusada(self.nome, "segredo do JWT não configurado") from None
        if self._jwks is None:
            raise IdentidadeRecusada(self.nome, "JWKS não configurado")
        try:
            return self._jwks.get_signing_key_from_jwt(token).key
        except jwt.PyJWTError as exc:
            raise IdentidadeRecusada(
                self.nome, f"chave pública indisponível ({type(exc).__name__})"
            )

    def autenticar(self, credencial: str) -> Identidade | None:
        if credencial.count(".") != 2:
            return None
        try:
            cabecalho = jwt.get_unverified_header(credencial)
        except jwt.PyJWTError:
            return None
        cfg = self._config
        alg = cabecalho.get("alg")
        if alg not in cfg.algoritmos:
            raise IdentidadeRecusada(self.nome, f"algoritmo não aceito: {alg!r}")
        exigidos = ["exp", "sub"]
        if cfg.emissor:
            exigidos.append("iss")
        if cfg.audiencia:
            exigidos.append("aud")
        try:
            claims = jwt.decode(
                credencial,
                self._chave(credencial, alg),
                algorithms=[alg],
                audience=cfg.audiencia,
                issuer=cfg.emissor,
                leeway=cfg.folga_s,
                options={"require": exigidos, "verify_aud": bool(cfg.audiencia)},
            )
        except jwt.ExpiredSignatureError:
            raise IdentidadeRecusada(self.nome, "token expirado") from None
        except jwt.InvalidIssuerError:
            raise IdentidadeRecusada(self.nome, "emissor inválido") from None
        except jwt.InvalidAudienceError:
            raise IdentidadeRecusada(self.nome, "audiência inválida") from None
        except jwt.PyJWTError as exc:
            raise IdentidadeRecusada(self.nome, f"token inválido ({type(exc).__name__})") from None

        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub.strip():
            raise IdentidadeRecusada(self.nome, "sub ausente")
        if cfg.claim_tenant and str(_dig(claims, cfg.claim_tenant)) != cfg.tenant_id:
            raise IdentidadeRecusada(self.nome, "usuário de outro estabelecimento")
        bruto = _dig(claims, cfg.claim_papel)
        papel_origem = str(bruto).strip().upper() if isinstance(bruto, str) else None
        if not papel_origem:
            raise IdentidadeRecusada(self.nome, "papel ausente no token")
        papel = cfg.papeis.get(papel_origem)
        if papel is None:
            raise IdentidadeRecusada(self.nome, f"papel sem acesso: {papel_origem}")

        email = claims.get("email") if isinstance(claims.get("email"), str) else None
        email = email.strip().lower() if email else None
        metodos, ultimo_login = _amr(claims.get("amr"))
        autenticado_em = (
            _instante(claims.get("auth_time")) or ultimo_login or _instante(claims.get("iat"))
        )
        aal = claims.get("aal") if isinstance(claims.get("aal"), str) else None
        return Identidade(
            id=sub,
            perfil=UserProfile(identity=email or f"agenda:{sub}", role=Role(papel)),
            provedor=self.nome,
            metodo="jwt",
            email=email,
            papel_origem=papel_origem,
            aal=aal,
            amr=metodos,
            autenticado_em=autenticado_em,
            tenant=str(_dig(claims, cfg.claim_tenant)) if cfg.claim_tenant else None,
        )


@dataclass
class ResolvedorIdentidade:
    provedores: Sequence[IdentityProvider]
    auditar_recusa: Callable[[str, str], None] | None = None
    nomes: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        self.nomes = tuple(p.nome for p in self.provedores)

    def autenticar(self, credencial: str) -> Identidade | None:
        for provedor in self.provedores:
            try:
                identidade = provedor.autenticar(credencial)
            except IdentidadeRecusada as exc:
                logger.info("credencial recusada (%s): %s", exc.provedor, exc.motivo)
                if self.auditar_recusa is not None:
                    self.auditar_recusa(exc.provedor, exc.motivo)
                return None
            if identidade is not None:
                return identidade
        return None

    def resolver(self, authorization_header: str | None) -> Identidade | None:
        if not authorization_header:
            return None
        esquema, _, credencial = authorization_header.partition(" ")
        credencial = credencial.strip()
        if esquema.lower() != "bearer" or not credencial:
            return None
        return self.autenticar(credencial)


def construir_resolvedor(
    config: IdentidadeConfig,
    store: SQLUserStore,
    secrets: SecretStore,
    *,
    domain_allowlist: Iterable[str] = (),
    auditar_recusa: Callable[[str, str], None] | None = None,
    jwks_client: Any | None = None,
) -> ResolvedorIdentidade:
    provedores: list[IdentityProvider] = []
    for nome in config.provedores:
        if nome == "local":
            provedores.append(LocalIdentityProvider(store))
        elif nome == "agenda" and config.agenda is not None:
            provedores.append(
                AgendaIdentityProvider(
                    config.agenda,
                    secrets,
                    domain_allowlist=domain_allowlist,
                    jwks_client=jwks_client,
                )
            )
    return ResolvedorIdentidade(provedores, auditar_recusa)
