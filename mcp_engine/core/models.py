from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuleEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class Rule(_StrictModel):
    id: str
    description: str
    condition: dict[str, Any]
    effect: RuleEffect
    message: str | None = None
    next_step: str | None = None
    aprovador: Literal["admin", "super_admin"] | None = None


class RuleSet(_StrictModel):
    schema_version: Literal[1]
    client_id: str
    rules: tuple[Rule, ...] = ()


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class DerivedFact(_StrictModel):
    """Fato calculado pelo motor a partir do vault, nunca informado pelo chamador.

    `vault_match` mapeia chaves do frontmatter para um literal ou `$argumento`.
    `transform`: `value` devolve o campo como está; `equals_actor` compara o
    campo com a identidade logada; `in` testa se o campo está em `values`.
    """

    vault_match: dict[str, str]
    field: str
    transform: Literal["value", "equals_actor", "in"] = "value"
    values: tuple[str, ...] = ()
    if_true: Any = True
    if_false: Any = False
    if_missing: Any = None


class VaultRead(_StrictModel):
    """Leitura do vault mediada por uma skill, restrita a certos tipos de nota."""

    types: tuple[str, ...] = Field(min_length=1)
    query_from: str
    limit: int = 3


class VaultPolicy(_StrictModel):
    id: str
    when: dict[str, Any]
    effect: Literal["allow", "metadata_only", "deny"]
    expose: tuple[str, ...] = ()
    audit: str | None = None


class VaultAccess(_StrictModel):
    schema_version: Literal[1]
    client_id: str
    default: Literal["allow", "deny"] = "deny"
    write_roles: tuple[str, ...] = ("admin", "super_admin")
    vinculo_campo: str = "responsavel"
    identificador_campo: str = "identificador"
    write_denied_next_step: str = "Peça a um administrador do cliente."
    policies: tuple[VaultPolicy, ...] = ()


class SkillExecutor(_StrictModel):
    """Como uma skill é executada depois de aprovada pelas regras (spec 021).

    `mock` é o padrão e mantém o comportamento das demos. `native` chama um
    handler Python registrado por um módulo. `http` chama um serviço externo
    pela allowlist do cliente.
    """

    type: Literal["mock", "native", "http"] = "mock"
    handler: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_s: float = 10.0
    idempotente: bool = False
    max_resposta_bytes: int = 1_000_000
    mapeamento_resposta: dict[str, str] = Field(default_factory=dict)
    corpo_fixo: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _native_needs_handler(self) -> SkillExecutor:
        if self.type == "native" and not self.handler:
            raise ValueError("executor native exige 'handler'")
        return self


class SkillDefinition(_StrictModel):
    name: str
    description: str
    method: HttpMethod | None = None
    url_template: str | None = None
    allowed_domains: tuple[str, ...] = ()
    parameters: dict[str, Any] = Field(default_factory=dict)
    derived_facts: dict[str, DerivedFact] = Field(default_factory=dict)
    examples: tuple[str, ...] = ()
    vault_read: VaultRead | None = None
    timeout_seconds: float = 30.0
    executor: SkillExecutor = Field(default_factory=SkillExecutor)
    autonomia: Literal["A0", "A1", "A2", "A3"] = "A0"
    a1_condicao: dict[str, Any] | None = None
    irreversivel: bool = False
    resumo: str | None = None
    aprovador: Literal["admin", "super_admin"] = "admin"
    fatos_de_modulo: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _autonomia_rules(self) -> SkillDefinition:
        if self.irreversivel and self.autonomia in ("A0", "A1"):
            raise ValueError("skill irreversível não pode ter autonomia A0 ou A1")
        if self.a1_condicao is not None and self.autonomia != "A1":
            raise ValueError("a1_condicao só vale para autonomia A1")
        return self

    @model_validator(mode="after")
    def _network_fields(self) -> SkillDefinition:
        if self.executor.type != "native":
            if self.method is None or not self.url_template or not self.allowed_domains:
                raise ValueError(
                    "skills mock/http exigem method, url_template e ao menos um allowed_domains"
                )
        return self


class SkillSet(_StrictModel):
    schema_version: Literal[1]
    client_id: str
    skills: tuple[SkillDefinition, ...] = ()


class AuditDecision(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    PENDING_APPROVAL = "pending_approval"


class AuditEvent(_StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    client_id: str
    occurred_at: datetime
    actor: str
    action: str
    decision: AuditDecision
    rule_id: str | None = None
    payload_digest: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    user_email: str | None = None
    role: str | None = None
    trace_id: str | None = None


class RuleDecision(_StrictModel):
    effect: RuleEffect
    rule_id: str | None = None
    message: str | None = None
    next_step: str | None = None
    aprovador: Literal["admin", "super_admin"] | None = None


class DLPPattern(_StrictModel):
    pattern: str
    label: str
    action: Literal["mask", "deny"]


class ClientConfig(_StrictModel):
    client_id: str
    context: str | None = None
    simulador: bool = True
    domain_allowlist: tuple[str, ...] = Field(min_length=1)
    block_private_ip: bool = True
    dlp_patterns: tuple[DLPPattern, ...] = ()
    vault_root: str | None = None
    vault_provider: Literal["obsidian", "notion"] = "obsidian"
    notion_token: str | None = None
    notion_root_page_id: str | None = None
    rbac_default: Literal["allow", "deny"] = "deny"
    cors_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    )
    cors_permitir_qualquer: bool = False
    operacao_ttl_horas: int = 24
    modules: tuple[str, ...] = ()
    modulos_privados: tuple[str, ...] = ()
    config_via_notas: bool = False
    config_aprovacoes_por_hora: int = Field(default=30, ge=1)
    gatilhos_ativos: bool = True


class SkillCall(_StrictModel):
    skill_name: str
    url: str
    method: HttpMethod
    actor: str = "unknown"
    request_body: dict[str, Any] = Field(default_factory=dict)
    response_text: str = ""
    response_status: int | None = None


class InterceptDecision(_StrictModel):
    allowed: bool
    reason: str | None = None
    dlp_matches: dict[str, list[str]] = Field(default_factory=dict)


class RequestScan(_StrictModel):
    allowed: bool
    reason: str | None = None
    dlp_matches: dict[str, int] = Field(default_factory=dict)
    arguments: dict[str, Any] = Field(default_factory=dict)


class VaultNote(_StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    note_id: str
    client_id: str
    title: str
    content: str
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    links: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    updated_at: datetime
    content_hash: str


class DemoScenario(_StrictModel):
    id: str
    title: str
    skill: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    profile: str | None = None
    note: str | None = None
    in_matrix: bool = True


class DemoScenarios(_StrictModel):
    schema_version: Literal[1]
    client_id: str
    profiles: dict[str, str] = Field(default_factory=dict)
    scenarios: tuple[DemoScenario, ...] = ()
