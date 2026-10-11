"""Lançamentos do ledger: recebimentos, despesas, estornos, repasse e fechamento.

Todas as funções assumem uma transação aberta (`db.transacao`) e valores em centavos.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

from mcp_engine.modules.financeiro import params
from mcp_engine.modules.financeiro.db import ErroFinanceiro, agora_iso, mes_de
from mcp_engine.modules.financeiro.money import formatar_brl, meio_para_cima

ATIPICO_PCT = 50
RETROATIVO_MAX_DIAS = 7
PAPEIS_GESTORES = ("admin", "super_admin")


@dataclass(frozen=True)
class Quem:
    actor: str
    role: str
    confirmado_por: str | None = None
    aprovado_por: str | None = None

    @property
    def gestor(self) -> bool:
        return self.role in PAPEIS_GESTORES


def dividir(
    valor: int,
    taxa_bp: int,
    tipo: str,
    base: str,
    valor_bp: int | None,
    valor_centavos: int | None,
) -> tuple[int, int, int, bool]:
    """Devolve (taxa, líquido, repasse, limitado). A sobra de centavos fica com a clínica."""
    taxa = meio_para_cima(valor * taxa_bp, 10000)
    liquido = valor - taxa
    base_calculo = liquido if base == "liquido" else valor
    if tipo == "percentual":
        repasse = meio_para_cima(base_calculo * (valor_bp or 0), 10000)
    else:
        repasse = valor_centavos or 0
    return taxa, liquido, min(repasse, liquido), repasse > liquido


def calcular_repasse(
    con: sqlite3.Connection,
    *,
    valor: int,
    data: date,
    meio_id: int | None,
    forma_id: int,
    parcelas: int,
    profissional_id: int | None,
    unidade_id: int | None,
    servico: str | None,
    tipo: str,
) -> dict[str, Any]:
    taxa_bp = params.taxa_vigente(con, meio_id, forma_id, parcelas, data)
    taxa = meio_para_cima(valor * taxa_bp, 10000)
    liquido = valor - taxa
    snap: dict[str, Any] = {
        "profissional_id": profissional_id,
        "comissao_regra_id": None,
        "base": None,
        "taxa_bp": taxa_bp,
        "taxa_centavos": taxa,
        "liquido_centavos": liquido,
        "repasse_centavos": 0,
        "parte_clinica_centavos": liquido,
        "limitado": 0,
    }
    if profissional_id is None or tipo == "juros":
        return snap
    regra = params.comissao_vigente(con, profissional_id, servico, forma_id, unidade_id, data)
    _, _, repasse, limitado = dividir(
        valor, taxa_bp, regra["tipo"], regra["base"], regra["valor_bp"], regra["valor_centavos"]
    )
    snap.update(
        comissao_regra_id=regra["id"],
        base=regra["base"],
        repasse_centavos=repasse,
        parte_clinica_centavos=liquido - repasse,
        limitado=int(limitado),
    )
    return snap


def _gravar_repasse(con: sqlite3.Connection, recebimento_id: int, snap: dict[str, Any]) -> None:
    con.execute(
        "INSERT INTO repasse (recebimento_id, data_pagamento, profissional_id,"
        " comissao_regra_id, base, taxa_bp,"
        " taxa_centavos, liquido_centavos, repasse_centavos, parte_clinica_centavos, limitado)"
        " VALUES (:rid, :data_pagamento, :profissional_id, :comissao_regra_id, :base, :taxa_bp,"
        " :taxa_centavos,"
        " :liquido_centavos, :repasse_centavos, :parte_clinica_centavos, :limitado)",
        {"rid": recebimento_id, **snap},
    )


def mes_fechado(con: sqlite3.Connection, competencia: date, unidade_id: int | None) -> bool:
    return (
        con.execute(
            "SELECT 1 FROM fechamento_vigente WHERE mes = ? AND status = 'fechado'"
            " AND (unidade_id IS NULL OR unidade_id = ?)",
            (mes_de(competencia), unidade_id),
        ).fetchone()
        is not None
    )


def fatos_recebimento(con: sqlite3.Connection, d: dict[str, Any], hoje: date) -> dict[str, Any]:
    paciente = con.execute(
        "SELECT valor_sessao_centavos, status FROM paciente_ref WHERE id = ?", (d["paciente_id"],)
    ).fetchone()
    esperado = paciente["valor_sessao_centavos"] if paciente else None
    n = max(1, len(d.get("datas_sessoes") or []))
    atipico = bool(
        d["tipo"] == "sessao"
        and esperado
        and abs(d["valor"] - esperado * n) * 100 > esperado * n * ATIPICO_PCT
    )
    duplicidade = (
        con.execute(
            "SELECT 1 FROM recebimento WHERE paciente_id = ? AND valor_centavos = ?"
            " AND data_pagamento = ? AND forma_id = ? LIMIT 1",
            (d["paciente_id"], d["valor"], d["data_pagamento"].isoformat(), d["forma_id"]),
        ).fetchone()
        is not None
    )
    competencia = d.get("data_competencia") or d["data_pagamento"]
    return {
        "valor_atipico": atipico,
        "retroativo_dias": max(0, (hoje - d["data_pagamento"]).days),
        "mes_fechado": mes_fechado(con, competencia, d.get("unidade_id")),
        "duplicidade_suspeita": duplicidade,
        "paciente_a_confirmar": bool(paciente and paciente["status"] == "a_confirmar"),
    }


def fatos_despesa(con: sqlite3.Connection, d: dict[str, Any], hoje: date) -> dict[str, Any]:
    duplicidade = (
        con.execute(
            "SELECT 1 FROM despesa WHERE data = ? AND valor_centavos = ? AND categoria_id = ?"
            " AND descricao = ? LIMIT 1",
            (d["data"].isoformat(), d["valor"], d["categoria_id"], d["descricao"]),
        ).fetchone()
        is not None
    )
    return {
        "retroativo_dias": max(0, (hoje - d["data"]).days),
        "mes_fechado": mes_fechado(con, d.get("data_competencia") or d["data"], None),
        "duplicidade_suspeita": duplicidade,
    }


def inserir_recebimento(
    con: sqlite3.Connection, d: dict[str, Any], quem: Quem, *, hoje: date
) -> tuple[int, bool]:
    """Grava recebimento, sessões e snapshot de repasse. Devolve (id, reutilizado)."""
    if d.get("operacao_id"):
        existente = con.execute(
            "SELECT id FROM recebimento WHERE operacao_id = ?", (d["operacao_id"],)
        ).fetchone()
        if existente:
            return existente["id"], True
    valor = d["valor"]
    if valor <= 0:
        raise ErroFinanceiro("O valor do recebimento deve ser maior que zero.")
    snap = calcular_repasse(
        con,
        valor=valor,
        data=d["data_pagamento"],
        meio_id=d.get("meio_id"),
        forma_id=d["forma_id"],
        parcelas=d.get("parcelas", 1),
        profissional_id=d.get("profissional_id"),
        unidade_id=d.get("unidade_id"),
        servico=d.get("servico"),
        tipo=d["tipo"],
    )
    competencia = d.get("data_competencia") or d["data_pagamento"]
    rid = con.execute(
        "INSERT INTO recebimento (data_pagamento, data_competencia, paciente_id, forma_id,"
        " meio_id, parcelas, valor_centavos, profissional_id, unidade_id, secretaria, servico,"
        " tipo, nf_status, nf_numero, comprovante_ref, observacao, origem, lote_id, operacao_id,"
        " pacote_id, cobranca_prevista_id, criado_por, criado_em)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            d["data_pagamento"].isoformat(),
            competencia.isoformat(),
            d["paciente_id"],
            d["forma_id"],
            d.get("meio_id"),
            d.get("parcelas", 1),
            valor,
            d.get("profissional_id"),
            d.get("unidade_id"),
            d.get("secretaria") or quem.actor,
            d.get("servico"),
            d["tipo"],
            d.get("nf_status", "pendente"),
            d.get("nf_numero"),
            d.get("comprovante_ref"),
            d.get("observacao"),
            d.get("origem", "manual"),
            d.get("lote_id"),
            d.get("operacao_id"),
            d.get("pacote_id"),
            d.get("cobranca_prevista_id"),
            quem.actor,
            agora_iso(),
        ),
    ).lastrowid
    for dia in sorted(set(d.get("datas_sessoes") or [])):
        con.execute(
            "INSERT INTO recebimento_sessao (recebimento_id, data_sessao, sessao_id)"
            " VALUES (?, ?, ?)",
            (rid, dia.isoformat(), None),
        )
    _gravar_repasse(con, rid, {**snap, "data_pagamento": d["data_pagamento"].isoformat()})
    return rid, False


def inserir_despesa(con: sqlite3.Connection, d: dict[str, Any], quem: Quem) -> tuple[int, bool]:
    if d.get("operacao_id"):
        existente = con.execute(
            "SELECT id FROM despesa WHERE operacao_id = ?", (d["operacao_id"],)
        ).fetchone()
        if existente:
            return existente["id"], True
    if d["valor"] <= 0:
        raise ErroFinanceiro("O valor da despesa deve ser maior que zero.")
    competencia = d.get("data_competencia") or d["data"]
    rid = con.execute(
        "INSERT INTO despesa (data, data_competencia, descricao, categoria_id, forma_id,"
        " valor_centavos, recorrente, comprovante_ref, origem, lote_id, operacao_id,"
        " criado_por, criado_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            d["data"].isoformat(),
            competencia.isoformat(),
            d["descricao"],
            d["categoria_id"],
            d.get("forma_id"),
            d["valor"],
            int(bool(d.get("recorrente"))),
            d.get("comprovante_ref"),
            d.get("origem", "manual"),
            d.get("lote_id"),
            d.get("operacao_id"),
            quem.actor,
            agora_iso(),
        ),
    ).lastrowid
    return rid, False


def _saldo_estornavel(con: sqlite3.Connection, tabela: str, original: int, total: int) -> int:
    estornado = con.execute(
        f"SELECT -COALESCE(SUM(valor_centavos), 0) FROM {tabela} WHERE estorno_de = ?",
        (original,),
    ).fetchone()[0]
    return total - estornado


def estornar_recebimento(
    con: sqlite3.Connection,
    original_id: int,
    valor: int | None,
    motivo: str,
    quem: Quem,
    *,
    hoje: date,
    operacao_id: str | None = None,
) -> tuple[int, bool]:
    if operacao_id:
        existente = con.execute(
            "SELECT id FROM recebimento WHERE operacao_id = ?", (operacao_id,)
        ).fetchone()
        if existente:
            return existente["id"], True
    orig = con.execute("SELECT * FROM recebimento WHERE id = ?", (original_id,)).fetchone()
    if orig is None or orig["tipo"] == "estorno":
        raise ErroFinanceiro("Recebimento original não encontrado para estorno.")
    saldo = _saldo_estornavel(con, "recebimento", original_id, orig["valor_centavos"])
    if saldo <= 0:
        raise ErroFinanceiro("O lançamento já foi totalmente estornado.")
    valor = saldo if valor is None else valor
    if valor <= 0:
        raise ErroFinanceiro("O valor do estorno deve ser maior que zero.")
    if valor > saldo:
        raise ErroFinanceiro(
            f"Estorno de {formatar_brl(valor)} excede o saldo estornável de {formatar_brl(saldo)}."
        )
    rorig = con.execute("SELECT * FROM repasse WHERE recebimento_id = ?", (original_id,)).fetchone()
    previos = con.execute(
        "SELECT COALESCE(SUM(r.taxa_centavos),0) t, COALESCE(SUM(r.repasse_centavos),0) p"
        " FROM repasse r JOIN recebimento e ON e.id = r.recebimento_id WHERE e.estorno_de = ?",
        (original_id,),
    ).fetchone()
    taxa_rest = rorig["taxa_centavos"] + previos["t"]
    rep_rest = rorig["repasse_centavos"] + previos["p"]
    if valor == saldo:
        taxa_e, rep_e = taxa_rest, rep_rest
    else:
        taxa_e = meio_para_cima(taxa_rest * valor, saldo)
        rep_e = meio_para_cima(rep_rest * valor, saldo)
    liquido_e = valor - taxa_e
    rep_e = min(rep_e, liquido_e)
    competencia = hoje.isoformat()
    rid = con.execute(
        "INSERT INTO recebimento (data_pagamento, data_competencia, paciente_id, forma_id,"
        " meio_id, parcelas, valor_centavos, profissional_id, unidade_id, secretaria, servico,"
        " tipo, nf_status, estorno_de, observacao, origem, operacao_id, pacote_id,"
        " cobranca_prevista_id, criado_por, criado_em)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?, 'estorno', ?,?,?,'manual',?,?,?,?,?)",
        (
            competencia,
            competencia,
            orig["paciente_id"],
            orig["forma_id"],
            orig["meio_id"],
            orig["parcelas"],
            -valor,
            orig["profissional_id"],
            orig["unidade_id"],
            quem.actor,
            orig["servico"],
            orig["nf_status"] if orig["nf_status"] != "emitida" else "pendente",
            original_id,
            motivo,
            operacao_id,
            orig["pacote_id"],
            orig["cobranca_prevista_id"],
            quem.actor,
            agora_iso(),
        ),
    ).lastrowid
    _gravar_repasse(
        con,
        rid,
        {
            "data_pagamento": competencia,
            "profissional_id": rorig["profissional_id"],
            "comissao_regra_id": rorig["comissao_regra_id"],
            "base": rorig["base"],
            "taxa_bp": rorig["taxa_bp"],
            "taxa_centavos": -taxa_e,
            "liquido_centavos": -liquido_e,
            "repasse_centavos": -rep_e,
            "parte_clinica_centavos": -(liquido_e - rep_e),
            "limitado": rorig["limitado"],
        },
    )
    return rid, False


def estornar_despesa(
    con: sqlite3.Connection,
    original_id: int,
    valor: int | None,
    motivo: str,
    quem: Quem,
    *,
    hoje: date,
    operacao_id: str | None = None,
) -> tuple[int, bool]:
    if operacao_id:
        existente = con.execute(
            "SELECT id FROM despesa WHERE operacao_id = ?", (operacao_id,)
        ).fetchone()
        if existente:
            return existente["id"], True
    orig = con.execute("SELECT * FROM despesa WHERE id = ?", (original_id,)).fetchone()
    if orig is None or orig["estorno_de"] is not None:
        raise ErroFinanceiro("Despesa original não encontrada para estorno.")
    saldo = _saldo_estornavel(con, "despesa", original_id, orig["valor_centavos"])
    if saldo <= 0:
        raise ErroFinanceiro("O lançamento já foi totalmente estornado.")
    valor = saldo if valor is None else valor
    if valor <= 0:
        raise ErroFinanceiro("O valor do estorno deve ser maior que zero.")
    if valor > saldo:
        raise ErroFinanceiro(
            f"Estorno de {formatar_brl(valor)} excede o saldo estornável de {formatar_brl(saldo)}."
        )
    dia = hoje.isoformat()
    rid = con.execute(
        "INSERT INTO despesa (data, data_competencia, descricao, categoria_id, forma_id,"
        " valor_centavos, recorrente, estorno_de, origem, operacao_id, criado_por, criado_em)"
        " VALUES (?,?,?,?,?,?,0,?,'manual',?,?,?)",
        (
            dia,
            dia,
            f"Estorno: {motivo}"[:200],
            orig["categoria_id"],
            orig["forma_id"],
            -valor,
            original_id,
            operacao_id,
            quem.actor,
            agora_iso(),
        ),
    ).lastrowid
    return rid, False


def anexar_comprovante(con: sqlite3.Connection, tabela: str, lancamento_id: int, ref: str) -> None:
    if tabela not in ("recebimento", "despesa"):
        raise ErroFinanceiro("Tipo de lançamento inválido.")
    ref = ref.strip()
    if not ref or len(ref) > 200 or "/" in ref or "\\" in ref or ".." in ref:
        raise ErroFinanceiro("Referência de comprovante inválida (use apenas o nome do arquivo).")
    cur = con.execute(
        f"UPDATE {tabela} SET comprovante_ref = ? WHERE id = ?",
        (ref, lancamento_id),
    )
    if cur.rowcount != 1:
        raise ErroFinanceiro("Lançamento não encontrado.")


def atualizar_status_nf(
    con: sqlite3.Connection, recebimento_id: int, status: str, numero: str | None
) -> None:
    if status not in ("emitida", "sem_nf", "pendente"):
        raise ErroFinanceiro("Status de NF inválido.")
    if status == "emitida" and not numero:
        raise ErroFinanceiro("NF emitida exige o número da nota.")
    if status != "emitida":
        numero = None
    cur = con.execute(
        "UPDATE recebimento SET nf_status = ?, nf_numero = ? WHERE id = ? AND tipo <> 'estorno'",
        (status, numero, recebimento_id),
    )
    if cur.rowcount != 1:
        raise ErroFinanceiro("Recebimento não encontrado.")


_HASH_REC = (
    "id, data_pagamento, data_competencia, paciente_id, forma_id, meio_id, parcelas,"
    " valor_centavos, profissional_id, unidade_id, tipo, estorno_de"
)
_HASH_DESP = "id, data, data_competencia, descricao, categoria_id, valor_centavos, estorno_de"
_HASH_REP = "id, recebimento_id, taxa_centavos, repasse_centavos, parte_clinica_centavos"


def hash_mes(con: sqlite3.Connection, mes: str, unidade_id: int | None) -> str:
    h = hashlib.sha256()
    filtro_u = "" if unidade_id is None else " AND unidade_id = :u"
    p = {"mes": mes, "u": unidade_id}
    consultas = [
        f"SELECT {_HASH_REC} FROM recebimento WHERE substr(data_competencia,1,7) = :mes{filtro_u}"
        " ORDER BY id",
        f"SELECT {_HASH_REP} FROM repasse WHERE recebimento_id IN (SELECT id FROM recebimento"
        f" WHERE substr(data_competencia,1,7) = :mes{filtro_u}) ORDER BY id",
    ]
    if unidade_id is None:
        consultas.append(
            f"SELECT {_HASH_DESP} FROM despesa WHERE substr(data_competencia,1,7) = :mes ORDER"
            " BY id"
        )
    for sql in consultas:
        for row in con.execute(sql, p):
            h.update(json.dumps(tuple(row), ensure_ascii=False).encode())
        h.update(b"|")
    return h.hexdigest()


def fechar_mes(
    con: sqlite3.Connection, mes: str, unidade_id: int | None, quem: Quem, *, hoje: date
) -> str:
    if mes >= mes_de(hoje):
        raise ErroFinanceiro("Só é possível fechar meses já encerrados (anteriores ao corrente).")
    atual = con.execute(
        "SELECT status FROM fechamento_vigente WHERE mes = ? AND COALESCE(unidade_id,0) = ?",
        (mes, unidade_id or 0),
    ).fetchone()
    if atual and atual["status"] == "fechado":
        raise ErroFinanceiro(f"O mês {mes} já está fechado.")
    digest = hash_mes(con, mes, unidade_id)
    con.execute(
        "INSERT INTO fechamento_mensal (mes, unidade_id, status, aprovado_por, hash, criado_em)"
        " VALUES (?, ?, 'fechado', ?, ?, ?)",
        (mes, unidade_id, quem.aprovado_por or quem.actor, digest, agora_iso()),
    )
    return digest


def reabrir_mes(
    con: sqlite3.Connection, mes: str, unidade_id: int | None, motivo: str, quem: Quem
) -> None:
    if quem.role != "super_admin":
        raise ErroFinanceiro("Apenas o super_admin pode reabrir um mês fechado.")
    if not motivo.strip():
        raise ErroFinanceiro("Informe o motivo da reabertura.")
    atual = con.execute(
        "SELECT status FROM fechamento_vigente WHERE mes = ? AND COALESCE(unidade_id,0) = ?",
        (mes, unidade_id or 0),
    ).fetchone()
    if not atual or atual["status"] != "fechado":
        raise ErroFinanceiro(f"O mês {mes} não está fechado.")
    con.execute(
        "INSERT INTO fechamento_mensal (mes, unidade_id, status, aprovado_por, motivo, criado_em)"
        " VALUES (?, ?, 'reaberto', ?, ?, ?)",
        (mes, unidade_id, quem.aprovado_por or quem.actor, motivo.strip(), agora_iso()),
    )
