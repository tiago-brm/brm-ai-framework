"""Executor de skills (spec 021): handlers nativos, conectores HTTP e segredos.

O executor só aceita uma `ChamadaAutorizada`, que o pipeline do motor emite
depois de validação, DLP de entrada, fatos derivados e regras. Não existe
caminho de código que chame um handler ou um serviço externo sem ela.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from mcp_engine.core.models import HttpMethod, SkillDefinition

EVENTOS_CHAVE = "_eventos"
_SELO = object()
_SECRET_NAME = re.compile(r"[A-Za-z0-9_\-]+")
_SECRET_REF = re.compile(r"\$\{secret:([A-Za-z0-9_\-]+)\}")


class ExecucaoFalhou(Exception):
    """Falha de execução com mensagem já saneada (sem URL, segredo nem corpo)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SecretNotFound(ExecucaoFalhou):
    def __init__(self, name: str) -> None:
        super().__init__(f"segredo não configurado: {name}")


@dataclass(frozen=True)
class ChamadaAutorizada:
    skill: SkillDefinition
    arguments: dict[str, Any]
    actor: str
    role: str
    trace_id: str | None
    idempotency_key: str | None = None
    _selo: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._selo is not _SELO:
            raise TypeError("ChamadaAutorizada só pode ser emitida pelo pipeline do motor")


def emitir_chamada(
    skill: SkillDefinition,
    arguments: dict[str, Any],
    actor: str,
    role: str,
    trace_id: str | None,
    idempotency_key: str | None = None,
) -> ChamadaAutorizada:
    return ChamadaAutorizada(
        skill=skill,
        arguments=dict(arguments),
        actor=actor,
        role=role,
        trace_id=trace_id,
        idempotency_key=idempotency_key,
        _selo=_SELO,
    )


@dataclass(frozen=True)
class HandlerContext:
    client_id: str
    actor: str
    role: str
    trace_id: str | None
    data_root: Path
    idempotency_key: str | None = None
    confirmado_por: str | None = None
    aprovado_por: str | None = None


Handler = Callable[[HandlerContext, dict[str, Any]], dict[str, Any]]


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, name: str, handler: Handler | None = None) -> Any:
        def _add(fn: Handler) -> Handler:
            if name in self._handlers:
                raise ValueError(f"handler já registrado: {name}")
            self._handlers[name] = fn
            return fn

        return _add(handler) if handler is not None else _add

    def get(self, name: str) -> Handler:
        try:
            return self._handlers[name]
        except KeyError:
            raise ExecucaoFalhou(f"handler não registrado: {name}") from None

    def names(self) -> list[str]:
        return sorted(self._handlers)


class SecretStore:
    """Segredos por variável de ambiente ou arquivo em `BRM_SECRETS_DIR` (padrão
    `/run/secrets`, o formato de Docker secrets).

    Com `client_id`, procura primeiro o segredo do cliente
    (`BRM_SECRET_<CLIENTE>_<NOME>` e `<dir>/<cliente>/<nome>`) e só depois o global
    (`BRM_SECRET_<NOME>` e `<dir>/<nome>`)."""

    def __init__(self, secrets_dir: Path | None = None, client_id: str | None = None) -> None:
        self._dir = Path(secrets_dir or os.getenv("BRM_SECRETS_DIR", "/run/secrets"))
        if client_id is not None and not _SECRET_NAME.fullmatch(client_id):
            raise ValueError(f"client_id inválido para segredos: {client_id!r}")
        self._client_id = client_id

    def for_client(self, client_id: str) -> SecretStore:
        return SecretStore(self._dir, client_id)

    @staticmethod
    def _env_key(*parts: str) -> str:
        return "BRM_SECRET_" + "_".join(p.upper().replace("-", "_") for p in parts)

    def get(self, name: str) -> str:
        if not _SECRET_NAME.fullmatch(name):
            raise SecretNotFound(name)
        if self._client_id:
            env = os.getenv(self._env_key(self._client_id, name))
            if env:
                return env
            scoped = self._dir / self._client_id / name
            if scoped.is_file():
                return scoped.read_text().strip()
        env = os.getenv(self._env_key(name))
        if env:
            return env
        path = self._dir / name
        if path.is_file():
            return path.read_text().strip()
        raise SecretNotFound(name)

    def resolve(self, text: str) -> str:
        return _SECRET_REF.sub(lambda m: self.get(m.group(1)), text)


def run_native(
    chamada: ChamadaAutorizada, registry: HandlerRegistry, ctx: HandlerContext
) -> dict[str, Any]:
    handler = registry.get(chamada.skill.executor.handler or "")
    result = handler(ctx, chamada.arguments)
    if not isinstance(result, dict):
        raise ExecucaoFalhou("handler devolveu resultado fora do contrato")
    return result


def _dig(data: Any, path: str) -> Any:
    for part in path.removeprefix("$.").split("."):
        if isinstance(data, dict) and part in data:
            data = data[part]
        else:
            return None
    return data


def run_http(
    chamada: ChamadaAutorizada,
    url: str,
    secrets: SecretStore,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    skill = chamada.skill
    cfg = skill.executor
    assert skill.method is not None
    headers = {k: secrets.resolve(v) for k, v in cfg.headers.items()}
    url = secrets.resolve(url)
    method = skill.method

    request_kwargs: dict[str, Any] = {"headers": headers}
    if method in (HttpMethod.GET, HttpMethod.DELETE):
        template = skill.url_template or ""
        request_kwargs["params"] = {
            k: v for k, v in chamada.arguments.items() if f"{{{k}}}" not in template
        }
    else:
        fixo = {k: secrets.resolve(v) for k, v in cfg.corpo_fixo.items()}
        request_kwargs["json"] = {**chamada.arguments, **fixo}
    if chamada.idempotency_key:
        headers["Idempotency-Key"] = chamada.idempotency_key

    owns_client = client is None
    http = client or httpx.Client(timeout=cfg.timeout_s, follow_redirects=False)
    attempts = 3 if cfg.idempotente else 1
    try:
        last: str = "falha de rede"
        for _ in range(attempts):
            try:
                response = http.request(method.value, url, **request_kwargs)
                break
            except httpx.TransportError as exc:
                last = type(exc).__name__
        else:
            raise ExecucaoFalhou(f"serviço externo indisponível ({last})")
    finally:
        if owns_client:
            http.close()

    if len(response.content) > cfg.max_resposta_bytes:
        raise ExecucaoFalhou("resposta do serviço acima do limite")
    if response.status_code >= 400:
        raise ExecucaoFalhou(f"serviço externo respondeu {response.status_code}")

    result: dict[str, Any] = {"status": response.status_code}
    if cfg.mapeamento_resposta:
        try:
            payload = response.json()
        except ValueError:
            raise ExecucaoFalhou("resposta fora do contrato") from None
        result["dados"] = {k: _dig(payload, p) for k, p in cfg.mapeamento_resposta.items()}
    else:
        result["corpo"] = response.text
    return result
