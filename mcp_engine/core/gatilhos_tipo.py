"""Esquema definitivo do bloco `brm-config` do tipo `gatilho` (spec 031).

Um gatilho é dado, nunca código: quando disparar (agenda cron ou evento), condição JSONLogic,
uma skill existente com argumentos modelados, a saída e os limites. Só vale a versão aprovada
pelo mecanismo de notas da spec 030.
"""

from __future__ import annotations

import re
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from json_logic import jsonLogic
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mcp_engine.core import cron
from mcp_engine.core.config_notas import ContextoValidacao, TipoConfig

_EVENTO = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}")
_NOME_VAR = re.compile(r"^[a-z][a-z0-9_]*(\.[a-zA-Z0-9_]+)*$")
_HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_RANK = {"A0": 0, "A1": 1, "A2": 2, "A3": 3}

VARIAVEIS_DO_MOTOR = frozenset(
    {"hoje", "agora", "hora", "dia_semana", "janela", "mes_atual", "mes_anterior"}
)


class _Estrito(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Quando(_Estrito):
    agenda: str | None = None
    evento: str | None = None
    fuso: str = "America/Sao_Paulo"

    @model_validator(mode="after")
    def _um_so(self) -> Quando:
        if (self.agenda is None) == (self.evento is None):
            raise ValueError("'quando' precisa de exatamente um: agenda ou evento")
        if self.agenda is not None:
            try:
                cron.parse(self.agenda)
            except cron.CronErro as exc:
                raise ValueError(f"agenda inválida: {exc}") from None
        if self.evento is not None and not _EVENTO.match(self.evento):
            raise ValueError("evento deve ter a forma dominio.acontecimento (minúsculas)")
        try:
            ZoneInfo(self.fuso)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"fuso horário desconhecido: {self.fuso!r}") from None
        return self


class Acao(_Estrito):
    skill: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_\-]{0,63}$")
    argumentos: dict[str, Any] = Field(default_factory=dict)


class Saida(_Estrito):
    tipo: Literal["alerta", "nota", "email"]
    mensagem: str = Field(min_length=1, max_length=500)
    nivel: Literal["info", "aviso", "critico"] = "aviso"
    assunto: str | None = Field(default=None, max_length=150)
    skill: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_\-]{0,63}$")

    @model_validator(mode="after")
    def _so_email(self) -> Saida:
        if self.tipo != "email" and (self.assunto or self.skill):
            raise ValueError("'assunto' e 'skill' só valem para saída de e-mail")
        return self


class Horario(_Estrito):
    das: str
    ate: str

    @field_validator("das", "ate")
    @classmethod
    def _hora(cls, valor: str) -> str:
        if not _HORA.match(valor):
            raise ValueError('use HH:MM entre aspas, por exemplo "08:00"')
        return valor


class Limites(_Estrito):
    max_por_hora: int = Field(default=4, ge=1, le=60)
    max_profundidade: int = Field(default=3, ge=0, le=5)
    falhas_seguidas: int = Field(default=3, ge=1, le=10)
    rejeicoes_por_hora: int = Field(default=20, ge=1, le=1000)
    atraso_maximo_min: int = Field(default=60, ge=1, le=1440)
    horario: Horario | None = None


class GatilhoBloco(_Estrito):
    id: str
    descricao: str = Field(min_length=1, max_length=300)
    quando: Quando
    fatos_de_modulo: tuple[str, ...] = ()
    condicao: dict[str, Any] | None = None
    acao: Acao
    saida: Saida
    autonomia: Literal["A0", "A1", "A2", "A3"] = "A0"
    papel: Literal["viewer", "admin"] = "viewer"
    permissoes: tuple[str, ...] = Field(min_length=1, max_length=20)
    destinatario: str = Field(min_length=1, max_length=120)
    limites: Limites = Field(default_factory=Limites)

    @model_validator(mode="after")
    def _coerencia(self) -> GatilhoBloco:
        if self.acao.skill not in self.permissoes:
            raise ValueError("a skill da ação precisa estar em 'permissoes'")
        if self.saida.tipo == "email":
            if not _EMAIL.match(self.destinatario):
                raise ValueError("saída por e-mail exige 'destinatario' com um e-mail")
            if (self.saida.skill or "enviar_email") not in self.permissoes:
                raise ValueError("a skill de e-mail precisa estar em 'permissoes'")
        return self


def nome_skill_email(dados: dict[str, Any]) -> str:
    return dados["saida"].get("skill") or "enviar_email"


def _strings(valor: Any) -> list[str]:
    if isinstance(valor, str):
        return [valor]
    if isinstance(valor, dict):
        return [t for k, v in valor.items() for t in (str(k), *_strings(v))]
    if isinstance(valor, (list, tuple)):
        return [t for v in valor for t in _strings(v)]
    return []


