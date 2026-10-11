"""Módulos de domínio (spec 027): contrato, registro estático e carregamento.

O núcleo só carrega módulos do catálogo estático (`_KNOWN`), registrados por
`register_module` (testes) ou privados do próprio cliente, em
`clients/<id>/modules/<nome>` e listados em `modulos_privados`. O módulo recebe
apenas o `ModuleHost`, sem acesso ao state.db nem ao user_store.
"""

from __future__ import annotations

import importlib
import importlib.util
import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from mcp_engine.core.executor import Handler, HandlerRegistry, SecretStore

API_VERSION = 1

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

_KNOWN: dict[str, str] = {
    "agenda": "mcp_engine.modules.agenda",
    "datajud": "mcp_engine.modules.datajud",
    "financeiro": "mcp_engine.modules.financeiro",
    "resend": "mcp_engine.modules.resend",
}

_REGISTERED: dict[str, BaseModule] = {}

_USAR_OVERRIDES = {
    "usar",
    "description",
    "autonomia",
    "aprovador",
    "resumo",
    "examples",
    "executor",
}
_EXECUTOR_OVERRIDES = {"timeout_s"}


class ModuleError(Exception):
    pass


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: str
    api_version: int
    compartilhavel: bool
    skills: tuple[str, ...] = ()
    segredos_requeridos: tuple[str, ...] = ()
    dominios_requeridos: tuple[str, ...] = ()


