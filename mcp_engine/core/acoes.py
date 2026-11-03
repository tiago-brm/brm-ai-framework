"""Central de Ações (spec 035): o chat prepara, a pessoa confirma numa página logada.

O núcleo só conhece tipos de ação registrados (`<modulo>.<nome>`); cada tipo valida os
argumentos, gera o resumo, carrega os dados ao vivo do produto e executa. A ação fica guardada
no servidor com o hash dos itens, vale por pouco tempo, é de uso único e executa com a
identidade de quem confirmou (PIN) ou liberou (gestor), nunca de quem criou o link.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import re
import secrets
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from mcp_engine.core.atributos_usuario import (
    PIN_BLOQUEADO,
    PIN_NAO_DEFINIDO,
    AtributosUsuarioStore,
)
from mcp_engine.core.auth import (
    current_user_var,
    ensure_trace_id,
    set_current_user,
    trace_id_var,
)
from mcp_engine.core.executor import ExecucaoFalhou
from mcp_engine.core.identidade import Identidade, identidade_var, set_identidade
from mcp_engine.core.models import CentralAcoesConfig
from mcp_engine.core.operacoes import RANK
from mcp_engine.core.state import AcaoRow, StateStore

logger = logging.getLogger(__name__)

PENDENTE = "pendente"
EM_EXECUCAO = "em_execucao"
CONFIRMADA = "confirmada"
FALHOU = "falhou"
EXPIRADA = "expirada"
CANCELADA = "cancelada"

TRANSICOES: dict[str, frozenset[str]] = {
    PENDENTE: frozenset({EM_EXECUCAO, EXPIRADA, CANCELADA, FALHOU}),
    EM_EXECUCAO: frozenset({CONFIRMADA, FALHOU}),
}
_NOME_TIPO = re.compile(r"^[a-z][a-z0-9_]{0,31}\.[a-z][a-z0-9_]{0,47}$")
_ID_ACAO = re.compile(r"^[0-9a-f]{32}$")


class AcaoErro(Exception):
    """Recusa com código HTTP e mensagem para a pessoa (sem dado sensível)."""

    def __init__(self, status: int, motivo: str, **extra: Any) -> None:
        super().__init__(motivo)
        self.status = status
        self.motivo = motivo
        self.extra = extra


class AcaoRecusada(Exception):
    """Levantada por um tipo na criação: o pedido não vira link."""


@dataclass(frozen=True)
class CampoFaltante:
    nome: str
    rotulo: str
    tipo: str = "texto"
    editavel: bool = False


@dataclass(frozen=True)
class Previa:
    resumo: str
    dados: Mapping[str, Any] = field(default_factory=dict)
    faltantes: tuple[CampoFaltante, ...] = ()


@dataclass(frozen=True)
class Preparo:
    sensivel: bool = False
    papel_liberacao: str | None = None
    meta: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextoAcao:
    client_id: str
    acao_id: str
    ator: str
    papel: str
    trace_id: str | None
    data_root: Path
    item: int = 0
    meta: Mapping[str, Any] = field(default_factory=dict)
    liberado_por: str | None = None
    documentos: Any = None

    def emitir_documento(
        self,
        modelo_id: str,
        fontes: Mapping[str, Any],
        *,
        referencia: str | None = None,
        metadados: Mapping[str, Any] | None = None,
        substitui: str | None = None,
    ) -> Any:
        if self.documentos is None:
            raise ExecucaoFalhou("o emissor de documentos não está disponível")
        return self.documentos.emitir(
            modelo_id,
            fontes,
            emitido_por=self.ator,
            emitido_papel=self.papel,
            referencia=referencia,
            metadados=metadados,
            acao_id=self.acao_id,
            trace_id=self.trace_id,
            substitui=substitui,
        )


@dataclass(frozen=True)
class TipoAcao:
    """Tipo de ação. `executar` devolve um dict com `resumo` (vai ao chat) e ids do efeito;
    o resto da resposta só aparece na página. `carregar` lê o produto ao vivo."""

    nome: str
    titulo: str
    descricao: str
    esquema: type[BaseModel]
    resumir: Callable[[dict[str, Any]], str]
    carregar: Callable[[ContextoAcao, dict[str, Any]], Previa]
    executar: Callable[[ContextoAcao, dict[str, Any]], dict[str, Any]]
    preparar: Callable[[ContextoAcao, dict[str, Any]], Preparo] | None = None
    gravar_campos: Callable[[ContextoAcao, dict[str, Any], dict[str, str]], None] | None = None
    validade_min: int | None = None
    sensivel: bool = False
    papel_minimo: str = "viewer"
    papel_liberacao: str = "admin"


class RegistroAcoes:
    def __init__(self) -> None:
        self._tipos: dict[str, TipoAcao] = {}

    def registrar(self, tipo: TipoAcao) -> None:
        if not _NOME_TIPO.match(tipo.nome):
            raise ValueError(f"nome de tipo de ação inválido: {tipo.nome!r}")
        if tipo.nome in self._tipos:
            raise ValueError(f"tipo de ação já registrado: {tipo.nome!r}")
        if tipo.esquema.model_config.get("extra") != "forbid":
            raise ValueError(f"esquema de {tipo.nome!r} precisa de extra='forbid'")
        if tipo.papel_minimo not in RANK or tipo.papel_liberacao not in RANK:
            raise ValueError(f"papel desconhecido em {tipo.nome!r}")
        self._tipos[tipo.nome] = tipo

    def registrar_do_modulo(self, modulo: str, tipo: TipoAcao) -> None:
        self.registrar(replace(tipo, nome=f"{modulo}.{tipo.nome}"))

    def get(self, nome: str) -> TipoAcao | None:
        return self._tipos.get(nome)

    def listar(self) -> list[TipoAcao]:
        return [self._tipos[n] for n in sorted(self._tipos)]


def digest_itens(itens: Sequence[Mapping[str, Any]]) -> str:
    canon = json.dumps(list(itens), sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _utc(valor: datetime | None) -> datetime | None:
    if valor is None:
        return None
    return valor if valor.tzinfo else valor.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Acao:
    id: str
    itens: tuple[dict[str, Any], ...]
    digest: str
    resumo: str
    status: str
    criado_por: str
    criado_papel: str
    criado_em: datetime
    expira_em: datetime
    trace_id: str | None
    sensivel: bool
    papel_minimo: str
    papel_liberacao: str
    confirmado_por: str | None
    confirmado_papel: str | None
    confirmado_provedor: str | None
    metodo: str | None
    liberado_por: str | None
    resultado: list[dict[str, Any]] | None
    motivo: str | None
    concluida_em: datetime | None


def _to_acao(row: AcaoRow) -> Acao:
    return Acao(
        id=row.id,
        itens=tuple(json.loads(row.itens)),
        digest=row.digest,
        resumo=row.resumo,
        status=row.status,
        criado_por=row.criado_por,
        criado_papel=row.criado_papel,
        criado_em=_utc(row.criado_em),
        expira_em=_utc(row.expira_em),
        trace_id=row.trace_id,
        sensivel=row.sensivel,
        papel_minimo=row.papel_minimo,
        papel_liberacao=row.papel_liberacao,
        confirmado_por=row.confirmado_por,
        confirmado_papel=row.confirmado_papel,
        confirmado_provedor=row.confirmado_provedor,
        metodo=row.metodo,
        liberado_por=row.liberado_por,
        resultado=json.loads(row.resultado) if row.resultado else None,
        motivo=row.motivo,
        concluida_em=_utc(row.concluida_em),
    )


class AcaoStore:
    def __init__(self, state: StateStore) -> None:
        self._engine = state.engine

    def criar(self, row: AcaoRow) -> Acao:
        with Session(self._engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return _to_acao(row)

    def get(self, acao_id: str) -> Acao | None:
        if not _ID_ACAO.match(acao_id or ""):
            return None
        with Session(self._engine) as session:
            row = session.get(AcaoRow, acao_id)
            return _to_acao(row) if row else None

    def transicao(self, acao_id: str, de: str, para: str, **campos: Any) -> bool:
        if para not in TRANSICOES.get(de, frozenset()):
            raise ValueError(f"transição inválida: {de} -> {para}")
        if campos.get("resultado") is not None:
            campos["resultado"] = json.dumps(campos["resultado"], sort_keys=True, default=str)
        with Session(self._engine) as session:
            result = session.execute(
                update(AcaoRow)
                .where(AcaoRow.id == acao_id, AcaoRow.status == de)
                .values(status=para, **campos)
            )
            session.commit()
            return result.rowcount == 1

    def criadas_desde(self, criado_por: str, desde: datetime) -> int:
        with Session(self._engine) as session:
            return (
                session.scalar(
                    select(func.count())
                    .select_from(AcaoRow)
                    .where(AcaoRow.criado_por == criado_por, AcaoRow.criado_em >= desde)
                )
                or 0
            )


Auditor = Callable[[str, bool, dict[str, Any]], None]


def _na_sessao(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Roda o método como a pessoa e com um trace_id, para a auditoria nunca sair anônima."""

    @functools.wraps(fn)
    def envolvido(self: CentralAcoes, identidade: Identidade, *args: Any, **kwargs: Any) -> Any:
        with self._como(identidade):
            _, token = ensure_trace_id()
            try:
                return fn(self, identidade, *args, **kwargs)
            finally:
                if token is not None:
                    trace_id_var.reset(token)

    return envolvido