def modelos_de(texto: str) -> list[str]:
    return [m.strip() for m in _PLACEHOLDER.findall(texto)]


def _variaveis_jsonlogic(regra: Any) -> list[str]:
    achadas: list[str] = []
    if isinstance(regra, dict):
        for chave, valor in regra.items():
            if chave == "var":
                alvo = valor[0] if isinstance(valor, list) and valor else valor
                achadas.append(str(alvo))
            else:
                achadas += _variaveis_jsonlogic(valor)
    elif isinstance(regra, list):
        for item in regra:
            achadas += _variaveis_jsonlogic(item)
    return achadas


def variaveis_conhecidas(dados: dict[str, Any], ctx: ContextoValidacao) -> set[str] | None:
    """Raízes de variável válidas no contexto do gatilho, ou None sem provedores para conferir."""
    if ctx.provedores is None:
        return None
    conhecidas = set(VARIAVEIS_DO_MOTOR) | set(ctx.provedores.fatos_de(dados["fatos_de_modulo"]))
    if dados["quando"].get("evento"):
        conhecidas.add("evento")
    return conhecidas


def lint(dados: dict[str, Any], ctx: ContextoValidacao) -> list[str]:
    erros: list[str] = []
    if dados.get("condicao") is not None:
        try:
            jsonLogic(dados["condicao"], {})
        except Exception as exc:
            return [f"condição não é JSONLogic válido: {exc}"]
    textos_comuns = _strings(dados["acao"]["argumentos"])
    mensagens = [dados["saida"]["mensagem"], dados["saida"].get("assunto") or ""]
    for texto in textos_comuns + mensagens:
        if texto.count("{{") != texto.count("}}"):
            erros.append(f"modelo mal fechado: {texto[:60]!r}")
    conhecidas = variaveis_conhecidas(dados, ctx)
    nomes_condicao = _variaveis_jsonlogic(dados.get("condicao"))
    usadas = [(n, "condição") for n in nomes_condicao]
    usadas += [(m, "argumento") for t in textos_comuns for m in modelos_de(t)]
    usadas += [(m, "saída") for t in mensagens for m in modelos_de(t)]
    for nome, onde in usadas:
        if not _NOME_VAR.match(nome):
            erros.append(f"variável inválida em {onde}: {nome!r}")
            continue
        raiz = nome.split(".")[0]
        if onde == "saída" and raiz == "resultado":
            continue
        if conhecidas is not None and raiz not in conhecidas:
            erros.append(f"variável desconhecida em {onde}: {nome!r}")
    if ctx.provedores is not None:
        for nome in dados["fatos_de_modulo"]:
            if not ctx.provedores.existe(nome):
                erros.append(f"provedor de fatos desconhecido: {nome!r}")
    if ctx.skills is None:
        return erros
    for nome in dados["permissoes"]:
        if nome not in ctx.skills:
            erros.append(f"permissão para skill inexistente: {nome!r}")
    skill = ctx.skills.get(dados["acao"]["skill"])
    if skill is None:
        erros.append(f"skill inexistente: {dados['acao']['skill']!r}")
    elif _RANK[dados["autonomia"]] < _RANK[skill.autonomia]:
        erros.append(
            f"autonomia declarada {dados['autonomia']} abaixo da skill {skill.name} "
            f"({skill.autonomia}): declare {skill.autonomia} para registrar que gera proposta"
        )
    if dados["saida"]["tipo"] == "email" and nome_skill_email(dados) not in ctx.skills:
        erros.append(f"skill de e-mail inexistente: {nome_skill_email(dados)!r}")
    return erros


def projetar(client_id: str, versoes: Any) -> str:
    from mcp_engine.core.config_notas import _dump_yaml

    itens = [dict(v.dados) for v in sorted(versoes, key=lambda v: v.config_id)]
    return _dump_yaml({"schema_version": 1, "client_id": client_id, "gatilhos": itens})


def resumo(dados: dict[str, Any]) -> str:
    quando = dados["quando"]
    gatilho = f"agenda {quando['agenda']}" if quando.get("agenda") else f"evento {quando['evento']}"
    return (
        f"Gatilho {dados['id']}: {gatilho}; executa {dados['acao']['skill']} como "
        f"sistema:{dados['id']} (papel {dados['papel']}, autonomia {dados['autonomia']}); "
        f"saída {dados['saida']['tipo']} para {dados['destinatario']}; "
        f"até {dados['limites']['max_por_hora']} por hora"
    )


def tipo_gatilho() -> TipoConfig:
    return TipoConfig(
        nome="gatilho",
        schema=GatilhoBloco,
        lint=lint,
        arquivo="gatilhos.yaml",
        projetar=projetar,
        resumo=resumo,
    )
