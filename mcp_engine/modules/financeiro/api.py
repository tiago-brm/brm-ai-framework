"""API pública do financeiro para outros módulos (spec 026).

O módulo consumidor nunca abre o banco do ledger: recebe uma transação e uma fachada com as
operações permitidas, ou consultas de leitura prontas.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from mcp_engine.modules.financeiro import sessoes
from mcp_engine.modules.financeiro.db import (
    FUSO,
    ErroFinanceiro,
    abrir,
    caminho_ledger,
    normalizar_nome,
    transacao,
)
from mcp_engine.modules.financeiro.lancamentos import Quem
from mcp_engine.modules.financeiro.params import garantir_profissional

SISTEMA = Quem(actor="sincronizador-agenda", role="sistema")
_FORMATO_UTC = "%Y-%m-%dT%H:%M:%SZ"


class LedgerAgenda:
    """Operações do ledger que o sincronizador da Agenda pode fazer, dentro de uma transação."""

    def __init__(self, con: sqlite3.Connection) -> None:
        self._con = con

    def paciente_da_agenda(self, customer_id: str, nome: str) -> int:
        """O paciente nasce na Agenda: cria sob demanda por `external_ref` e atualiza o nome."""
        nome = re.sub(r"\s+", " ", nome).strip()[:160]
        norm = normalizar_nome(nome)
        if not norm:
            raise ErroFinanceiro("Cliente da Agenda sem nome.")
        con = self._con
        row = con.execute(
            "SELECT id, nome FROM paciente_ref WHERE external_ref = ?", (customer_id,)
        ).fetchone()
        if row:
            if row["nome"] != nome:
                con.execute(
                    "UPDATE paciente_ref SET nome = ?, nome_norm = ? WHERE id = ?",
                    (nome, norm, row["id"]),
                )
            return row["id"]
        adotado = con.execute(
            "SELECT id FROM paciente_ref WHERE nome_norm = ? AND external_ref IS NULL"
            " ORDER BY id LIMIT 1",
            (norm,),
        ).fetchone()
        if adotado:
            con.execute(
                "UPDATE paciente_ref SET external_ref = ?, nome = ?, status = 'ativo' WHERE id = ?",
                (customer_id, nome, adotado["id"]),
            )
            return adotado["id"]
        return con.execute(
            "INSERT INTO paciente_ref (nome, nome_norm, external_ref, status)"
            " VALUES (?, ?, ?, 'ativo')",
            (nome, norm, customer_id),
        ).lastrowid

    def profissional_da_agenda(self, staff_id: str | None, nome: str) -> int:
        return garantir_profissional(self._con, nome, external_ref=staff_id)

    def upsert_sessao(
        self,
        *,
        agenda_id: str,
        paciente_id: int,
        inicio_utc: str,
        status_agenda: str,
        profissional_id: int | None,
        servico: str | None,
        preco_servico_centavos: int | None,
    ) -> tuple[int, int | None]:
        con = self._con
        atual = con.execute(
            "SELECT pacote_id FROM sessao WHERE agenda_id = ?", (agenda_id,)
        ).fetchone()
        valor, origem = self._valor_previsto(
            paciente_id, atual["pacote_id"] if atual else None, preco_servico_centavos
        )
        sid = sessoes.upsert_sessao(
            con,
            agenda_id=agenda_id,
            paciente_id=paciente_id,
            inicio_utc=inicio_utc,
            status_agenda=status_agenda,
            profissional_id=profissional_id,
            servico=servico,
            valor_previsto_centavos=valor,
            valor_previsto_origem=origem,
        )
        pacote = con.execute("SELECT pacote_id FROM sessao WHERE id = ?", (sid,)).fetchone()[0]
        return sid, pacote

    def _valor_previsto(
        self, paciente_id: int, pacote_id: int | None, preco_servico: int | None
    ) -> tuple[int | None, str | None]:
        if pacote_id is not None:
            p = self._con.execute(
                "SELECT preco_acordado_centavos / sessoes_totais FROM pacote WHERE id = ?",
                (pacote_id,),
            ).fetchone()
            if p and p[0]:
                return p[0], "pacote"
        acordado = self._con.execute(
            "SELECT valor_sessao_centavos FROM paciente_ref WHERE id = ?", (paciente_id,)
        ).fetchone()[0]
        if acordado:
            return acordado, "paciente"
        if preco_servico is not None:
            return preco_servico, "servico"
        return None, None

    def pendencia(
        self,
        tipo: str,
        motivo: str,
        *,
        referencia: str,
        sugestao: str | None = None,
        detalhe: dict[str, Any] | None = None,
    ) -> int:
        return sessoes.criar_pendencia(
            self._con, tipo, motivo, referencia=referencia, sugestao=sugestao, detalhe=detalhe
        )

    def gerar_pendencias_sem_desfecho(self, agora: datetime) -> int:
        return sessoes.gerar_pendencias_sem_desfecho(self._con, agora)

    def encerrar_sem_desfecho_resolvidas(self, agora: datetime) -> int:
        limite = agora.astimezone(UTC).strftime(_FORMATO_UTC)
        rows = self._con.execute(
            "SELECT p.id FROM pendencia_operacional p WHERE p.tipo = 'sessao_sem_desfecho'"
            " AND p.status = 'aberta' AND NOT EXISTS (SELECT 1 FROM sessao s"
            " WHERE 'sessao:' || s.id = p.referencia AND s.status_agenda = 'SCHEDULED'"
            " AND s.inicio_utc < ? AND NOT EXISTS (SELECT 1 FROM ocorrencia_efetiva o"
            " WHERE o.sessao_id = s.id))",
            (limite,),
        ).fetchall()
        for r in rows:
            sessoes.encerrar_pendencia(
                self._con,
                r["id"],
                "resolvida",
                "A sessão ganhou desfecho (concluída ou cancelada na Agenda, ou falta registrada).",
                SISTEMA,
            )
        return len(rows)

    def gerar_cobrancas_por_sessao(self, pacote_ids: set[int]) -> int:
        return sum(sessoes.gerar_cobrancas_por_sessao(self._con, p) for p in sorted(pacote_ids))


class FinanceiroAPI:
    def __init__(self, data_root: Path | str, client_id: str) -> None:
        self._caminho = caminho_ledger(data_root, client_id)

    @contextmanager
    def transacao(self) -> Iterator[LedgerAgenda]:
        con = abrir(self._caminho)
        try:
            with transacao(con):
                yield LedgerAgenda(con)
        finally:
            con.close()

    def sessoes_realizadas_sem_pagamento(
        self, de: date, ate: date, fuso: ZoneInfo = FUSO, limite: int = 500
    ) -> list[dict[str, Any]]:
        """Sessões COMPLETED do período (dias no fuso dado) sem recebimento correspondente."""
        inicio = datetime.combine(de, time.min, tzinfo=fuso).astimezone(UTC)
        fim = datetime.combine(ate + timedelta(days=1), time.min, tzinfo=fuso).astimezone(UTC)
        con = abrir(self._caminho)
        try:
            candidatas = con.execute(
                "SELECT s.id, s.agenda_id, s.paciente_id, s.pacote_id, s.inicio_utc, s.servico,"
                " s.valor_previsto_centavos, pa.nome AS paciente, pr.nome AS profissional"
                " FROM sessao s JOIN paciente_ref pa ON pa.id = s.paciente_id"
                " LEFT JOIN profissional pr ON pr.id = s.profissional_id"
                " WHERE s.status_agenda = 'COMPLETED' AND s.inicio_utc >= ? AND s.inicio_utc < ?"
                " ORDER BY s.inicio_utc, s.id",
                (inicio.strftime(_FORMATO_UTC), fim.strftime(_FORMATO_UTC)),
            ).fetchall()
            pagas = {
                (r[0], r[1])
                for r in con.execute(
                    "SELECT r.paciente_id, rs.data_sessao FROM recebimento_sessao rs"
                    " JOIN recebimento r ON r.id = rs.recebimento_id WHERE r.estorno_de IS NULL"
                    " AND NOT EXISTS (SELECT 1 FROM recebimento e WHERE e.estorno_de = r.id)"
                )
            }
            pacotes_pagos = {
                r[0]
                for r in con.execute(
                    "SELECT DISTINCT r.pacote_id FROM recebimento r WHERE r.pacote_id IS NOT NULL"
                    " AND r.estorno_de IS NULL AND NOT EXISTS (SELECT 1 FROM recebimento e"
                    " WHERE e.estorno_de = r.id)"
                )
            }
        finally:
            con.close()
        saida = []
        for s in candidatas:
            local = datetime.strptime(s["inicio_utc"], _FORMATO_UTC).replace(tzinfo=UTC)
            dia = local.astimezone(fuso).date().isoformat()
            if (s["paciente_id"], dia) in pagas or s["pacote_id"] in pacotes_pagos:
                continue
            saida.append(
                {
                    "agenda_id": s["agenda_id"],
                    "data": dia,
                    "paciente": s["paciente"],
                    "profissional": s["profissional"],
                    "servico": s["servico"],
                    "valor_previsto_centavos": s["valor_previsto_centavos"],
                }
            )
            if len(saida) >= limite:
                break
        return saida
