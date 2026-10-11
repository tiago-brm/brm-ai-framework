"""Sessões, faltas, pacotes, cobrança prevista e pendências operacionais."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from typing import Any

from mcp_engine.modules.financeiro.db import ErroFinanceiro, agora_iso
from mcp_engine.modules.financeiro.lancamentos import Quem
from mcp_engine.modules.financeiro.money import formatar_brl

FALTAS_QUE_CONTAM = ("falta", "cancelamento_tardio")


def criar_pendencia(
    con: sqlite3.Connection,
    tipo: str,
    motivo: str,
    *,
    referencia: str | None = None,
    sugestao: str | None = None,
    detalhe: dict[str, Any] | None = None,
    lote_id: int | None = None,
    aba: str | None = None,
    linha: int | None = None,
) -> int:
    if referencia is not None:
        existente = con.execute(
            "SELECT id FROM pendencia_operacional WHERE tipo = ? AND referencia = ?"
            " AND status = 'aberta'",
            (tipo, referencia),
        ).fetchone()
        if existente:
            return existente["id"]
    return con.execute(
        "INSERT INTO pendencia_operacional (tipo, referencia, lote_id, aba, linha, motivo,"
        " sugestao, detalhe, criada_em) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            tipo,
            referencia,
            lote_id,
            aba,
            linha,
            motivo,
            sugestao,
            json.dumps(detalhe, ensure_ascii=False, default=str) if detalhe else None,
            agora_iso(),
        ),
    ).lastrowid


def encerrar_pendencia(
    con: sqlite3.Connection, pendencia_id: int, status: str, resolucao: str, quem: Quem
) -> None:
    cur = con.execute(
        "UPDATE pendencia_operacional SET status = ?, resolucao = ?, resolvida_por = ?,"
        " resolvida_em = ? WHERE id = ? AND status = 'aberta'",
        (status, resolucao, quem.actor, agora_iso(), pendencia_id),
    )
    if cur.rowcount != 1:
        raise ErroFinanceiro("Pendência não encontrada ou já encerrada.")


def consumidas(con: sqlite3.Connection, pacote_id: int) -> int:
    return con.execute(
        "SELECT COUNT(*) FROM sessao s JOIN pacote p ON p.id = s.pacote_id"
        " JOIN modelo_pacote m ON m.id = p.modelo_id"
        " WHERE s.pacote_id = ? AND (s.status_agenda = 'COMPLETED' OR (m.falta_consome = 1"
        " AND EXISTS (SELECT 1 FROM ocorrencia_efetiva o WHERE o.sessao_id = s.id"
        " AND o.tipo IN ('falta', 'cancelamento_tardio'))))",
        (pacote_id,),
    ).fetchone()[0]


def _garantir_limite_pacote(con: sqlite3.Connection, sessao_id: int) -> None:
    row = con.execute("SELECT pacote_id FROM sessao WHERE id = ?", (sessao_id,)).fetchone()
    if row is None or row["pacote_id"] is None:
        return
    total = con.execute(
        "SELECT sessoes_totais FROM pacote WHERE id = ?", (row["pacote_id"],)
    ).fetchone()[0]
    if consumidas(con, row["pacote_id"]) > total:
        con.execute("UPDATE sessao SET pacote_id = NULL WHERE id = ?", (sessao_id,))
        criar_pendencia(
            con,
            "pacote_excedido",
            "A sessão ultrapassa o total de sessões do pacote e ficou fora dele.",
            referencia=f"sessao:{sessao_id}",
            sugestao="Registre um novo pacote ou cobre a sessão avulsa.",
        )


def upsert_sessao(
    con: sqlite3.Connection,
    *,
    agenda_id: str | None,
    paciente_id: int,
    inicio_utc: str,
    status_agenda: str = "SCHEDULED",
    profissional_id: int | None = None,
    servico: str | None = None,
    pacote_id: int | None = None,
    valor_previsto_centavos: int | None = None,
    valor_previsto_origem: str | None = None,
) -> int:
    inicio_utc = normalizar_utc(inicio_utc)
    row = (
        con.execute("SELECT id, pacote_id FROM sessao WHERE agenda_id = ?", (agenda_id,)).fetchone()
        if agenda_id
        else None
    )
    if row:
        sid = row["id"]
        con.execute(
            "UPDATE sessao SET paciente_id = ?, profissional_id = ?, servico = ?, inicio_utc = ?,"
            " status_agenda = ?, valor_previsto_centavos = ?, valor_previsto_origem = ?,"
            " pacote_id = COALESCE(?, pacote_id), sincronizado_em = ? WHERE id = ?",
            (
                paciente_id,
                profissional_id,
                servico,
                inicio_utc,
                status_agenda,
                valor_previsto_centavos,
                valor_previsto_origem,
                pacote_id,
                agora_iso(),
                sid,
            ),
        )
    else:
        sid = con.execute(
            "INSERT INTO sessao (agenda_id, paciente_id, profissional_id, servico, inicio_utc,"
            " status_agenda, valor_previsto_centavos, valor_previsto_origem, pacote_id,"
            " sincronizado_em) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                agenda_id,
                paciente_id,
                profissional_id,
                servico,
                inicio_utc,
                status_agenda,
                valor_previsto_centavos,
                valor_previsto_origem,
                pacote_id,
                agora_iso(),
            ),
        ).lastrowid
    _garantir_limite_pacote(con, sid)
    if (
        status_agenda == "COMPLETED"
        and con.execute(
            "SELECT 1 FROM ocorrencia_efetiva WHERE sessao_id = ? AND tipo IN ('falta',"
            " 'cancelamento_tardio')",
            (sid,),
        ).fetchone()
    ):
        _conflito_desfecho(con, sid)
    return sid


def _conflito_desfecho(con: sqlite3.Connection, sessao_id: int) -> None:
    criar_pendencia(
        con,
        "conflito_desfecho",
        "A sessão consta como concluída na Agenda e também tem falta registrada.",
        referencia=f"sessao:{sessao_id}",
        sugestao="O gestor decide: manter_realizada (estorna a falta) ou corrigir a Agenda.",
    )


def localizar_sessao(
    con: sqlite3.Connection, agenda_id: str | None, paciente_id: int | None, dia: date | None
) -> sqlite3.Row:
    if agenda_id:
        row = con.execute("SELECT * FROM sessao WHERE agenda_id = ?", (agenda_id,)).fetchone()
    elif paciente_id is not None and dia is not None:
        rows = con.execute(
            "SELECT * FROM sessao WHERE paciente_id = ? AND substr(inicio_utc,1,10) = ?",
            (paciente_id, dia.isoformat()),
        ).fetchall()
        if len(rows) > 1:
            raise ErroFinanceiro("Mais de uma sessão nesse dia: informe o agenda_id.")
        row = rows[0] if rows else None
    else:
        raise ErroFinanceiro("Informe agenda_id ou paciente e data da sessão.")
    if row is None:
        raise ErroFinanceiro(
            "Sessão não encontrada: ela vem da Agenda (sincronize antes de registrar a falta)."
        )
    return row


def registrar_falta(
    con: sqlite3.Connection,
    sessao_id: int,
    tipo: str,
    motivo: str | None,
    quem: Quem,
    *,
    operacao_id: str | None = None,
) -> tuple[int, bool]:
    if operacao_id:
        existente = con.execute(
            "SELECT id FROM sessao_ocorrencia WHERE operacao_id = ?", (operacao_id,)
        ).fetchone()
        if existente:
            return existente["id"], True
    if tipo not in ("falta", "falta_justificada", "cancelamento_tardio"):
        raise ErroFinanceiro("Tipo de ocorrência inválido.")
    sessao = con.execute("SELECT * FROM sessao WHERE id = ?", (sessao_id,)).fetchone()
    if sessao is None:
        raise ErroFinanceiro("Sessão não encontrada.")
    if con.execute("SELECT 1 FROM ocorrencia_efetiva WHERE sessao_id = ?", (sessao_id,)).fetchone():
        raise ErroFinanceiro("Esta sessão já tem uma ocorrência registrada.")
    oid = con.execute(
        "INSERT INTO sessao_ocorrencia (sessao_id, tipo, motivo, registrado_por, registrado_em,"
        " operacao_id) VALUES (?,?,?,?,?,?)",
        (sessao_id, tipo, motivo, quem.actor, agora_iso(), operacao_id),
    ).lastrowid
    if sessao["status_agenda"] == "COMPLETED" and tipo in FALTAS_QUE_CONTAM:
        _conflito_desfecho(con, sessao_id)
    _garantir_limite_pacote(con, sessao_id)
    return oid, False


def estornar_ocorrencia(con: sqlite3.Connection, sessao_id: int, quem: Quem) -> None:
    efetiva = con.execute(
        "SELECT * FROM ocorrencia_efetiva WHERE sessao_id = ?", (sessao_id,)
    ).fetchone()
    if efetiva is None:
        raise ErroFinanceiro("Não há ocorrência a estornar nesta sessão.")
    con.execute(
        "INSERT INTO sessao_ocorrencia (sessao_id, tipo, motivo, registrado_por, registrado_em,"
        " estorno_de) VALUES (?,?,?,?,?,?)",
        (
            sessao_id,
            efetiva["tipo"],
            "estorno de ocorrência",
            quem.actor,
            agora_iso(),
            efetiva["id"],
        ),
    )


def normalizar_utc(valor: str) -> str:
    try:
        dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        raise ErroFinanceiro(f"Data e hora inválidas: {valor!r}.") from None
    if dt.tzinfo is None:
        raise ErroFinanceiro("Data e hora da sessão exigem fuso horário.")
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def contar_sessoes_sem_desfecho(con: sqlite3.Connection, agora: datetime | None = None) -> int:
    limite = (agora or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return con.execute(
        "SELECT COUNT(*) FROM sessao s WHERE status_agenda = 'SCHEDULED' AND inicio_utc < ?"
        " AND NOT EXISTS (SELECT 1 FROM ocorrencia_efetiva o WHERE o.sessao_id = s.id)",
        (limite,),
    ).fetchone()[0]


def gerar_pendencias_sem_desfecho(con: sqlite3.Connection, agora: datetime | None = None) -> int:
    limite = (agora or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    criadas = 0
    for s in con.execute(
        "SELECT id FROM sessao s WHERE status_agenda = 'SCHEDULED' AND inicio_utc < ?"
        " AND NOT EXISTS (SELECT 1 FROM ocorrencia_efetiva o WHERE o.sessao_id = s.id)",
        (limite,),
    ).fetchall():
        ref = f"sessao:{s['id']}"
        if con.execute(
            "SELECT 1 FROM pendencia_operacional WHERE tipo = 'sessao_sem_desfecho'"
            " AND referencia = ? AND status = 'aberta'",
            (ref,),
        ).fetchone():
            continue
        criar_pendencia(
            con,
            "sessao_sem_desfecho",
            "Sessão passada sem desfecho: marque como concluída na Agenda ou registre a falta"
            " aqui.",
            referencia=ref,
        )
        criadas += 1
    return criadas


def modelo_vigente(con: sqlite3.Connection, nome: str, em: date) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM modelo_pacote WHERE nome = ? AND vigente_de <= ?"
        " AND (vigente_ate IS NULL OR vigente_ate >= ?)",
        (nome.strip(), em.isoformat(), em.isoformat()),
    ).fetchone()
    if row is None:
        raise ErroFinanceiro(f"Modelo de pacote {nome!r} não está vigente em {em.isoformat()}.")
    return row


def registrar_pacote(
    con: sqlite3.Connection,
    *,
    paciente_id: int,
    modelo_nome: str,
    inicio: date,
    quem: Quem,
    preco_acordado: int | None = None,
    sessoes_totais: int | None = None,
    operacao_id: str | None = None,
) -> dict[str, Any]:
    if operacao_id:
        existente = con.execute(
            "SELECT id FROM pacote WHERE operacao_id = ?", (operacao_id,)
        ).fetchone()
        if existente:
            return {"pacote_id": existente["id"], "reutilizado": True, "avisos": []}
    m = modelo_vigente(con, modelo_nome, inicio)
    modo = m["modo_cobranca"]
    n_parcelas = m["n_parcelas"] if modo in ("parcelado", "mensal") else 1
    sessoes = sessoes_totais or (m["n_sessoes"] * (n_parcelas if modo == "mensal" else 1))
    unitario = m["preco_unitario_centavos"]
    if preco_acordado is not None:
        preco = preco_acordado
    elif modo == "mensal":
        preco = (m["preco_total_centavos"] or unitario * m["n_sessoes"]) * n_parcelas
    else:
        preco = m["preco_total_centavos"] or unitario * sessoes
    if preco <= 0 or sessoes < 1:
        raise ErroFinanceiro("Preço e número de sessões do pacote devem ser positivos.")
    validade = inicio + timedelta(days=m["validade_dias"])
    pid = con.execute(
        "INSERT INTO pacote (paciente_id, modelo_id, preco_acordado_centavos, sessoes_totais,"
        " inicio, validade, operacao_id, criado_por, criado_em) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            paciente_id,
            m["id"],
            preco,
            sessoes,
            inicio.isoformat(),
            validade.isoformat(),
            operacao_id,
            quem.actor,
            agora_iso(),
        ),
    ).lastrowid
    avisos = []
    paciente = con.execute(
        "SELECT valor_sessao_centavos FROM paciente_ref WHERE id = ?", (paciente_id,)
    ).fetchone()
    unit_efetivo = preco // sessoes
    if paciente["valor_sessao_centavos"] and paciente["valor_sessao_centavos"] != unit_efetivo:
        avisos.append(
            f"Valor por sessão do pacote ({formatar_brl(unit_efetivo)}) difere do valor acordado "
            f"do paciente ({formatar_brl(paciente['valor_sessao_centavos'])})."
        )
    for venc, valor, origem in _cobrancas_iniciais(modo, preco, inicio, m, n_parcelas):
        con.execute(
            "INSERT INTO cobranca_prevista (pacote_id, vencimento, valor_centavos, origem)"
            " VALUES (?,?,?,?)",
            (pid, venc.isoformat(), valor, origem),
        )
    return {"pacote_id": pid, "reutilizado": False, "avisos": avisos, "preco_centavos": preco}


def _cobrancas_iniciais(
    modo: str, preco: int, inicio: date, m: sqlite3.Row, n: int
) -> list[tuple[date, int, str]]:
    if modo == "antecipado":
        return [(inicio, preco, "inicio")]
    if modo == "por_sessao":
        return []
    base, resto = divmod(preco, n)
    passo = m["intervalo_dias"] if modo == "parcelado" else 30
    origem = "parcela" if modo == "parcelado" else "mensal"
    return [
        (inicio + timedelta(days=passo * i), base + (resto if i == n - 1 else 0), origem)
        for i in range(n)
    ]


def gerar_cobrancas_por_sessao(con: sqlite3.Connection, pacote_id: int) -> int:
    pacote = con.execute(
        "SELECT p.*, m.modo_cobranca, m.falta_cobrada FROM pacote p"
        " JOIN modelo_pacote m ON m.id = p.modelo_id WHERE p.id = ?",
        (pacote_id,),
    ).fetchone()
    if pacote is None or pacote["modo_cobranca"] != "por_sessao":
        return 0
    unit = pacote["preco_acordado_centavos"] // pacote["sessoes_totais"]
    feitas = con.execute(
        "SELECT COUNT(*) FROM cobranca_prevista WHERE pacote_id = ? AND origem = 'sessao'",
        (pacote_id,),
    ).fetchone()[0]
    criadas = 0
    cobraveis = con.execute(
        "SELECT s.id, substr(s.inicio_utc,1,10) dia FROM sessao s WHERE s.pacote_id = ?"
        " AND NOT EXISTS (SELECT 1 FROM cobranca_prevista c WHERE c.sessao_id = s.id)"
        " AND (s.status_agenda = 'COMPLETED' OR (? = 1 AND EXISTS (SELECT 1 FROM ocorrencia_efetiva"
        " o WHERE o.sessao_id = s.id AND o.tipo IN ('falta', 'cancelamento_tardio'))))"
        " ORDER BY s.inicio_utc, s.id",
        (pacote_id, pacote["falta_cobrada"]),
    ).fetchall()
    for s in cobraveis:
        k = feitas + criadas + 1
        if k > pacote["sessoes_totais"]:
            break
        valor = (
            pacote["preco_acordado_centavos"] - unit * (pacote["sessoes_totais"] - 1)
            if k == pacote["sessoes_totais"]
            else unit
        )
        con.execute(
            "INSERT INTO cobranca_prevista (pacote_id, sessao_id, vencimento, valor_centavos,"
            " origem) VALUES (?,?,?,?, 'sessao')",
            (pacote_id, s["id"], s["dia"], valor),
        )
        criadas += 1
    return criadas