def _rank(papel: str) -> int:
    return RANK.get(papel, -1)


class CentralAcoes:
    def __init__(
        self,
        *,
        client_id: str,
        config: CentralAcoesConfig,
        state: StateStore,
        atributos: AtributosUsuarioStore,
        registro: RegistroAcoes,
        auditar: Auditor,
        data_root: Path,
        documentos: Any = None,
        trace_id: Callable[[], str | None] = lambda: None,
        agora: Callable[[], datetime] = lambda: datetime.now(UTC),
        verificar_entrada: Callable[[dict[str, Any]], str | None] | None = None,
    ) -> None:
        self.client_id = client_id
        self.config = config
        self.store = AcaoStore(state)
        self.atributos = atributos
        self.registro = registro
        self._auditar = auditar
        self._data_root = Path(data_root)
        self.documentos = documentos
        self._trace_id = trace_id
        self._agora = agora
        self._verificar_entrada = verificar_entrada

    def link(self, acao_id: str) -> str:
        return f"{(self.config.url_base or '').rstrip('/')}/acao/{acao_id}"

    def _ctx(self, acao: Acao | None, identidade: Identidade, item: int = 0) -> ContextoAcao:
        meta = acao.itens[item].get("meta", {}) if acao is not None else {}
        return ContextoAcao(
            client_id=self.client_id,
            acao_id=acao.id if acao is not None else "",
            ator=identidade.perfil.identity,
            papel=identidade.perfil.role.value,
            trace_id=self._trace_id(),
            data_root=self._data_root,
            item=item,
            meta=meta,
            liberado_por=acao.liberado_por if acao is not None else None,
            documentos=self.documentos,
        )

    def _audit(self, evento: str, ok: bool, acao: Acao | None, **detalhes: Any) -> None:
        if acao is not None:
            detalhes = {
                "acao_id": acao.id,
                "trace_id_pedido": acao.trace_id,
                "criado_por": acao.criado_por,
                **detalhes,
            }
        self._auditar(f"acao:{evento}", ok, detalhes)

    def _tipo(self, nome: str) -> TipoAcao:
        tipo = self.registro.get(nome)
        if tipo is None:
            raise AcaoErro(422, f"tipo de ação desconhecido: {nome}")
        return tipo

    # -- lado do chat ------------------------------------------------------

    @_na_sessao
    def criar(self, identidade: Identidade, itens: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        try:
            return self._criar(identidade, itens)
        except AcaoErro as exc:
            self._audit("criacao_recusada", False, None, motivo=exc.motivo)
            return {"allowed": False, "reason": exc.motivo, **exc.extra}

    def _criar(self, identidade: Identidade, itens: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if not self.config.url_base:
            raise AcaoErro(503, "A Central de Ações não está configurada neste cliente.")
        if not itens or len(itens) > self.config.max_itens_lote:
            raise AcaoErro(422, f"Envie de 1 a {self.config.max_itens_lote} itens.")
        agora = self._agora()
        criadas = self.store.criadas_desde(identidade.perfil.identity, agora - timedelta(hours=1))
        if criadas >= self.config.max_por_hora:
            raise AcaoErro(429, "Limite de links por hora atingido para esta conta.")

        normalizados: list[dict[str, Any]] = []
        resumos: list[str] = []
        sensivel = False
        papel_minimo = "viewer"
        papel_liberacao = "admin"
        validade = self.config.validade_min
        ctx = self._ctx(None, identidade)
        for item in itens:
            tipo = self._tipo(str(item.get("tipo", "")))
            try:
                args = tipo.esquema.model_validate(item.get("argumentos") or {}).model_dump(
                    mode="json", exclude_none=True
                )
            except ValidationError as exc:
                campos = sorted({".".join(map(str, e["loc"])) for e in exc.errors()})
                raise AcaoErro(422, f"Argumentos inválidos para {tipo.nome}.", campos=campos)
            if self._verificar_entrada is not None:
                barrado = self._verificar_entrada(args)
                if barrado:
                    raise AcaoErro(
                        422,
                        "Os argumentos trazem dado pessoal; use os identificadores.",
                        dlp=barrado,
                    )
            try:
                preparo = tipo.preparar(ctx, args) if tipo.preparar else Preparo()
            except AcaoRecusada as exc:
                raise AcaoErro(403, str(exc)) from None
            except ExecucaoFalhou as exc:
                raise AcaoErro(503, exc.reason) from None
            item_sensivel = tipo.sensivel or preparo.sensivel
            sensivel = sensivel or item_sensivel
            if _rank(tipo.papel_minimo) > _rank(papel_minimo):
                papel_minimo = tipo.papel_minimo
            liberacao = preparo.papel_liberacao or tipo.papel_liberacao
            if item_sensivel and _rank(liberacao) > _rank(papel_liberacao):
                papel_liberacao = liberacao
            if tipo.validade_min:
                validade = min(validade, tipo.validade_min)
            normalizados.append({"tipo": tipo.nome, "argumentos": args, "meta": dict(preparo.meta)})
            resumos.append(tipo.resumir(args))

        acao_id = secrets.token_hex(16)
        resumo = resumos[0] if len(resumos) == 1 else f"{len(resumos)} itens: " + "; ".join(resumos)
        digest = digest_itens(normalizados)
        self._audit(
            "criada",
            True,
            None,
            acao_id=acao_id,
            tipos=[i["tipo"] for i in normalizados],
            digest=digest,
            sensivel=sensivel,
        )
        acao = self.store.criar(
            AcaoRow(
                id=acao_id,
                itens=json.dumps(normalizados, sort_keys=True, default=str, ensure_ascii=False),
                digest=digest,
                resumo=resumo,
                status=PENDENTE,
                criado_por=identidade.perfil.identity,
                criado_papel=identidade.perfil.role.value,
                criado_em=agora,
                expira_em=agora + timedelta(minutes=validade),
                trace_id=self._trace_id(),
                sensivel=sensivel,
                papel_minimo=papel_minimo,
                papel_liberacao=papel_liberacao,
            )
        )
        passo = "Envie o link à pessoa que vai confirmar; ela entra com a própria conta."
        if sensivel:
            passo = "Ação sensível: o link precisa ser aberto e liberado por um gestor."
        return {
            "allowed": True,
            "acao_id": acao.id,
            "link": self.link(acao.id),
            "resumo": acao.resumo,
            "status": acao.status,
            "sensivel": acao.sensivel,
            "expira_em": acao.expira_em.isoformat(),
            "proximo_passo": passo,
        }

    @_na_sessao
    def status(self, identidade: Identidade, acao_id: str) -> dict[str, Any]:
        acao = self._carregar(acao_id)
        if acao is None:
            return {"allowed": False, "reason": "ação não encontrada"}
        if acao.criado_por != identidade.perfil.identity and _rank(
            identidade.perfil.role.value
        ) < _rank("admin"):
            self._audit("status_negado", False, acao)
            return {"allowed": False, "reason": "ação não encontrada"}
        return {
            "allowed": True,
            "acao_id": acao.id,
            "status": acao.status,
            "resumo": acao.resumo,
            "link": self.link(acao.id),
            "expira_em": acao.expira_em.isoformat(),
            "confirmado_por": acao.confirmado_por,
            "liberado_por": acao.liberado_por,
            "resultado": [
                {k: v for k, v in r.items() if k in ("item", "ok", "resumo") or k.endswith("_id")}
                for r in acao.resultado or []
            ],
            "motivo": acao.motivo,
        }

    # -- lado da página ----------------------------------------------------

    def _carregar(self, acao_id: str) -> Acao | None:
        acao = self.store.get(acao_id)
        if acao is None:
            return None
        if acao.status == PENDENTE and self._agora() >= acao.expira_em:
            if self.store.transicao(acao.id, PENDENTE, EXPIRADA):
                self._audit("expirada", False, acao)
            return self.store.get(acao.id)
        return acao

    def _exigir(self, identidade: Identidade, acao_id: str, *, pendente: bool = True) -> Acao:
        acao = self._carregar(acao_id)
        if acao is None:
            raise AcaoErro(404, "Ação não encontrada.")
        if _rank(identidade.perfil.role.value) < _rank(acao.papel_minimo):
            self._audit("acesso_negado", False, acao, pessoa=identidade.perfil.identity)
            raise AcaoErro(403, "Seu perfil não pode abrir esta ação.")
        if pendente and acao.status == EXPIRADA:
            raise AcaoErro(410, "Este link expirou. Peça um novo no chat.")
        if pendente and acao.status != PENDENTE:
            raise AcaoErro(409, f"Este link já foi usado (estado: {acao.status}).")
        return acao

    def _ve_dados(self, identidade: Identidade, acao: Acao) -> bool:
        return not acao.sensivel or _rank(identidade.perfil.role.value) >= _rank(
            acao.papel_liberacao
        )

    def _previas(self, identidade: Identidade, acao: Acao) -> list[tuple[TipoAcao, Previa]]:
        saida = []
        for i, item in enumerate(acao.itens):
            tipo = self._tipo(item["tipo"])
            try:
                saida.append(
                    (tipo, tipo.carregar(self._ctx(acao, identidade, i), item["argumentos"]))
                )
            except ExecucaoFalhou as exc:
                raise AcaoErro(503, exc.reason) from None
        return saida

    def _cabecalho(self, identidade: Identidade, acao: Acao) -> dict[str, Any]:
        return {
            "acao_id": acao.id,
            "status": acao.status,
            "resumo_pedido": acao.resumo,
            "voce": {
                **identidade.publica(),
                "nome": self.atributos.nome(identidade.provedor, identidade.id),
            },
            "pedido_por": acao.criado_por,
            "pedido_em": acao.criado_em.isoformat(),
            "expira_em": acao.expira_em.isoformat(),
            "sensivel": acao.sensivel,
            "requer": "liberacao_gestor" if acao.sensivel else "pin",
            "papel_liberacao": acao.papel_liberacao if acao.sensivel else None,
            "pin_definido": self.atributos.pin_definido(identidade.provedor, identidade.id),
        }

    @_na_sessao
    def abrir(self, identidade: Identidade, acao_id: str) -> dict[str, Any]:
        acao = self._exigir(identidade, acao_id, pendente=False)
        corpo = self._cabecalho(identidade, acao)
        if acao.status != PENDENTE:
            corpo["resultado"] = acao.resultado
            corpo["motivo"] = acao.motivo
            corpo["confirmado_por"] = acao.confirmado_por
            corpo["liberado_por"] = acao.liberado_por
            self._audit("aberta", True, acao, pessoa=identidade.perfil.identity, dados=False)
            return corpo
        if not self._ve_dados(identidade, acao):
            corpo["itens"] = []
            corpo["aviso"] = "Aguardando liberação de um gestor; os dados só aparecem para ele."
            self._audit("aberta", True, acao, pessoa=identidade.perfil.identity, dados=False)
            return corpo
        corpo["itens"] = [
            {
                "indice": i,
                "tipo": tipo.nome,
                "titulo": tipo.titulo,
                "resumo": previa.resumo,
                "dados": previa.dados,
                "campos_faltantes": [f.__dict__ for f in previa.faltantes],
            }
            for i, (tipo, previa) in enumerate(self._previas(identidade, acao))
        ]
        self._audit(
            "aberta",
            True,
            acao,
            pessoa=identidade.perfil.identity,
            provedor=identidade.provedor,
            metodo=identidade.metodo,
            dados=True,
        )
        return corpo

    def _conferir_pin(self, identidade: Identidade, acao: Acao, pin: str) -> None:
        resultado = self.atributos.verificar_pin(identidade.provedor, identidade.id, pin or "")
        if resultado.ok:
            return
        pessoa = identidade.perfil.identity
        if resultado.status == PIN_NAO_DEFINIDO:
            self._audit("pin_nao_definido", False, acao, pessoa=pessoa)
            raise AcaoErro(428, "Defina seu PIN antes de confirmar ações.")
        if resultado.status == PIN_BLOQUEADO:
            self._audit("pin_bloqueado", False, acao, pessoa=pessoa)
            ate = resultado.bloqueado_ate.isoformat() if resultado.bloqueado_ate else None
            raise AcaoErro(423, "PIN bloqueado por tentativas erradas.", bloqueado_ate=ate)
        self._audit("pin_incorreto", False, acao, pessoa=pessoa, restantes=resultado.restantes)
        raise AcaoErro(401, "PIN incorreto.", tentativas_restantes=resultado.restantes)

    def _gravar(
        self, identidade: Identidade, acao: Acao, campos: Sequence[Mapping[str, Any]]
    ) -> list[str]:
        if not self._ve_dados(identidade, acao):
            raise AcaoErro(403, "Só o gestor pode preencher dados desta ação.")
        previas = self._previas(identidade, acao)
        por_item: dict[int, dict[str, str]] = {}
        for campo in campos:
            try:
                indice = int(campo.get("item", 0))
                nome = str(campo["nome"])
                valor = str(campo["valor"]).strip()
            except (KeyError, TypeError, ValueError):
                raise AcaoErro(422, "Campo mal informado: use item, nome e valor.") from None
            if not 0 <= indice < len(previas):
                raise AcaoErro(422, f"Item inexistente: {indice}.")
            tipo, previa = previas[indice]
            editaveis = {f.nome for f in previa.faltantes if f.editavel}
            if nome not in editaveis or tipo.gravar_campos is None or not valor:
                raise AcaoErro(422, f"O campo {nome!r} não pode ser preenchido aqui.")
            por_item.setdefault(indice, {})[nome] = valor
        gravados: list[str] = []
        for indice, valores in sorted(por_item.items()):
            tipo = previas[indice][0]
            nomes = sorted(valores)
            self._audit(
                "campos_gravando",
                True,
                acao,
                pessoa=identidade.perfil.identity,
                item=indice,
                campos=nomes,
            )
            try:
                tipo.gravar_campos(
                    self._ctx(acao, identidade, indice), acao.itens[indice]["argumentos"], valores
                )
            except ExecucaoFalhou as exc:
                self._audit("campos_falharam", False, acao, item=indice, campos=nomes)
                raise AcaoErro(422, exc.reason) from None
            self._audit("campos_gravados", True, acao, item=indice, campos=nomes)
            gravados += [f"{indice}.{n}" for n in nomes]
        return gravados

    @_na_sessao
    def gravar_campos(
        self, identidade: Identidade, acao_id: str, pin: str, campos: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        acao = self._exigir(identidade, acao_id)
        if not campos:
            raise AcaoErro(422, "Nenhum campo informado.")
        self._conferir_pin(identidade, acao, pin)
        with self._como(identidade):
            gravados = self._gravar(identidade, acao, campos)
        return {"acao_id": acao.id, "gravados": gravados}

    @_na_sessao
    def confirmar(
        self,
        identidade: Identidade,
        acao_id: str,
        pin: str,
        campos: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        acao = self._exigir(identidade, acao_id)
        if acao.sensivel:
            self._audit("confirmacao_negada", False, acao, motivo="exige_liberacao")
            raise AcaoErro(403, "Esta ação é sensível e precisa ser liberada por um gestor.")
        self._conferir_pin(identidade, acao, pin)
        return self._executar(identidade, acao, campos, liberacao=False)

    @_na_sessao
    def liberar(
        self,
        identidade: Identidade,
        acao_id: str,
        pin: str,
        campos: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        acao = self._exigir(identidade, acao_id)
        pessoa = identidade.perfil.identity
        if not acao.sensivel:
            raise AcaoErro(409, "Esta ação não exige liberação; use a confirmação com PIN.")
        if _rank(identidade.perfil.role.value) < _rank(acao.papel_liberacao):
            self._audit("liberacao_negada", False, acao, pessoa=pessoa, motivo="papel")
            raise AcaoErro(403, f"Esta ação exige liberação de perfil {acao.papel_liberacao}.")
        if pessoa == acao.criado_por:
            self._audit("liberacao_negada", False, acao, pessoa=pessoa, motivo="segregacao")
            raise AcaoErro(403, "Quem pediu a ação não pode liberá-la.")
        if self.config.exigir_aal2_liberacao and not (
            identidade.aal == "aal2"
            and identidade.autenticacao_recente(self.config.reautenticacao_min, self._agora())
        ):
            self._audit("liberacao_negada", False, acao, pessoa=pessoa, motivo="aal2")
            raise AcaoErro(401, "Entre de novo com senha e código do autenticador (TOTP).")
        self._conferir_pin(identidade, acao, pin)
        return self._executar(identidade, acao, campos, liberacao=True)

    @_na_sessao
    def cancelar(self, identidade: Identidade, acao_id: str) -> dict[str, Any]:
        acao = self._exigir(identidade, acao_id)
        pessoa = identidade.perfil.identity
        if pessoa != acao.criado_por and _rank(identidade.perfil.role.value) < _rank("admin"):
            raise AcaoErro(403, "Só quem pediu ou um gestor pode cancelar.")
        if not self.store.transicao(acao.id, PENDENTE, CANCELADA, motivo=f"cancelada por {pessoa}"):
            raise AcaoErro(409, "Esta ação já foi tratada.")
        self._audit("cancelada", False, acao, pessoa=pessoa)
        return {"acao_id": acao.id, "status": CANCELADA}

    @staticmethod
    @contextmanager
    def _como(identidade: Identidade) -> Iterator[None]:
        t1 = set_current_user(identidade.perfil)
        t2 = set_identidade(identidade)
        try:
            yield
        finally:
            identidade_var.reset(t2)
            current_user_var.reset(t1)

    def _executar(
        self,
        identidade: Identidade,
        acao: Acao,
        campos: Sequence[Mapping[str, Any]],
        *,
        liberacao: bool,
    ) -> dict[str, Any]:
        pessoa = identidade.perfil.identity
        if digest_itens(acao.itens) != acao.digest:
            self.store.transicao(acao.id, PENDENTE, FALHOU, motivo="pedido adulterado")
            self._audit("falhou", False, acao, motivo="pedido adulterado")
            raise AcaoErro(409, "O pedido foi alterado depois de criado e não será executado.")
        with self._como(identidade):
            if campos:
                self._gravar(identidade, acao, campos)
            faltando = [
                f"{i}.{f.nome}"
                for i, (_, previa) in enumerate(self._previas(identidade, acao))
                for f in previa.faltantes
            ]
            if faltando:
                raise AcaoErro(422, "Preencha os campos obrigatórios.", faltantes=faltando)

            metodo = "pin+liberacao" if liberacao else "pin"
            self._audit(
                "execucao_iniciada",
                True,
                acao,
                pessoa=pessoa,
                provedor=identidade.provedor,
                metodo=metodo,
                aal=identidade.aal,
            )
            marcado = self.store.transicao(
                acao.id,
                PENDENTE,
                EM_EXECUCAO,
                confirmado_por=pessoa,
                confirmado_papel=identidade.perfil.role.value,
                confirmado_provedor=identidade.provedor,
                metodo=metodo,
                liberado_por=pessoa if liberacao else None,
            )
            if not marcado:
                raise AcaoErro(409, "Este link já foi usado.")
            acao = self.store.get(acao.id) or acao

            resultados: list[dict[str, Any]] = []
            completos: list[dict[str, Any]] = []
            falha: str | None = None
            for i, item in enumerate(acao.itens):
                if falha is not None:
                    resultados.append({"item": i, "ok": False, "resumo": "não executado"})
                    continue
                tipo = self._tipo(item["tipo"])
                try:
                    saida = tipo.executar(self._ctx(acao, identidade, i), item["argumentos"])
                    if not isinstance(saida, dict):
                        raise ExecucaoFalhou("resultado fora do contrato")
                except ExecucaoFalhou as exc:
                    falha = exc.reason
                except AcaoErro as exc:
                    falha = exc.motivo
                except Exception:
                    logger.exception("falha ao executar item %s da ação %s", i, acao.id)
                    falha = "erro interno ao executar"
                if falha is not None:
                    resultados.append({"item": i, "ok": False, "resumo": falha})
                    continue
                publico = {
                    k: v
                    for k, v in saida.items()
                    if k == "resumo" or k.endswith("_id") or k == "allowed"
                }
                resultados.append({"item": i, "ok": True, **publico})
                completos.append({"item": i, **saida})

            final = CONFIRMADA if falha is None else FALHOU
            self.store.transicao(
                acao.id,
                EM_EXECUCAO,
                final,
                resultado=resultados,
                motivo=falha,
                concluida_em=self._agora(),
            )
            self._audit(
                final, falha is None, acao, pessoa=pessoa, metodo=metodo, resultado=resultados
            )
        return {
            "acao_id": acao.id,
            "status": final,
            "motivo": falha,
            "resultado": resultados,
            "detalhes": completos,
        }
