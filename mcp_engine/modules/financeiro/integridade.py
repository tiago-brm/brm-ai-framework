"""Verificação dos invariantes F1 a F10 do ledger (spec 023)."""

from __future__ import annotations

import sqlite3
from typing import Any

from mcp_engine.modules.financeiro.lancamentos import hash_mes


def _v(inv: str, detalhe: str, **extra: Any) -> dict[str, Any]:
    return {"invariante": inv, "detalhe": detalhe, **extra}


def verificar(con: sqlite3.Connection) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    total = {
        r["mes"]: r["t"]
        for r in con.execute(
            "SELECT substr(data_pagamento,1,7) mes, SUM(valor_centavos) t FROM recebimento"
            " GROUP BY 1"
        )
    }
    por_forma = {
        r["mes"]: r["t"]
        for r in con.execute(
            "SELECT substr(r.data_pagamento,1,7) mes, SUM(r.valor_centavos) t FROM recebimento r"
            " JOIN forma_pagamento f ON f.id = r.forma_id GROUP BY 1"
        )
    }
    por_unidade = {
        r["mes"]: r["t"]
        for r in con.execute(
            "SELECT substr(r.data_pagamento,1,7) mes, SUM(r.valor_centavos) t FROM recebimento r"
            " LEFT JOIN unidade u ON u.id = r.unidade_id"
            " WHERE r.unidade_id IS NULL OR u.id IS NOT NULL GROUP BY 1"
        )
    }
    for mes, t in total.items():
        if por_forma.get(mes) != t:
            out.append(_v("F1", f"soma por forma difere do total em {mes}", mes=mes))
        if por_unidade.get(mes) != t:
            out.append(_v("F2", f"soma por unidade difere do total em {mes}", mes=mes))

    for r in con.execute(
        "SELECT e.id FROM recebimento e LEFT JOIN recebimento o ON o.id = e.estorno_de"
        " WHERE e.estorno_de IS NOT NULL AND (o.id IS NULL OR o.paciente_id <> e.paciente_id"
        " OR o.tipo = 'estorno')"
    ):
        out.append(_v("F3", "estorno sem original válido do mesmo paciente", id=r["id"]))
    for tabela in ("recebimento", "despesa"):
        for r in con.execute(
            f"SELECT o.id FROM {tabela} o JOIN {tabela} e ON e.estorno_de = o.id"
            " GROUP BY o.id HAVING -SUM(e.valor_centavos) > o.valor_centavos"
        ):
            out.append(_v("F3", f"estornos de {tabela} acima do saldo", id=r["id"]))

    for tabela, col in (
        ("recebimento", "valor_centavos"),
        ("despesa", "valor_centavos"),
        ("repasse", "repasse_centavos"),
        ("repasse", "parte_clinica_centavos"),
        ("repasse", "taxa_centavos"),
    ):
        n = con.execute(
            f"SELECT COUNT(*) FROM {tabela} WHERE typeof({col}) <> 'integer'"
        ).fetchone()[0]
        if n:
            out.append(_v("F4", f"{n} valor(es) não inteiro(s) em {tabela}.{col}"))

    for f in con.execute(
        "SELECT mes, unidade_id, hash FROM fechamento_vigente WHERE status = 'fechado'"
    ):
        if hash_mes(con, f["mes"], f["unidade_id"]) != f["hash"]:
            out.append(_v("F5", f"conteúdo do mês fechado {f['mes']} difere do hash", mes=f["mes"]))

    for r in con.execute(
        "SELECT r.id FROM recebimento r LEFT JOIN repasse p ON p.recebimento_id = r.id"
        " WHERE p.id IS NULL OR p.repasse_centavos + p.parte_clinica_centavos"
        " <> r.valor_centavos - p.taxa_centavos"
    ):
        out.append(_v("F6", "repasse mais parte da clínica difere de valor menos taxa", id=r["id"]))

    for tabela, extra in (
        (
            "comissao_regra",
            "a.profissional_id = b.profissional_id AND a.servico IS b.servico"
            " AND a.forma_id IS b.forma_id AND a.unidade_id IS b.unidade_id",
        ),
        (
            "taxa_meio",
            "a.meio_id = b.meio_id AND a.forma_id = b.forma_id AND a.parcelas = b.parcelas",
        ),
        ("modelo_pacote", "a.nome = b.nome"),
    ):
        for r in con.execute(
            f"SELECT a.id FROM {tabela} a JOIN {tabela} b ON a.id < b.id AND {extra}"
            " AND a.vigente_de <= COALESCE(b.vigente_ate, '9999-12-31')"
            " AND b.vigente_de <= COALESCE(a.vigente_ate, '9999-12-31')"
        ):
            out.append(_v("F7", f"vigências sobrepostas em {tabela}", id=r["id"]))
    for r in con.execute(
        "SELECT p.recebimento_id FROM repasse p JOIN recebimento r ON r.id = p.recebimento_id"
        " JOIN comissao_regra c ON c.id = p.comissao_regra_id WHERE r.estorno_de IS NULL"
        " AND NOT (c.vigente_de <= r.data_pagamento"
        " AND (c.vigente_ate IS NULL OR c.vigente_ate >= r.data_pagamento))"
    ):
        out.append(
            _v("F7", "regra de comissão fora da vigência no lançamento", id=r["recebimento_id"])
        )

    for r in con.execute(
        "SELECT s.id FROM sessao s WHERE s.status_agenda = 'COMPLETED'"
        " AND EXISTS (SELECT 1 FROM ocorrencia_efetiva o WHERE o.sessao_id = s.id"
        " AND o.tipo IN ('falta', 'cancelamento_tardio'))"
        " AND NOT EXISTS (SELECT 1 FROM pendencia_operacional p WHERE p.tipo = 'conflito_desfecho'"
        " AND p.referencia = 'sessao:' || s.id AND p.status = 'aberta')"
    ):
        out.append(_v("F8", "sessão realizada e com falta sem pendência aberta", id=r["id"]))

    for r in con.execute("SELECT id, sessoes_totais FROM pacote"):
        n = con.execute(
            "SELECT COUNT(*) FROM sessao s JOIN pacote p ON p.id = s.pacote_id"
            " JOIN modelo_pacote m ON m.id = p.modelo_id WHERE s.pacote_id = ?"
            " AND (s.status_agenda = 'COMPLETED' OR (m.falta_consome = 1 AND EXISTS ("
            "SELECT 1 FROM ocorrencia_efetiva o WHERE o.sessao_id = s.id"
            " AND o.tipo IN ('falta', 'cancelamento_tardio'))))",
            (r["id"],),
        ).fetchone()[0]
        if n > r["sessoes_totais"]:
            out.append(_v("F9", "sessões consumidas acima do total do pacote", id=r["id"]))

    for r in con.execute(
        "SELECT p.id, p.preco_acordado_centavos"
        " - COALESCE((SELECT SUM(valor_centavos) FROM cobranca_prevista c WHERE c.pacote_id ="
        " p.id), 0)"
        " AS excesso FROM pacote p"
    ):
        if r["excesso"] < 0:
            out.append(_v("F10", "cobranças previstas acima do preço do pacote", id=r["id"]))

    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    if fk:
        out.append(_v("F1", f"{len(fk)} referência(s) órfã(s) no ledger"))
    return out
