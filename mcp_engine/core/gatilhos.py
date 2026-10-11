"""Gatilhos e automações declarativas (spec 031).

O gatilho é uma nota aprovada (spec 030) que o motor executa sozinho, como o ator de sistema
`sistema:<id>`, com as permissões mínimas que ele declarou. Agenda: laço assíncrono simples e
tabela em `state.db`, sem biblioteca de agendamento. Eventos: tabela `evento` (outbox) escrita
só depois do commit do efeito de domínio.

Garantias: (T1) ação A2 ou A3 nunca executa sem pessoa, vira operação aguardando aprovação;
(T2) cada janela executa no máximo uma vez (chave única `gatilho@janela`), inclusive após
reinício; (T3) gatilho inativo, pausado ou desativado pelo disjuntor não dispara.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from json_logic import jsonLogic
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mcp_engine.core import cron, gatilhos_tipo
from mcp_engine.core.auth import (
    Role,
    UserProfile,
    current_user_var,
    ensure_trace_id,
    is_tool_allowed,
    set_current_user,
    trace_id_var,
)
from mcp_engine.core.config_notas import TipoConfig, VersaoAtiva
from mcp_engine.core.fatos import ContextoFatos, FatosIndisponiveis
from mcp_engine.core.interceptor import scan_response
from mcp_engine.core.models import AuditDecision, AuditEvent
from mcp_engine.core.state import (
    ControleRow,
    EventoRow,
    GatilhoAlertaRow,
    GatilhoExecucaoRow,
    GatilhoRow,
)
from mcp_engine.core.user_store import IDENTIDADE_SISTEMA

logger = logging.getLogger(__name__)

EXECUTANDO = "executando"
OK = "ok"
PROPOSTA = "proposta"
FALHOU = "falhou"
NEGADO = "negado"
CONDICAO_FALSA = "condicao_falsa"
LIMITADO = "limitado"
CORTADO = "cortado"
FORA_DO_HORARIO = "fora_do_horario"
PERDIDA = "perdida"

CONTAM_NO_LIMITE = (OK, PROPOSTA, FALHOU, NEGADO)
FALHAS = (FALHOU, NEGADO)
SAUDAVEIS = (OK, PROPOSTA, CONDICAO_FALSA)
REJEICOES = (LIMITADO, CORTADO)
LEASE = timedelta(minutes=5)
BATIMENTO_A_CADA = timedelta(seconds=15)
ATRASO_ALERTA = timedelta(minutes=3)
PAUSA = "gatilhos_pausados"
BATIMENTO = "agendador_batimento"
TAMANHO_MAX_PAYLOAD = 4096

_TIPO_EVENTO = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_MODELO = re.compile(r"\{\{\s*(.*?)\s*\}\}")
_SLUG = re.compile(r"[^0-9A-Za-z_-]+")

gatilho_atual: ContextVar[tuple[str, int] | None] = ContextVar("gatilho_atual", default=None)


def _naive(momento: datetime) -> datetime:
    return momento.astimezone(UTC).replace(tzinfo=None)


def _aware(momento: datetime) -> datetime:
    return momento.replace(tzinfo=UTC) if momento.tzinfo is None else momento


def _buscar(contexto: dict[str, Any], nome: str) -> tuple[bool, Any]:
    atual: Any = contexto
    for parte in nome.split("."):
        if isinstance(atual, dict) and parte in atual:
            atual = atual[parte]
        else:
            return False, None
    return True, atual


def _texto(valor: Any) -> str:
    if valor is None:
        return "-"
    if isinstance(valor, bool):
        return "sim" if valor else "não"
    return str(valor)


def renderizar(valor: Any, contexto: dict[str, Any], *, estrito: bool = True) -> Any:
    """Substitui `{{ variavel }}` por texto. Sem avaliação de código: só busca por caminho."""
    if isinstance(valor, str):

        def troca(m: re.Match[str]) -> str:
            achou, resolvido = _buscar(contexto, m.group(1))
            if achou:
                return _texto(resolvido)
            if estrito:
                raise ValueError(f"variável sem valor no modelo: {m.group(1)!r}")
            return m.group(0)

        return _MODELO.sub(troca, valor)
    if isinstance(valor, dict):
        return {k: renderizar(v, contexto, estrito=estrito) for k, v in valor.items()}
    if isinstance(valor, list):
        return [renderizar(v, contexto, estrito=estrito) for v in valor]
    return valor


class Gatilhos:
    def __init__(
        self, engine: Any, relogio: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._e = engine
        self._db = engine._state.engine
        self.relogio = relogio
        self._tick_lock = threading.Lock()
        self._ultimo_batimento: datetime | None = None

    def tipo(self) -> TipoConfig:
        return replace(gatilhos_tipo.tipo_gatilho(), materializar=self.materializar)

    @property
    def habilitado(self) -> bool:
        cfg = self._e._client_config
        return bool(cfg.config_via_notas and cfg.gatilhos_ativos)

    def materializar(self, versoes: tuple[VersaoAtiva, ...]) -> None:
        """Deixa a tabela `gatilho` igual às versões aprovadas; estado de execução é mantido
        enquanto o conteúdo aprovado não muda (nova aprovação reabre um gatilho desativado)."""
        with Session(self._db) as s:
            existentes = {r.config_id: r for r in s.scalars(select(GatilhoRow))}
            ativos = set()
            for v in versoes:
                ativos.add(v.config_id)
                definicao = json.dumps(dict(v.dados), sort_keys=True, ensure_ascii=False)
                row = existentes.get(v.config_id)
                if row is None:
                    s.add(
                        GatilhoRow(
                            config_id=v.config_id,
                            caminho=v.caminho,
                            hash=v.hash,
                            versao=v.versao,
                            aprovado_por=v.aprovado_por,
                            definicao=definicao,
                        )
                    )
                elif row.hash != v.hash:
                    row.caminho, row.hash, row.versao = v.caminho, v.hash, v.versao
                    row.aprovado_por, row.definicao = v.aprovado_por, definicao
                    row.ativo, row.motivo_desativacao = True, None
                    row.falhas_seguidas, row.proxima_execucao = 0, None
            for config_id, row in existentes.items():
                if config_id not in ativos:
                    s.delete(row)
            s.commit()

    def reconciliar(self, ativas: list[VersaoAtiva]) -> None:
        self.materializar(tuple(v for v in ativas if v.tipo == "gatilho"))

    def _get(self, config_id: str) -> GatilhoRow | None:
        with Session(self._db) as s:
            row = s.get(GatilhoRow, config_id)
            if row is not None:
                s.expunge(row)
            return row

    def listar_ativos(self) -> list[GatilhoRow]:
        with Session(self._db) as s:
            rows = list(s.scalars(select(GatilhoRow).order_by(GatilhoRow.config_id)))
            for r in rows:
                s.expunge(r)
            return rows

    def _controle(self, chave: str) -> str | None:
        with Session(self._db) as s:
            row = s.get(ControleRow, chave)
            return row.valor if row else None

    def _definir_controle(self, chave: str, valor: str) -> None:
        with Session(self._db) as s:
            row = s.get(ControleRow, chave)
            if row is None:
                s.add(ControleRow(chave=chave, valor=valor))
            else:
                row.valor = valor
                row.atualizado_em = datetime.now(UTC)
            s.commit()

    def pausado(self) -> bool:
        return self._controle(PAUSA) == "1"

    def pausar(self, pausado: bool, por: str) -> None:
        self._definir_controle(PAUSA, "1" if pausado else "0")
        self._auditar_sistema(
            "gatilhos:pausa" if pausado else "gatilhos:retomada", AuditDecision.ALLOWED,
            ator=por, por=por,
        )  # fmt: skip

    def _auditar_sistema(
        self, action: str, decisao: AuditDecision, *, ator: str = "motor", **detalhes: Any
    ) -> None:
        self._e._audit_sink.record(
            AuditEvent(
                client_id=self._e.client_id,
                occurred_at=datetime.now(UTC),
                actor=ator,
                action=action,
                decision=decisao,
                details=detalhes,
            )
        )

    # ------------------------------------------------------------------ eventos (outbox)

    def publicar_eventos(self, eventos: Any) -> None:
        """Grava eventos pós-commit. Falha de publicação nunca desfaz o efeito já gravado."""
        try:
            origem = gatilho_atual.get()
            profundidade = origem[1] + 1 if origem else 0
            with Session(self._db) as s:
                for ev in eventos if isinstance(eventos, list) else []:
                    tipo = str(ev.get("tipo", ""))
                    payload = ev.get("payload") or {}
                    bruto = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
                    if not _TIPO_EVENTO.match(tipo) or len(bruto) > TAMANHO_MAX_PAYLOAD:
                        logger.warning("evento descartado (tipo ou tamanho inválido): %r", tipo)
                        continue
                    dedup = ev.get("dedup")
                    s.add(
                        EventoRow(
                            tipo=tipo,
                            payload=bruto,
                            criado_em=_naive(datetime.now(UTC)),
                            profundidade=profundidade,
                            origem_gatilho=origem[0] if origem else None,
                            chave_dedup=f"{tipo}:{dedup}" if dedup else None,
                            trace_id=trace_id_var.get(),
                        )
                    )
                    try:
                        s.commit()
                    except IntegrityError:
                        s.rollback()
        except Exception:
            logger.exception("falha ao publicar eventos")

    def _eventos_pendentes(self, limite: int = 100) -> list[EventoRow]:
        with Session(self._db) as s:
            rows = list(
                s.scalars(
                    select(EventoRow)
                    .where(EventoRow.processado_em.is_(None))
                    .order_by(EventoRow.id)
                    .limit(limite)
                )
            )
            for r in rows:
                s.expunge(r)
            return rows

    def _marcar_processado(self, evento_id: int, agora: datetime) -> None:
        with Session(self._db) as s:
            s.execute(
                update(EventoRow)
                .where(EventoRow.id == evento_id)
                .values(processado_em=_naive(agora))
            )
            s.commit()

    # ------------------------------------------------------------------ agendador

    def tick(self, agora: datetime | None = None) -> dict[str, int]:
        """Um passo do agendador: agendas vencidas e eventos pendentes. Seguro para reentrada."""
        agora = agora or self.relogio()
        resumo = {"agendas": 0, "eventos": 0}
        if not self.habilitado:
            return resumo
        with self._tick_lock:
            self._batimento(agora)
            if self.pausado():
                return resumo
            for row in self.listar_ativos():
                d = json.loads(row.definicao)
                if row.ativo and d["quando"].get("agenda"):
                    resumo["agendas"] += self._tick_agenda(row, d, agora)
            resumo["eventos"] = self._processar_eventos(agora)
        return resumo

    def _batimento(self, agora: datetime) -> None:
        if self._ultimo_batimento and agora - self._ultimo_batimento < BATIMENTO_A_CADA:
            return
        self._ultimo_batimento = agora
        self._definir_controle(BATIMENTO, agora.isoformat())

    def _agendar(self, config_id: str, proxima: datetime | None) -> None:
        with Session(self._db) as s:
            s.execute(
                update(GatilhoRow)
                .where(GatilhoRow.config_id == config_id)
                .values(proxima_execucao=_naive(proxima) if proxima else None)
            )
            s.commit()

    def _tick_agenda(self, row: GatilhoRow, d: dict[str, Any], agora: datetime) -> int:
        expressao = cron.parse(d["quando"]["agenda"])
        fuso = d["quando"]["fuso"]
        if row.proxima_execucao is None:
            self._agendar(row.config_id, cron.proxima(expressao, agora, fuso))
            return 0
        janela_dt = _aware(row.proxima_execucao)
        if janela_dt > agora:
            return 0
        janela = janela_dt.isoformat(timespec="minutes")
        atraso = agora - janela_dt
        limites = d["limites"]
        if atraso > timedelta(minutes=limites["atraso_maximo_min"]):
            estado = self._registrar_perdida(row, janela, agora)
        else:
            estado = self.executar(row.config_id, janela=janela, origem="agenda", agora=agora)[
                "status"
            ]
        if estado != "ocupada":
            self._agendar(row.config_id, cron.proxima(expressao, max(agora, janela_dt), fuso))
        return 0 if estado in ("ocupada", "ja_executada", PERDIDA) else 1

    def _registrar_perdida(self, row: GatilhoRow, janela: str, agora: datetime) -> str:
        chave = f"{row.config_id}@{janela}"
        with Session(self._db) as s:
            s.add(
                GatilhoExecucaoRow(
                    gatilho_id=row.config_id, chave=chave, janela=janela, origem="agenda",
                    status=PERDIDA, hash_definicao=row.hash, iniciado_em=_naive(agora),
                    concluido_em=_naive(agora),
                    erro="janela vencida além do atraso máximo (motor parado)",
                )
            )  # fmt: skip
            try:
                s.commit()
            except IntegrityError:
                s.rollback()
                return "ja_executada"
        self._auditar_sistema("gatilho:perdida", AuditDecision.DENIED, gatilho_id=row.config_id,
                              janela=janela)  # fmt: skip
        return PERDIDA

    def _processar_eventos(self, agora: datetime) -> int:
        executados = 0
        por_tipo: dict[str, list[GatilhoRow]] = {}
        for row in self.listar_ativos():
            d = json.loads(row.definicao)
            if row.ativo and d["quando"].get("evento"):
                por_tipo.setdefault(d["quando"]["evento"], []).append(row)
        for ev in self._eventos_pendentes():
            pendente = False
            for row in por_tipo.get(ev.tipo, []):
                resultado = self.executar(
                    row.config_id, janela=f"evt{ev.id}", origem="evento", agora=agora, evento=ev
                )
                pendente = pendente or resultado["status"] == "ocupada"
                executados += resultado["status"] not in ("ocupada", "ja_executada", "inativo")
            if not pendente:
                self._marcar_processado(ev.id, agora)
        return executados

    async def rodar(self, intervalo: float = 1.0, parar: asyncio.Event | None = None) -> None:
        parar = parar or asyncio.Event()
        while not parar.is_set():
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                logger.exception("falha no agendador de gatilhos")
            try:
                await asyncio.wait_for(parar.wait(), timeout=intervalo)
            except TimeoutError:
                pass

    # ------------------------------------------------------------------ execução

    def _reservar(
        self, row: GatilhoRow, chave: str, janela: str, origem: str, evento: EventoRow | None,
        agora: datetime,
    ) -> tuple[str, int | None]:  # fmt: skip
        n = _naive(agora)
        with Session(self._db) as s:
            s.add(
                GatilhoExecucaoRow(
                    gatilho_id=row.config_id, chave=chave, janela=janela, origem=origem,
                    evento_id=evento.id if evento else None,
                    profundidade=evento.profundidade if evento else 0, status=EXECUTANDO,
                    hash_definicao=row.hash, iniciado_em=n, trace_id=trace_id_var.get(),
                )
            )  # fmt: skip
            try:
                s.commit()
                return "novo", s.scalar(
                    select(GatilhoExecucaoRow.id).where(GatilhoExecucaoRow.chave == chave)
                )
            except IntegrityError:
                s.rollback()
            existente = s.scalar(
                select(GatilhoExecucaoRow).where(GatilhoExecucaoRow.chave == chave)
            )
            if existente is None:
                return "ocupada", None
            if existente.status != EXECUTANDO:
                return "ja_executada", existente.id
            if n - existente.iniciado_em < LEASE:
                return "ocupada", existente.id
            tomou = s.execute(
                update(GatilhoExecucaoRow)
                .where(
                    GatilhoExecucaoRow.id == existente.id,
                    GatilhoExecucaoRow.status == EXECUTANDO,
                    GatilhoExecucaoRow.iniciado_em == existente.iniciado_em,
                )
                .values(iniciado_em=n)
            )
            s.commit()
            return ("retomada", existente.id) if tomou.rowcount == 1 else ("ocupada", existente.id)

    def _contar_execucoes(self, config_id: str, status: tuple[str, ...], desde: datetime) -> int:
        with Session(self._db) as s:
            return (
                s.scalar(
                    select(func.count())
                    .select_from(GatilhoExecucaoRow)
                    .where(
                        GatilhoExecucaoRow.gatilho_id == config_id,
                        GatilhoExecucaoRow.status.in_(status),
                        GatilhoExecucaoRow.iniciado_em >= _naive(desde),
                    )
                )
                or 0
            )

    def executar(
        self, config_id: str, *, janela: str, origem: str, agora: datetime | None = None,
        evento: EventoRow | None = None, sufixo: str = "",
    ) -> dict[str, Any]:  # fmt: skip
        agora = agora or self.relogio()
        row = self._get(config_id)
        if row is None or not row.ativo or self.pausado() or not self.habilitado:
            return {"status": "inativo"}
        chave = f"{config_id}@{janela}{sufixo}"
        if evento is not None and evento.trace_id:
            token = trace_id_var.set(evento.trace_id)
        else:
            _, token = ensure_trace_id()
        user_token = None
        try:
            estado, exec_id = self._reservar(row, chave, janela, origem, evento, agora)
            if estado in ("ja_executada", "ocupada"):
                return {"status": estado}
            d = json.loads(row.definicao)
            ator = UserProfile(identity=f"{IDENTIDADE_SISTEMA}{config_id}", role=Role(d["papel"]))
            user_token = set_current_user(ator)
            try:
                status, resultado, operacao_id, erro = self._decidir_e_rodar(
                    row, d, exec_id, chave, janela, evento, agora, retomada=estado == "retomada"
                )
            except Exception as exc:
                logger.exception("gatilho %s falhou", config_id)
                status, resultado, operacao_id, erro = FALHOU, {}, None, type(exc).__name__
            self._concluir(row, d, exec_id, status, resultado, erro, operacao_id, agora)
            self._auditar_execucao(row, d, chave, status, evento, operacao_id, erro)
            return {"status": status, "operacao_id": operacao_id, "erro": erro}
        finally:
            if user_token is not None:
                current_user_var.reset(user_token)
            if token is not None:
                trace_id_var.reset(token)

    def _contexto(
        self, d: dict[str, Any], janela: str, evento: EventoRow | None, agora: datetime
    ) -> dict[str, Any]:
        local = agora.astimezone(ZoneInfo(d["quando"]["fuso"]))
        anterior = local.replace(day=1) - timedelta(days=1)
        ctx: dict[str, Any] = {
            "hoje": local.date().isoformat(),
            "agora": local.isoformat(timespec="minutes"),
            "hora": local.hour,
            "dia_semana": local.isoweekday(),
            "janela": janela,
            "mes_atual": local.strftime("%Y-%m"),
            "mes_anterior": anterior.strftime("%Y-%m"),
        }
        if evento is not None:
            ctx["evento"] = {
                **json.loads(evento.payload),
                "tipo": evento.tipo,
                "id": evento.id,
                "profundidade": evento.profundidade,
            }
        return ctx

    def _fatos(self, d: dict[str, Any], ctx: dict[str, Any], ator: UserProfile) -> dict[str, Any]:
        if not d["fatos_de_modulo"]:
            return {}
        argumentos = renderizar(d["acao"]["argumentos"], ctx, estrito=False)
        contexto = ContextoFatos(
            self._e.client_id, ator.identity, ator.role.value, self._e._data_root
        )
        return self._e.extensoes.provedores.calcular(d["fatos_de_modulo"], contexto, argumentos)

    def _dentro_do_horario(self, d: dict[str, Any], agora: datetime) -> bool:
        horario = d["limites"].get("horario")
        if not horario:
            return True
        local = agora.astimezone(ZoneInfo(d["quando"]["fuso"])).strftime("%H:%M")
        return horario["das"] <= local <= horario["ate"]

    def _decidir_e_rodar(
        self, row: GatilhoRow, d: dict[str, Any], exec_id: int | None, chave: str, janela: str,
        evento: EventoRow | None, agora: datetime, *, retomada: bool,
    ) -> tuple[str, dict[str, Any], str | None, str | None]:  # fmt: skip
        limites = d["limites"]
        profundidade = evento.profundidade if evento else 0
        if evento is not None and profundidade > limites["max_profundidade"]:
            self._alertar(d, "critico", f"Encadeamento cortado: o evento {evento.tipo} chegou à "
                          f"profundidade {profundidade} (máximo {limites['max_profundidade']}).",
                          exec_id)  # fmt: skip
            self._checar_tempestade(row, d)
            return CORTADO, {"profundidade": profundidade}, None, None
        if not self._dentro_do_horario(d, agora):
            return FORA_DO_HORARIO, {}, None, None
        feitas = self._contar_execucoes(row.config_id, CONTAM_NO_LIMITE, agora - timedelta(hours=1))
        if feitas >= limites["max_por_hora"]:
            self._checar_tempestade(row, d)
            return LIMITADO, {"feitas_na_ultima_hora": feitas}, None, None

        ator = UserProfile(identity=f"{IDENTIDADE_SISTEMA}{row.config_id}", role=Role(d["papel"]))
        ctx = self._contexto(d, janela, evento, agora)
        try:
            ctx.update(self._fatos(d, ctx, ator))
        except FatosIndisponiveis as exc:
            return FALHOU, {}, None, str(exc)
        condicao = d.get("condicao")
        if condicao is not None and not jsonLogic(condicao, ctx):
            return CONDICAO_FALSA, {}, None, None
        try:
            argumentos = renderizar(d["acao"]["argumentos"], ctx)
        except ValueError as exc:
            return FALHOU, {}, None, str(exc)

        negado = self._negar_se_sem_permissao(d, d["acao"]["skill"], ator)
        if negado:
            self._alertar(d, "critico", f"Gatilho sem permissão: {negado}", exec_id)
            return NEGADO, {}, None, negado

        token = gatilho_atual.set((row.config_id, profundidade))
        try:
            adotada = self._operacao_existente(row, d, exec_id) if retomada else None
            if adotada is not None:
                resposta: dict[str, Any] = {
                    "allowed": False, "operacao_id": adotada.id, "status": adotada.status,
                    "resumo": adotada.resumo,
                }  # fmt: skip
            else:
                resposta = self._e._call_skill(
                    d["acao"]["skill"], argumentos, idempotency_key=chave, _sistema=True
                )
        finally:
            gatilho_atual.reset(token)

        resultado_vars: dict[str, Any] = {}
        operacao_id = resposta.get("operacao_id")
        if resposta.get("allowed"):
            try:
                bruto = json.loads(resposta.get("result") or "{}")
                resultado_vars = bruto if isinstance(bruto, dict) else {}
            except ValueError:
                resultado_vars = {}
            status = OK
        elif operacao_id:
            resultado_vars = {
                "operacao_id": operacao_id,
                "status": resposta.get("status"),
                "resumo": resposta.get("resumo"),
            }
            status = PROPOSTA
        else:
            motivo = str(resposta.get("reason") or "ação recusada")
            self._alertar(d, "critico", f"Gatilho {d['id']} falhou: {motivo}", exec_id)
            return NEGADO, {}, None, motivo

        ctx["resultado"] = resultado_vars
        erro = self._emitir_saida(row, d, ctx, exec_id, chave, janela, ator)
        if erro:
            return FALHOU, {"acao": status}, operacao_id, erro
        return status, {"chaves": sorted(resultado_vars)[:20]}, operacao_id, None

    def _negar_se_sem_permissao(
        self, d: dict[str, Any], skill: str, ator: UserProfile
    ) -> str | None:
        if skill not in d["permissoes"]:
            return f"a skill {skill} não está nas permissões declaradas pelo gatilho"
        if not is_tool_allowed(ator.role, skill, self._e.user_store, self._e.rbac_default):
            return f"o RBAC não concede {skill} ao perfil {ator.role.value}"
        return None

    def _operacao_existente(self, row: GatilhoRow, d: dict[str, Any], exec_id: int | None) -> Any:
        with Session(self._db) as s:
            iniciado = s.scalar(
                select(GatilhoExecucaoRow.iniciado_em).where(GatilhoExecucaoRow.id == exec_id)
            )
        ator = f"{IDENTIDADE_SISTEMA}{row.config_id}"
        for op in self._e.operacoes.listar():
            if (
                op.proposto_por == ator
                and op.skill == d["acao"]["skill"]
                and iniciado is not None
                and op.criado_em >= _aware(iniciado) - timedelta(hours=2)
                and not self._operacao_adotada(op.id)
            ):
                return op
        return None

    def _operacao_adotada(self, operacao_id: str) -> bool:
        with Session(self._db) as s:
            return (
                s.scalar(
                    select(func.count())
                    .select_from(GatilhoExecucaoRow)
                    .where(GatilhoExecucaoRow.operacao_id == operacao_id)
                )
                or 0
            ) > 0

    # ------------------------------------------------------------------ saídas

    def _emitir_saida(
        self, row: GatilhoRow, d: dict[str, Any], ctx: dict[str, Any], exec_id: int | None,
        chave: str, janela: str, ator: UserProfile,
    ) -> str | None:  # fmt: skip
        saida = d["saida"]
        try:
            mensagem = str(renderizar(saida["mensagem"], ctx, estrito=False))
        except ValueError as exc:
            return str(exc)
        if saida["tipo"] == "alerta":
            self._alertar(d, saida["nivel"], mensagem, exec_id)
            return None
        if saida["tipo"] == "nota":
            return self._escrever_nota(d, mensagem, janela)
        skill = gatilhos_tipo.nome_skill_email(d)
        negado = self._negar_se_sem_permissao(d, skill, ator)
        if negado:
            return negado
        assunto = str(renderizar(saida.get("assunto") or d["descricao"], ctx, estrito=False))
        resposta = self._e._call_skill(
            skill,
            {"destinatario": d["destinatario"], "assunto": assunto, "corpo": mensagem},
            idempotency_key=f"{chave}:email",
            _sistema=True,
        )
        if resposta.get("allowed") or resposta.get("operacao_id"):
            return None
        return f"e-mail não enviado: {resposta.get('reason')}"

    def _checar_dlp(self, texto: str) -> str:
        if scan_response(texto, self._e._client_config).allowed:
            return texto
        return "(mensagem omitida: bloqueada pelo DLP do cliente)"

    def _alertar(self, d: dict[str, Any], nivel: str, texto: str, exec_id: int | None) -> None:
        with Session(self._db) as s:
            s.add(
                GatilhoAlertaRow(
                    gatilho_id=d["id"], execucao_id=exec_id, nivel=nivel,
                    texto=self._checar_dlp(texto)[:600], destinatario=d["destinatario"],
                    criado_em=_naive(datetime.now(UTC)),
                )
            )  # fmt: skip
            s.commit()
        try:
            from mcp_engine.core import config_notas_paginas as paginas

            paginas.atualizar_alertas(self._e.caderno)
        except Exception:
            logger.exception("falha ao atualizar sistema/alertas.md")

    def _escrever_nota(self, d: dict[str, Any], mensagem: str, janela: str) -> str | None:
        rel = f"relatorios/gatilhos/{d['id']}/{_SLUG.sub('-', janela)}.md"
        try:
            self._e.escritor.escrever(
                rel, d["descricao"], self._checar_dlp(mensagem),
                {"tipo": "relatorio", "zona": "relatorios", "gerado": True, "gatilho": d["id"]},
            )  # fmt: skip
        except Exception as exc:
            return f"nota não escrita: {type(exc).__name__}"
        return None

    # ------------------------------------------------------------------ conclusão e disjuntor

    def _concluir(
        self, row: GatilhoRow, d: dict[str, Any], exec_id: int | None, status: str,
        resultado: dict[str, Any], erro: str | None, operacao_id: str | None, agora: datetime,
    ) -> None:  # fmt: skip
        with Session(self._db) as s:
            s.execute(
                update(GatilhoExecucaoRow)
                .where(GatilhoExecucaoRow.id == exec_id)
                .values(
                    status=status, concluido_em=_naive(datetime.now(UTC)), erro=erro,
                    resultado=json.dumps(resultado, sort_keys=True, default=str)[:2000],
                    operacao_id=operacao_id,
                )
            )  # fmt: skip
            g = s.get(GatilhoRow, row.config_id)
            seguidas = 0
            if g is not None:
                g.ultima_execucao = _naive(agora)
                if status in FALHAS:
                    g.falhas_seguidas += 1
                elif status in SAUDAVEIS:
                    g.falhas_seguidas = 0
                seguidas = g.falhas_seguidas
            s.commit()
        if status in FALHAS and seguidas >= d["limites"]["falhas_seguidas"]:
            self.desativar(row.config_id, f"disjuntor: {seguidas} falhas seguidas")

    def _checar_tempestade(self, row: GatilhoRow, d: dict[str, Any]) -> None:
        recentes = self._contar_execucoes(
            row.config_id, REJEICOES, self.relogio() - timedelta(hours=1)
        )
        if recentes + 1 >= d["limites"]["rejeicoes_por_hora"]:
            self.desativar(row.config_id, f"disjuntor: {recentes + 1} disparos rejeitados na hora")

    def desativar(self, config_id: str, motivo: str) -> None:
        row = self._get(config_id)
        if row is None or not row.ativo:
            return
        with Session(self._db) as s:
            s.execute(
                update(GatilhoRow)
                .where(GatilhoRow.config_id == config_id)
                .values(ativo=False, motivo_desativacao=motivo)
            )
            s.commit()
        d = json.loads(row.definicao)
        self._alertar(d, "critico", f"Gatilho {config_id} desativado ({motivo}).", None)
        self._auditar_sistema(
            "gatilho:desativado", AuditDecision.DENIED, gatilho_id=config_id, motivo=motivo,
            aprovado_por=row.aprovado_por, hash=row.hash,
        )  # fmt: skip

    def reativar(self, config_id: str, por: str) -> bool:
        row = self._get(config_id)
        if row is None:
            return False
        with Session(self._db) as s:
            s.execute(
                update(GatilhoRow)
                .where(GatilhoRow.config_id == config_id)
                .values(ativo=True, motivo_desativacao=None, falhas_seguidas=0,
                        proxima_execucao=None)
            )  # fmt: skip
            s.commit()
        self._auditar_sistema("gatilho:reativado", AuditDecision.ALLOWED, ator=por,
                              gatilho_id=config_id, por=por)  # fmt: skip
        return True

    def reprocessar(self, config_id: str, janela: str, por: str) -> dict[str, Any]:
        """Executa de novo uma janela já registrada (decisão explícita do operador)."""
        with Session(self._db) as s:
            n = (
                s.scalar(
                    select(func.count())
                    .select_from(GatilhoExecucaoRow)
                    .where(
                        GatilhoExecucaoRow.gatilho_id == config_id,
                        GatilhoExecucaoRow.janela == janela,
                    )
                )
                or 0
            )
        if n == 0:
            return {"status": "janela_desconhecida"}
        self._auditar_sistema("gatilho:reprocesso", AuditDecision.ALLOWED, ator=por,
                              gatilho_id=config_id, janela=janela, por=por)  # fmt: skip
        return self.executar(config_id, janela=janela, origem="reprocesso", sufixo=f"#r{n}")

    def reconhecer_alerta(self, alerta_id: int, por: str) -> bool:
        with Session(self._db) as s:
            resultado = s.execute(
                update(GatilhoAlertaRow)
                .where(GatilhoAlertaRow.id == alerta_id, GatilhoAlertaRow.reconhecido_em.is_(None))
                .values(reconhecido_por=por, reconhecido_em=_naive(datetime.now(UTC)))
            )
            s.commit()
            return resultado.rowcount == 1

    def _auditar_execucao(
        self, row: GatilhoRow, d: dict[str, Any], chave: str, status: str,
        evento: EventoRow | None, operacao_id: str | None, erro: str | None,
    ) -> None:  # fmt: skip
        decisao = (
            AuditDecision.DENIED
            if status in (*FALHAS, CORTADO, LIMITADO)
            else (AuditDecision.ALLOWED)
        )
        self._e._audit_sink.record(
            AuditEvent(
                client_id=self._e.client_id,
                occurred_at=datetime.now(UTC),
                actor=f"{IDENTIDADE_SISTEMA}{row.config_id}",
                action=f"gatilho:{status}",
                decision=decisao,
                details={
                    "gatilho_id": row.config_id, "chave": chave, "versao": row.versao,
                    "nota": row.caminho, "hash_aprovado": row.hash,
                    "aprovado_por": row.aprovado_por, "skill": d["acao"]["skill"],
                    "evento_id": evento.id if evento else None, "operacao_id": operacao_id,
                    "erro": erro,
                },
            )
        )  # fmt: skip

    # ------------------------------------------------------------------ simulação e painel

    def simular(
        self, *, config_id: str | None = None, dados: dict[str, Any] | None = None,
        evento: dict[str, Any] | None = None,
    ) -> dict[str, Any]:  # fmt: skip
        """O que o gatilho faria agora, sem nenhum efeito (nem alerta, nem operação)."""
        if dados is None:
            row = self._get(config_id or "")
            if row is None:
                return {"allowed": False, "reason": "gatilho não encontrado"}
            dados = json.loads(row.definicao)
        d = dados
        agora = self.relogio()
        ator = UserProfile(identity=f"{IDENTIDADE_SISTEMA}{d['id']}", role=Role(d["papel"]))
        ev = None
        if evento is not None:
            ev = EventoRow(id=0, tipo=d["quando"].get("evento") or "simulado.evento",
                           payload=json.dumps(evento), criado_em=_naive(agora),
                           profundidade=0)  # fmt: skip
        ctx = self._contexto(d, "simulacao", ev, agora)
        saida: dict[str, Any] = {"allowed": True, "gatilho": d["id"], "ator": ator.identity,
                                 "efeito": "nenhum"}  # fmt: skip
        token = set_current_user(ator)
        try:
            try:
                fatos = self._fatos(d, ctx, ator)
            except FatosIndisponiveis as exc:
                return {**saida, "decisao": "fatos_indisponiveis", "detalhe": str(exc)}
            ctx.update(fatos)
            saida["fatos"] = fatos
            condicao = d.get("condicao")
            verdadeira = True if condicao is None else bool(jsonLogic(condicao, ctx))
            saida["condicao"] = verdadeira
            if not verdadeira:
                return {**saida, "decisao": "nao_dispararia"}
            try:
                argumentos = renderizar(d["acao"]["argumentos"], ctx)
            except ValueError as exc:
                return {**saida, "decisao": "modelo_invalido", "detalhe": str(exc)}
            saida["argumentos"] = argumentos
            negado = self._negar_se_sem_permissao(d, d["acao"]["skill"], ator)
            if negado:
                return {**saida, "decisao": "negado", "detalhe": negado}
            resposta = self._e._call_skill(
                d["acao"]["skill"], argumentos, simulacao=True, _sistema=True
            )
            saida["decisao"], saida["detalhe"] = self._ler_simulacao(resposta)
            saida["saida"] = {
                "tipo": d["saida"]["tipo"], "destinatario": d["destinatario"],
                "mensagem": str(renderizar(d["saida"]["mensagem"], ctx, estrito=False)),
            }  # fmt: skip
            return saida
        finally:
            current_user_var.reset(token)

    @staticmethod
    def _ler_simulacao(resposta: dict[str, Any]) -> tuple[str, str | None]:
        if resposta.get("allowed"):
            try:
                exigido = json.loads(resposta.get("result") or "{}").get("exigiria")
            except ValueError:
                exigido = None
            if exigido in ("A2", "A3"):
                return "criaria_operacao_proposta", f"a skill exige {exigido}: aprovação de pessoa"
            return "executaria", None
        if resposta.get("reason") == "human approval required":
            return "criaria_operacao_proposta", f"regra {resposta.get('rule_id')} exige aprovação"
        return "negado", str(resposta.get("reason"))

    def painel(self, limite: int = 20) -> dict[str, Any]:
        agora = _naive(self.relogio())
        with Session(self._db) as s:
            execs = list(
                s.scalars(select(GatilhoExecucaoRow).order_by(GatilhoExecucaoRow.id.desc())
                          .limit(limite))
            )  # fmt: skip
            falhas = list(
                s.scalars(
                    select(GatilhoExecucaoRow)
                    .where(GatilhoExecucaoRow.status.in_((*FALHAS, CORTADO, PERDIDA)))
                    .order_by(GatilhoExecucaoRow.id.desc())
                    .limit(limite)
                )
            )
            alertas = list(
                s.scalars(select(GatilhoAlertaRow).order_by(GatilhoAlertaRow.id.desc())
                          .limit(limite))
            )  # fmt: skip
            pendentes = s.scalar(
                select(func.count()).select_from(EventoRow).where(EventoRow.processado_em.is_(None))
            )
            ultimas: dict[str, GatilhoExecucaoRow] = {}
            for g in s.scalars(select(GatilhoRow)):
                ultima = s.scalar(
                    select(GatilhoExecucaoRow)
                    .where(GatilhoExecucaoRow.gatilho_id == g.config_id)
                    .order_by(GatilhoExecucaoRow.id.desc())
                    .limit(1)
                )
                if ultima is not None:
                    ultimas[g.config_id] = ultima
            ativos = []
            for g in s.scalars(select(GatilhoRow).order_by(GatilhoRow.config_id)):
                d = json.loads(g.definicao)
                ultima = ultimas.get(g.config_id)
                ativos.append(
                    {
                        "id": g.config_id, "descricao": d["descricao"], "quando": d["quando"],
                        "ativo": g.ativo, "motivo_desativacao": g.motivo_desativacao,
                        "autonomia": d["autonomia"], "skill": d["acao"]["skill"],
                        "destinatario": d["destinatario"], "limites": d["limites"],
                        "aprovado_por": g.aprovado_por, "versao": g.versao, "hash": g.hash,
                        "proxima_execucao": _iso(g.proxima_execucao),
                        "ultima_execucao": _iso(g.ultima_execucao),
                        "ultimo_status": ultima.status if ultima else None,
                        "falhas_seguidas": g.falhas_seguidas,
                        "atrasado": bool(
                            g.ativo and g.proxima_execucao is not None
                            and agora - g.proxima_execucao > ATRASO_ALERTA
                        ),
                    }
                )  # fmt: skip
        batimento = self._controle(BATIMENTO)
        return {
            "habilitado": self.habilitado,
            "pausado": self.pausado(),
            "agendador_batimento": batimento,
            "agendador_ativo": bool(
                batimento
                and self.relogio() - _aware(datetime.fromisoformat(batimento)) < ATRASO_ALERTA
            ),
            "eventos_pendentes": pendentes or 0,
            "gatilhos": ativos,
            "ultimas_execucoes": [_exec(e) for e in execs],
            "falhas": [_exec(e) for e in falhas],
            "alertas": [
                {
                    "id": a.id, "gatilho": a.gatilho_id, "nivel": a.nivel, "texto": a.texto,
                    "destinatario": a.destinatario, "criado_em": _iso(a.criado_em),
                    "reconhecido_por": a.reconhecido_por, "reconhecido_em": _iso(a.reconhecido_em),
                }
                for a in alertas
            ],
        }  # fmt: skip

    def alertas_recentes(self, limite: int = 50) -> list[GatilhoAlertaRow]:
        with Session(self._db) as s:
            rows = list(
                s.scalars(select(GatilhoAlertaRow).order_by(GatilhoAlertaRow.id.desc())
                          .limit(limite))
            )  # fmt: skip
            for r in rows:
                s.expunge(r)
            return rows

    def atrasados(self) -> list[str]:
        agora = _naive(self.relogio())
        return [
            g.config_id
            for g in self.listar_ativos()
            if g.ativo and g.proxima_execucao is not None
            and agora - g.proxima_execucao > ATRASO_ALERTA
        ]  # fmt: skip

    def limpar_antigos(self, dias: int = 30) -> int:
        corte = _naive(self.relogio()) - timedelta(days=dias)
        with Session(self._db) as s:
            r = s.execute(
                delete(EventoRow).where(EventoRow.processado_em.is_not(None),
                                        EventoRow.processado_em < corte)
            )  # fmt: skip
            s.commit()
            return r.rowcount


def _iso(momento: datetime | None) -> str | None:
    return _aware(momento).isoformat() if momento else None


def _exec(e: GatilhoExecucaoRow) -> dict[str, Any]:
    return {
        "id": e.id,
        "gatilho": e.gatilho_id,
        "janela": e.janela,
        "origem": e.origem,
        "status": e.status,
        "iniciado_em": _iso(e.iniciado_em),
        "concluido_em": _iso(e.concluido_em),
        "erro": e.erro,
        "operacao_id": e.operacao_id,
        "trace_id": e.trace_id,
        "profundidade": e.profundidade,
        "hash_definicao": e.hash_definicao[:12],
    }