class BaseModule:
    """Módulo declarativo: manifesto `module.yaml`, `skills.yaml` opcional,
    `migrations/*.sql` e `defaults/`. Módulos com lógica herdam e implementam `install`."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        try:
            self.manifest = Manifest.model_validate(
                yaml.safe_load((self.directory / "module.yaml").read_text())
            )
        except Exception as exc:
            raise ModuleError(f"module.yaml inválido em {self.directory.name}: {exc}") from exc
        self.skills_defs = self._load_skills()
        if set(self.skills_defs) != set(self.manifest.skills):
            raise ModuleError(
                f"módulo {self.manifest.name!r}: skills do manifesto divergem do skills.yaml"
            )
        defaults = self.directory / "defaults"
        self.defaults_dir: Path | None = defaults if defaults.is_dir() else None
        self.migrations: tuple[str, ...] = tuple(
            p.read_text() for p in sorted((self.directory / "migrations").glob("*.sql"))
        )

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def api_version(self) -> int:
        return self.manifest.api_version

    def _load_skills(self) -> dict[str, dict[str, Any]]:
        path = self.directory / "skills.yaml"
        if not path.is_file():
            return {}
        data = yaml.safe_load(path.read_text()) or {}
        return {s["name"]: s for s in data.get("skills", [])}

    def install(self, host: ModuleHost) -> None:
        return None

    def module_api(self, host: ModuleHost) -> Any:
        raise ModuleError(f"módulo {self.name!r} não expõe API para outros módulos")


class NamespacedHandlers:
    """Registro de handlers do módulo: todo nome ganha o prefixo `<modulo>.`."""

    def __init__(self, module: str, registry: HandlerRegistry) -> None:
        self._module = module
        self._registry = registry

    def register(self, name: str, handler: Handler | None = None) -> Any:
        if not _NAME.match(name):
            raise ModuleError(f"nome de handler inválido: {name!r}")
        return self._registry.register(f"{self._module}.{name}", handler)


@dataclass
class ModuleHost:
    module: str
    client_id: str
    data_root: Path
    handlers: NamespacedHandlers
    secrets: SecretStore
    add_vault_note: Callable[[str, str, dict[str, Any]], str | None]
    audit: Callable[[str, bool], None]
    tools: dict[str, Callable[..., dict[str, Any]]] = field(default_factory=dict)
    extensoes: Any = None
    modulos: Mapping[str, BaseModule] = field(default_factory=dict)
    leitor_operacoes: Callable[[], dict[str, Any]] | None = None

    def module_api(self, nome: str) -> Any:
        """API pública de outro módulo habilitado no mesmo cliente (nunca o banco dele)."""
        outro = self.modulos.get(nome)
        if outro is None:
            raise ModuleError(f"módulo {nome!r} não está habilitado neste cliente")
        return outro.module_api(self)

    def registrar_tipo_config(self, tipo: Any) -> None:
        """Registra um tipo de nota de configuração (spec 030) com esquema e materialização."""
        if self.extensoes is None:
            raise ModuleError("o núcleo não expõe o caderno de configuração")
        self.extensoes.registrar_tipo(tipo)

    def registrar_gerador_relatorio(self, gerador: Any) -> None:
        """Registra um gerador determinístico de relatório mensal (spec 025)."""
        if self.extensoes is None:
            raise ModuleError("o núcleo não expõe o caderno de configuração")
        self.extensoes.registrar_gerador(gerador)

    def registrar_provedor_fatos(
        self, nome: str, fn: Callable[..., dict[str, Any]], fatos: Iterable[str]
    ) -> None:
        """Registra um provedor de fatos (A1): função somente leitura, sem efeito colateral e
        rápida, que as skills usam por `fatos_de_modulo: [<modulo>.<nome>]`. O chamador nunca
        sobrescreve o valor devolvido. Sem registro do núcleo (host isolado), não faz nada."""
        if self.extensoes is None:
            return
        try:
            self.extensoes.provedores.registrar(f"{self.module}.{nome}", fn, fatos)
        except Exception as exc:
            raise ModuleError(str(exc)) from exc

    def registrar_verificacao_saude(self, nome: str, fn: Callable[[], dict[str, Any]]) -> None:
        """Registra uma verificação de integridade chamada por /health. Deve devolver
        `{"ok": bool, ...}` sem dados sensíveis, porque a rota é pública."""
        if self.extensoes is None:
            return
        chave = f"{self.module}.{nome}"
        if not _NAME.match(nome) or chave in self.extensoes.verificacoes:
            raise ModuleError(f"verificação inválida ou duplicada: {chave!r}")
        self.extensoes.verificacoes[chave] = fn

    def operacoes_agregadas(self) -> dict[str, Any] | None:
        """Contagem de operações por estado e por skill, sem argumentos (somente leitura)."""
        return self.leitor_operacoes() if self.leitor_operacoes is not None else None

    @property
    def db_path(self) -> Path:
        return Path(self.data_root) / self.client_id / f"{self.module}.sqlite"

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def register_tool(self, name: str, fn: Callable[..., dict[str, Any]]) -> None:
        if not _NAME.match(name) or name in self.tools:
            raise ModuleError(f"tool inválida ou duplicada: {name!r}")
        self.tools[name] = fn


def register_module(module: BaseModule) -> None:
    _validate(module)
    if module.name in _KNOWN:
        raise ModuleError(f"nome reservado pelo catálogo: {module.name!r}")
    _REGISTERED[module.name] = module


def unregister_module(name: str) -> None:
    _REGISTERED.pop(name, None)


def _validate(module: BaseModule) -> None:
    if not _NAME.match(module.name):
        raise ModuleError(f"nome de módulo inválido: {module.name!r}")
    if module.api_version != API_VERSION:
        raise ModuleError(
            f"módulo {module.name!r} usa api_version {module.api_version}; "
            f"o núcleo exige {API_VERSION}"
        )


def get_catalog_module(name: str) -> BaseModule:
    if name in _REGISTERED:
        module = _REGISTERED[name]
    else:
        target = _KNOWN.get(name)
        if target is None:
            raise ModuleError(f"módulo desconhecido: {name!r}")
        module = importlib.import_module(target).MODULE
    _validate(module)
    if module.name != name:
        raise ModuleError(f"módulo {name!r} registrado com nome divergente")
    if not module.manifest.compartilhavel:
        raise ModuleError(f"módulo {name!r} não é compartilhável e não pode estar no catálogo")
    return module


def _load_private(config_root: Path, client_id: str, name: str) -> BaseModule:
    if not _NAME.match(name):
        raise ModuleError(f"nome de módulo privado inválido: {name!r}")
    if name in _KNOWN or name in _REGISTERED:
        raise ModuleError(f"módulo privado {name!r} colide com o catálogo")
    base = (Path(config_root) / client_id / "modules").resolve()
    directory = (base / name).resolve()
    if directory.parent != base or not (directory / "module.yaml").is_file():
        raise ModuleError(f"módulo privado não encontrado para {client_id!r}: {name!r}")
    code = directory / "module.py"
    if code.is_file():
        spec = importlib.util.spec_from_file_location(f"brm_privado_{client_id}_{name}", code)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        module = mod.MODULE
        if not isinstance(module, BaseModule):
            raise ModuleError(f"módulo privado {name!r} fora do contrato")
    else:
        module = BaseModule(directory)
    _validate(module)
    if module.name != name:
        raise ModuleError(f"módulo privado {name!r} com nome divergente no manifesto")
    return module


def _dominio_permitido(dominio: str, allowlist: Iterable[str]) -> bool:
    return any(fnmatch(dominio.lower(), p.lower()) for p in allowlist)


def load_for_client(
    client_id: str,
    config_root: Path,
    modules: Iterable[str],
    modulos_privados: Iterable[str],
    domain_allowlist: Iterable[str],
) -> dict[str, BaseModule]:
    allowlist = tuple(domain_allowlist)
    loaded: dict[str, BaseModule] = {}
    for name in modules:
        loaded[name] = get_catalog_module(name)
    for name in modulos_privados:
        if name in loaded:
            raise ModuleError(f"módulo {name!r} listado como catálogo e privado")
        loaded[name] = _load_private(config_root, client_id, name)
    for module in loaded.values():
        faltando = [
            d for d in module.manifest.dominios_requeridos if not _dominio_permitido(d, allowlist)
        ]
        if faltando:
            raise ModuleError(
                f"módulo {module.name!r} requer domínios fora da allowlist: {', '.join(faltando)}"
            )
    return loaded


def segredos_ausentes(modules: Mapping[str, BaseModule], secrets: SecretStore) -> list[str]:
    ausentes = []
    for module in modules.values():
        for nome in module.manifest.segredos_requeridos:
            try:
                secrets.get(nome)
            except Exception:
                ausentes.append(f"{module.name}:{nome}")
    return ausentes


def resolve_skills(
    entries: Iterable[Any],
    modules: Mapping[str, BaseModule],
    domain_allowlist: Iterable[str],
) -> list[Any]:
    allowlist = tuple(domain_allowlist)
    out: list[Any] = []
    for item in entries:
        if not isinstance(item, dict) or "usar" not in item:
            out.append(item)
            continue
        ref = str(item["usar"])
        mod_name, _, skill_name = ref.partition(".")
        module = modules.get(mod_name)
        if module is None:
            raise ModuleError(f"usar {ref!r}: módulo {mod_name!r} não está habilitado")
        base = module.skills_defs.get(skill_name)
        if base is None:
            raise ModuleError(f"usar {ref!r}: skill inexistente no módulo")
        extra = set(item) - _USAR_OVERRIDES
        if extra:
            raise ModuleError(f"usar {ref!r}: sobrescrita não permitida: {sorted(extra)}")
        executor_over = item.get("executor") or {}
        if not isinstance(executor_over, dict) or set(executor_over) - _EXECUTOR_OVERRIDES:
            raise ModuleError(f"usar {ref!r}: só executor.timeout_s pode ser sobrescrito")
        merged = deepcopy(base)
        for key in ("description", "autonomia", "aprovador", "resumo", "examples"):
            if key in item:
                merged[key] = item[key]
        merged["executor"] = {**merged.get("executor", {}), **executor_over}
        for dominio in merged.get("allowed_domains", ()):
            if not _dominio_permitido(dominio, allowlist):
                raise ModuleError(f"usar {ref!r}: domínio {dominio!r} fora da allowlist do cliente")
        out.append(merged)
    nomes = [s["name"] for s in out if isinstance(s, dict) and "name" in s]
    if len(nomes) != len(set(nomes)):
        raise ModuleError("skills com nome duplicado após resolver 'usar'")
    return out


def _migrate(host: ModuleHost, migrations: Iterable[str]) -> None:
    conn = host.connect()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS _migracao (indice INTEGER PRIMARY KEY)")
        conn.commit()
        done = {r[0] for r in conn.execute("SELECT indice FROM _migracao")}
        for i, sql in enumerate(migrations, start=1):
            if i in done:
                continue
            conn.executescript(f"BEGIN;{sql};")
            conn.execute("INSERT INTO _migracao (indice) VALUES (?)", (i,))
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def install_modules(
    modules: Mapping[str, BaseModule],
    *,
    client_id: str,
    data_root: Path,
    registry: HandlerRegistry,
    secrets: SecretStore,
    add_vault_note: Callable[[str, str, dict[str, Any]], str | None],
    audit: Callable[[str, bool], None],
    extensoes: Any = None,
    leitor_operacoes: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Callable[..., dict[str, Any]]]:
    tools: dict[str, Callable[..., dict[str, Any]]] = {}
    for name, module in modules.items():
        host = ModuleHost(
            module=name,
            client_id=client_id,
            data_root=Path(data_root),
            handlers=NamespacedHandlers(name, registry),
            secrets=secrets,
            add_vault_note=add_vault_note,
            audit=audit,
            extensoes=extensoes,
            modulos=modules,
            leitor_operacoes=leitor_operacoes,
        )
        if module.migrations:
            _migrate(host, module.migrations)
        module.install(host)
        for tool_name, fn in host.tools.items():
            if tool_name in tools:
                raise ModuleError(f"tool duplicada entre módulos: {tool_name!r}")
            tools[tool_name] = fn
    return tools
