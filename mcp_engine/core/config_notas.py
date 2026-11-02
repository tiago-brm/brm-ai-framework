"""Notas de configuração (spec 030): tipos, validação, snapshots e projeções.

O caderno é a interface e o motor é a autoridade. Uma nota de configuração
traz prosa para humanos e um bloco ```brm-config``` em YAML. Só o bloco, e só
na versão aprovada guardada em `state.db`, tem efeito. A prosa nunca é
interpretada em tempo de execução.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import frontmatter as fm
import yaml
from json_logic import jsonLogic
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mcp_engine.core.acoes import RegistroAcoes
from mcp_engine.core.fatos import ProvedoresFatos
from mcp_engine.core.models import Rule, RuleSet, SkillDefinition
from mcp_engine.core.state import ConfigVersaoRow, StateStore
from mcp_engine.modules import ModuleError, resolve_skills

OPERACAO_ATIVAR = "ativar_configuracao"
TAMANHO_MAXIMO = 64 * 1024
PASTAS_IGNORADAS = frozenset(
    {"sistema", "relatorios", "memoria", "_modelos", "config_drafts", "library"}
)
RESERVADOS = frozenset({"memoria", "relatorio", "sistema", "modelo"})

_NOME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_INICIO_BLOCO = re.compile(r"^[ \t]*(?:```|~~~)[ \t]*brm-config", re.M)
_BLOCO = re.compile(r"^```brm-config[ \t]*\r?\n(.*?)\r?\n```[ \t]*$", re.S | re.M)
_EFEITO_RANK = {"deny": 0, "require_approval": 1, "allow": 2}
_ESQUEMAS_PROIBIDOS = ("file:", "javascript:", "data:", "ftp:", "gopher:")


class ConfigNotaErro(Exception):
    pass


class CompiladoDivergente(ConfigNotaErro):
    pass


def id_valido(valor: str) -> bool:
    return bool(_ID.match(valor))


def sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VersaoAtiva:
    caminho: str
    tipo: str
    config_id: str
    versao: int
    dados: Mapping[str, Any]
    hash: str
    proposto_por: str
    aprovado_por: str
    aprovado_em: datetime
    operacao_id: str
    vigente_de: str | None
    ordem: int


@dataclass
class ContextoValidacao:
    client_id: str
    allowlist: tuple[str, ...] = ()
    modulos: Mapping[str, Any] = field(default_factory=dict)
    handlers: Any = None
    reservados: frozenset[str] = frozenset()
    ativos: Mapping[tuple[str, str], str] = field(default_factory=dict)
    skills: Mapping[str, SkillDefinition] | None = None
    provedores: ProvedoresFatos | None = None


@dataclass(frozen=True)
class TipoConfig:
    """Tipo de nota de configuração.

    `materializar` recebe sempre o conjunto completo de versões ativas do tipo e
    deve deixar o estado do módulo igual a ele (idempotente e determinístico);
    é chamado na ativação e na reconstrução. `schema` precisa ser estrito
    (`extra="forbid"`).
    """

    nome: str
    aprovacao: bool = True
    schema: type[BaseModel] | None = None
    validar: (
        Callable[[dict[str, Any], ContextoValidacao], tuple[dict[str, Any] | None, list[str]]]
        | None
    ) = None
    lint: Callable[[dict[str, Any], ContextoValidacao], list[str]] | None = None
    materializar: Callable[[tuple[VersaoAtiva, ...]], None] | None = None
    arquivo: str | None = None
    projetar: Callable[[str, Sequence[VersaoAtiva]], str] | None = None
    resumo: Callable[[dict[str, Any]], str] | None = None
    aprovador: str = "admin"
    conhecimento: bool = False


class Extensoes:
    """Registro por cliente de tipos de configuração, geradores de relatório, provedores de
    fatos, tipos de ação da Central de Ações e verificações de saúde dos módulos."""

    def __init__(self) -> None:
        self.tipos: dict[str, TipoConfig] = {}
        self.geradores: dict[str, Any] = {}
        self.provedores = ProvedoresFatos()
        self.acoes = RegistroAcoes()
        self.verificacoes: dict[str, Callable[[], dict[str, Any]]] = {}
        for tipo in _tipos_nativos():
            self.tipos[tipo.nome] = tipo

    def registrar_tipo(self, tipo: TipoConfig, *, substituir: bool = False) -> None:
        if not _NOME.match(tipo.nome) or tipo.nome in RESERVADOS:
            raise ConfigNotaErro(f"nome de tipo inválido ou reservado: {tipo.nome!r}")
        if tipo.aprovacao and tipo.schema is None and tipo.validar is None:
            raise ConfigNotaErro(f"tipo {tipo.nome!r} com aprovação exige esquema")
        if tipo.schema is not None and tipo.schema.model_config.get("extra") != "forbid":
            raise ConfigNotaErro(f"esquema de {tipo.nome!r} precisa de extra='forbid'")
        existente = self.tipos.get(tipo.nome)
        if existente is not None and not (substituir and existente.nome in _NATIVOS):
            raise ConfigNotaErro(f"tipo já registrado: {tipo.nome!r}")
        self.tipos[tipo.nome] = tipo

    def registrar_gerador(self, gerador: Any) -> None:
        if gerador.nome in self.geradores:
            raise ConfigNotaErro(f"gerador já registrado: {gerador.nome!r}")
        self.geradores[gerador.nome] = gerador


@dataclass(frozen=True)
class Nota:
    frontmatter: dict[str, Any]
    prosa: str
    blocos: tuple[str, ...]
    inicios: int


def parse_nota(texto: str) -> Nota:
    try:
        post = fm.loads(texto)
    except Exception as exc:
        raise ConfigNotaErro(f"frontmatter inválido: {exc}") from exc
    corpo = post.content
    return Nota(
        frontmatter=dict(post.metadata),
        prosa=corpo,
        blocos=tuple(_BLOCO.findall(corpo)),
        inicios=len(_INICIO_BLOCO.findall(corpo)),
    )


def _yaml_seguro(texto: str) -> Any:
    for evento in yaml.parse(texto, Loader=yaml.SafeLoader):
        if isinstance(evento, yaml.AliasEvent) or getattr(evento, "anchor", None):
            raise ConfigNotaErro("âncoras e aliases YAML não são permitidos no bloco")
    return yaml.safe_load(texto)


def _strings(valor: Any) -> Iterable[str]:
    if isinstance(valor, str):
        yield valor
    elif isinstance(valor, Mapping):
        for k, v in valor.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(valor, (list, tuple)):
        for item in valor:
            yield from _strings(item)


def _host_permitido(host: str, allowlist: Iterable[str]) -> bool:
    return any(fnmatch(host.lower(), p.lower()) for p in allowlist)


def conferir_valores(dados: Any, allowlist: Iterable[str]) -> list[str]:
    erros: list[str] = []
    lista = tuple(allowlist)
    for texto in _strings(dados):
        baixo = texto.strip().lower()
        if baixo.startswith(_ESQUEMAS_PROIBIDOS):
            erros.append(f"esquema de URL não permitido: {texto[:60]!r}")
        if "../" in texto or "..\\" in texto:
            erros.append(f"caminho relativo não permitido: {texto[:60]!r}")
        if baixo.startswith(("http://", "https://")):
            host = urlparse(re.sub(r"\{[^}]*\}", "x", texto)).hostname or ""
            if not _host_permitido(host, lista):
                erros.append(f"host fora da allowlist do cliente: {host or texto[:60]!r}")
    return erros


def _erros_pydantic(exc: ValidationError) -> list[str]:
    out = []
    for err in exc.errors():
        local = ".".join(str(p) for p in err["loc"]) or "(raiz)"
        if err["type"] == "extra_forbidden":
            out.append(f"campo desconhecido: {local}")
        else:
            out.append(f"{local}: {err['msg']}")
    return out


def _validar_regra_lint(dados: dict[str, Any], ctx: ContextoValidacao) -> list[str]:
    try:
        jsonLogic(dados["condition"], {})
    except Exception as exc:
        return [f"condição não é JSONLogic válido: {exc}"]
    return []


def _validar_skill(
    dados: dict[str, Any], ctx: ContextoValidacao
) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        if "usar" in dados:
            resolvida = resolve_skills([dados], ctx.modulos, ctx.allowlist)[0]
            SkillDefinition.model_validate(resolvida)
            canon = json.loads(json.dumps(dados, sort_keys=True))
            nome = str(dados["usar"]).partition(".")[2]
            return {**canon, "name": nome}, []
        skill = SkillDefinition.model_validate(dados)
    except ModuleError as exc:
        return None, [str(exc)]
    except ValidationError as exc:
        return None, _erros_pydantic(exc)
    erros = []
    for dominio in skill.allowed_domains:
        if not _host_permitido(dominio, ctx.allowlist):
            erros.append(f"domínio {dominio!r} fora da allowlist do cliente")
    if skill.executor.type == "native" and ctx.handlers is not None:
        if skill.executor.handler not in ctx.handlers.names():
            erros.append(f"handler não registrado: {skill.executor.handler!r}")
    if skill.name in ctx.reservados:
        erros.append(f"nome de skill reservado por uma tool do núcleo: {skill.name!r}")
    if ctx.provedores is not None:
        erros += ctx.provedores.validar_uso(
            skill.fatos_de_modulo, skill.parameters, skill.derived_facts
        )
    return skill.model_dump(mode="json"), erros


def _dump_yaml(dados: Any) -> str:
    return yaml.safe_dump(dados, sort_keys=True, allow_unicode=True, default_flow_style=False)


def _projetar_regras(client_id: str, versoes: Sequence[VersaoAtiva]) -> str:
    ordenadas = sorted(
        versoes,
        key=lambda v: (v.ordem, _EFEITO_RANK.get(v.dados["effect"], 9), v.config_id),
    )
    ruleset = RuleSet(
        schema_version=1,
        client_id=client_id,
        rules=tuple(Rule.model_validate(v.dados) for v in ordenadas),
    )
    return _dump_yaml(ruleset.model_dump(mode="json"))


def _projetar_skills(client_id: str, versoes: Sequence[VersaoAtiva]) -> str:
    skills = [dict(v.dados) for v in sorted(versoes, key=lambda v: v.config_id)]
    for item in skills:
        if "usar" in item:
            item.pop("name", None)
    return _dump_yaml({"schema_version": 1, "client_id": client_id, "skills": skills})


def _resumo_regra(d: dict[str, Any]) -> str:
    condicao = json.dumps(d["condition"], ensure_ascii=False)
    return f"Regra {d['id']}: efeito {d['effect']} quando {condicao}"


def _resumo_skill(d: dict[str, Any]) -> str:
    if "usar" in d:
        autonomia = d.get("autonomia", "padrão")
        return f"Skill {d['name']} adotada do módulo ({d['usar']}), autonomia {autonomia}"
    return (
        f"Skill {d['name']}: {d['description']} (autonomia {d['autonomia']}, "
        f"executor {d['executor']['type']}, domínios {', '.join(d['allowed_domains']) or 'nenhum'})"
    )


def _tipos_nativos() -> list[TipoConfig]:
    from mcp_engine.core.gatilhos_tipo import tipo_gatilho

    conhecimento = [
        TipoConfig(nome=n, aprovacao=False, conhecimento=True)
        for n in ("politica", "procedimento", "glossario")
    ]
    return [
        *conhecimento,
        TipoConfig(
            nome="regra",
            schema=Rule,
            lint=_validar_regra_lint,
            arquivo="rules.yaml",
            projetar=_projetar_regras,
            resumo=_resumo_regra,
        ),
        TipoConfig(
            nome="skill",
            validar=_validar_skill,
            arquivo="skills.yaml",
            projetar=_projetar_skills,
            resumo=_resumo_skill,
        ),
        tipo_gatilho(),
    ]


_NATIVOS = frozenset({"politica", "procedimento", "glossario", "regra", "skill", "gatilho"})


@dataclass(frozen=True)
class Validacao:
    caminho: str
    ok: bool
    tipo: str | None = None
    config_id: str | None = None
    hash: str = ""
    dados: dict[str, Any] | None = None
    erros: tuple[str, ...] = ()
    avisos: tuple[str, ...] = ()
    vigente_de: str | None = None
    ordem: int = 100
    rascunho: bool = False
    frontmatter: Mapping[str, Any] = field(default_factory=dict)


def _falha(caminho: str, texto: str, erros: list[str], **extra: Any) -> Validacao:
    return Validacao(caminho=caminho, ok=False, hash=sha256(texto), erros=tuple(erros), **extra)


def validar_nota(
    caminho: str, texto: str, extensoes: Extensoes, ctx: ContextoValidacao
) -> Validacao:
    if len(texto.encode("utf-8")) > TAMANHO_MAXIMO:
        return _falha(caminho, texto, [f"nota maior que {TAMANHO_MAXIMO // 1024} KB"])
    try:
        nota = parse_nota(texto)
    except ConfigNotaErro as exc:
        return _falha(caminho, texto, [str(exc)])
    cab = nota.frontmatter
    tipo_nome = str(cab.get("tipo", ""))
    tipo = extensoes.tipos.get(tipo_nome)
    if tipo is None:
        return _falha(caminho, texto, [f"tipo desconhecido: {tipo_nome!r}"], frontmatter=cab)
    base = {"tipo": tipo_nome, "frontmatter": cab}
    rascunho = str(cab.get("status", "")).lower() == "rascunho"
    if rascunho:
        return Validacao(
            caminho=caminho, ok=False, hash=sha256(texto), rascunho=True,
            erros=("rascunho: remova 'status: rascunho' depois de conferir",), **base,
        )  # fmt: skip
    config_id = cab.get("id")
    ordem = cab.get("ordem", 100)
    vigente = cab.get("vigente_de")
    base["config_id"] = config_id if isinstance(config_id, str) else None
    if tipo.conhecimento:
        return Validacao(caminho=caminho, ok=True, hash=sha256(texto), **base)
    erros: list[str] = []
    if not isinstance(config_id, str) or not _ID.match(config_id):
        erros.append("frontmatter 'id' obrigatório (minúsculas, números, '-' e '_', até 64)")
    if not isinstance(ordem, int) or isinstance(ordem, bool):
        erros.append("frontmatter 'ordem' deve ser inteiro")
        ordem = 100
    if vigente is not None and not isinstance(vigente, (date, str)):
        erros.append("frontmatter 'vigente_de' deve ser uma data")
    if nota.inicios != 1 or len(nota.blocos) != 1:
        erros.append("a nota precisa de exatamente um bloco ```brm-config``` bem formado")
    if erros:
        return _falha(caminho, texto, erros, **base)
    try:
        dados = _yaml_seguro(nota.blocos[0])
    except (yaml.YAMLError, ConfigNotaErro) as exc:
        return _falha(caminho, texto, [f"bloco brm-config inválido: {exc}"], **base)
    if not isinstance(dados, dict):
        return _falha(caminho, texto, ["o bloco brm-config deve ser um mapa YAML"], **base)
    erros = conferir_valores(dados, ctx.allowlist)
    canon: dict[str, Any] | None = None
    if tipo.validar is not None:
        canon, e = tipo.validar(dados, ctx)
        erros += e
    elif tipo.schema is not None:
        try:
            canon = tipo.schema.model_validate(dados).model_dump(mode="json")
        except ValidationError as exc:
            erros += _erros_pydantic(exc)
    if canon is not None and tipo.lint is not None:
        erros += tipo.lint(canon, ctx)
    if canon is not None:
        nome_id = canon.get("id", canon.get("name"))
        if nome_id is not None and nome_id != config_id:
            erros.append(f"o 'id' do frontmatter ({config_id!r}) difere do bloco ({nome_id!r})")
        dono = ctx.ativos.get((tipo_nome, str(config_id)))
        if dono is not None and dono != caminho:
            erros.append(f"id já usado pela nota ativa {dono!r}")
    if erros:
        return _falha(caminho, texto, erros, **base)
    return Validacao(
        caminho=caminho,
        ok=True,
        tipo=tipo_nome,
        config_id=str(config_id),
        hash=sha256(texto),
        dados=canon,
        vigente_de=str(vigente) if vigente is not None else None,
        ordem=ordem,
        frontmatter=cab,
    )


def diff_legivel(antes: Mapping[str, Any] | None, depois: Mapping[str, Any]) -> list[str]:
    def achatar(valor: Any, prefixo: str = "") -> dict[str, str]:
        if isinstance(valor, Mapping):
            out: dict[str, str] = {}
            for k in sorted(valor):
                out.update(achatar(valor[k], f"{prefixo}{k}."))
            return out or {prefixo.rstrip("."): "{}"}
        return {prefixo.rstrip("."): json.dumps(valor, ensure_ascii=False, sort_keys=True)}

    novo = achatar(depois)
    velho = achatar(antes) if antes is not None else {}
    linhas = []
    for chave in sorted(set(novo) | set(velho)):
        if velho.get(chave) == novo.get(chave):
            continue
        if chave not in velho:
            linhas.append(f"+ {chave}: {novo[chave]}")
        elif chave not in novo:
            linhas.append(f"- {chave}: {velho[chave]}")
        else:
            linhas.append(f"~ {chave}: {velho[chave]} -> {novo[chave]}")
    return linhas


def _dir_ignorado(partes: Sequence[str]) -> bool:
    return any(p.lower() in PASTAS_IGNORADAS or p.startswith(".") for p in partes[:-1])


def listar_notas(vault_root: Path) -> list[tuple[str, str]]:
    vault_root = Path(vault_root)
    if not vault_root.is_dir():
        return []
    achadas = []
    for arq in sorted(vault_root.rglob("*.md")):
        rel = arq.relative_to(vault_root)
        if _dir_ignorado(rel.parts) or arq.is_symlink():
            continue
        try:
            if arq.stat().st_size > TAMANHO_MAXIMO * 4:
                continue
            achadas.append((rel.as_posix(), arq.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    return achadas


class SnapshotStore:
    def __init__(self, state: StateStore) -> None:
        self._engine = state.engine

    def ultimas(self) -> list[ConfigVersaoRow]:
        with Session(self._engine) as s:
            maximo = (
                select(ConfigVersaoRow.caminho, func.max(ConfigVersaoRow.versao).label("v"))
                .group_by(ConfigVersaoRow.caminho)
                .subquery()
            )
            stmt = (
                select(ConfigVersaoRow)
                .join(
                    maximo,
                    (ConfigVersaoRow.caminho == maximo.c.caminho)
                    & (ConfigVersaoRow.versao == maximo.c.v),
                )
                .order_by(ConfigVersaoRow.caminho)
            )
            rows = list(s.scalars(stmt))
            for r in rows:
                s.expunge(r)
            return rows

    def historico(self, caminho: str) -> list[ConfigVersaoRow]:
        with Session(self._engine) as s:
            rows = list(
                s.scalars(
                    select(ConfigVersaoRow)
                    .where(ConfigVersaoRow.caminho == caminho)
                    .order_by(ConfigVersaoRow.versao)
                )
            )
            for r in rows:
                s.expunge(r)
            return rows

    def ativa(self, caminho: str) -> ConfigVersaoRow | None:
        h = self.historico(caminho)
        return h[-1] if h else None

    def gravar(
        self,
        *,
        caminho: str,
        tipo: str,
        config_id: str,
        conteudo: str,
        proposto_por: str,
        aprovado_por: str,
        operacao_id: str,
    ) -> ConfigVersaoRow:
        with Session(self._engine) as s:
            atual = s.scalar(
                select(func.max(ConfigVersaoRow.versao)).where(ConfigVersaoRow.caminho == caminho)
            )
            row = ConfigVersaoRow(
                caminho=caminho,
                versao=(atual or 0) + 1,
                tipo=tipo,
                config_id=config_id,
                conteudo=conteudo,
                hash=sha256(conteudo),
                proposto_por=proposto_por,
                aprovado_por=aprovado_por,
                aprovado_em=datetime.now(UTC),
                operacao_id=operacao_id,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            s.expunge(row)
            return row

    def aprovacoes_na_ultima_hora(self, aprovador: str) -> int:
        limite = datetime.now(UTC).timestamp() - 3600
        with Session(self._engine) as s:
            rows = s.scalars(
                select(ConfigVersaoRow.aprovado_em).where(ConfigVersaoRow.aprovado_por == aprovador)
            )
            return sum(1 for t in rows if t.replace(tzinfo=t.tzinfo or UTC).timestamp() >= limite)


def versao_de_snapshot(
    row: ConfigVersaoRow, extensoes: Extensoes, ctx: ContextoValidacao
) -> VersaoAtiva:
    """Reconstrói a versão ativa só a partir do conteúdo aprovado (sem consultar o vault)."""
    sem_dono = ContextoValidacao(
        client_id=ctx.client_id,
        allowlist=ctx.allowlist,
        modulos=ctx.modulos,
        handlers=None,
        reservados=frozenset(),
    )
    v = validar_nota(row.caminho, row.conteudo, extensoes, sem_dono)
    if not v.ok or v.dados is None:
        raise ConfigNotaErro(f"snapshot {row.caminho} v{row.versao} não valida: {v.erros}")
    return VersaoAtiva(
        caminho=row.caminho,
        tipo=row.tipo,
        config_id=row.config_id,
        versao=row.versao,
        dados=v.dados,
        hash=row.hash,
        proposto_por=row.proposto_por,
        aprovado_por=row.aprovado_por,
        aprovado_em=row.aprovado_em,
        operacao_id=row.operacao_id,
        vigente_de=v.vigente_de,
        ordem=v.ordem,
    )


def projecoes(
    client_id: str, extensoes: Extensoes, ativas: Sequence[VersaoAtiva]
) -> dict[str, str]:
    saida: dict[str, str] = {}
    for tipo in extensoes.tipos.values():
        if tipo.arquivo is None or tipo.projetar is None:
            continue
        do_tipo = [v for v in ativas if v.tipo == tipo.nome]
        if do_tipo:
            saida[tipo.arquivo] = tipo.projetar(client_id, do_tipo)
    return saida


def escrever_atomico(destino: Path, texto: str) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_name(destino.name + ".tmp")
    tmp.write_text(texto, encoding="utf-8")
    os.replace(tmp, destino)
