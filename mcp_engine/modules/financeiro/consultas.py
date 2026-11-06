"""Consultas nomeadas e parametrizadas do ledger (sem SQL livre, spec 023).

Perfis sem acesso a paciente (qualquer papel fora de `PAPEIS_GESTORES`) recebem agregados
sem nome. Profissional (papel `viewer` cujo e-mail consta em `profissional.email`) vê só o
próprio repasse.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from mcp_engine.modules.financeiro import integridade
from mcp_engine.modules.financeiro.db import (
    ErroFinanceiro,
    mes_de,
    normalizar_nome,
    parse_data,
    parse_mes,
)
from mcp_engine.modules.financeiro.lancamentos import Quem, calcular_repasse
from mcp_engine.modules.financeiro.money import formatar_brl

LIMITE_MAX = 100
FILTROS_PERMITIDOS = {
    "unidade",
    "profissional",
    "forma",
    "meio",
    "categoria",
    "paciente",
    "limite",
    "pacote_id",
    "tipo",
}


@dataclass(frozen=True)
class Ctx:
    con: sqlite3.Connection
    quem: Quem
    hoje: date
    de: str
    ate: str
    filtros: dict[str, Any]
    operacoes: Callable[[], dict[str, Any] | None] | None = None

    @property
    def gestor(self) -> bool:
        return self.quem.gestor


def parse_periodo(periodo: str | None, hoje: date) -> tuple[str, str]:
    if not periodo:
        periodo = mes_de(hoje)
    texto = str(periodo).strip()
    if re.fullmatch(r"\d{4}-\d{2}", texto):
        mes = parse_mes(texto)
        inicio = date.fromisoformat(f"{mes}-01")
        fim = (inicio.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return inicio.isoformat(), fim.isoformat()
    partes = re.split(r"\s*(?:\.\.|/)\s*", texto)
    if len(partes) == 2:
        de, ate = parse_data(partes[0]), parse_data(partes[1])
        if de > ate:
            raise ErroFinanceiro("Período invertido: a data inicial é posterior à final.")
        return de.isoformat(), ate.isoformat()
    raise ErroFinanceiro("Período inválido: use AAAA-MM ou AAAA-MM-DD..AAAA-MM-DD.")


def _filtros(f: dict[str, Any] | None) -> dict[str, Any]:
    f = dict(f or {})
    extras = set(f) - FILTROS_PERMITIDOS
    if extras:
        raise ErroFinanceiro(f"Filtro não permitido: {', '.join(sorted(extras))}.")
    for chave, valor in f.items():
        if chave in ("limite", "pacote_id"):
            if (
                isinstance(valor, bool)
                or not isinstance(valor, int | str)
                or not str(valor).isdigit()
            ):
                raise ErroFinanceiro(f"Filtro {chave} deve ser um número inteiro.")
            f[chave] = int(valor)
        elif not isinstance(valor, str) or len(valor) > 120:
            raise ErroFinanceiro(f"Filtro {chave} deve ser um texto curto.")
    return f


def _where_rec(c: Ctx) -> tuple[str, list[Any]]:
    cond = ["r.data_pagamento BETWEEN ? AND ?"]
    p: list[Any] = [c.de, c.ate]
    f = c.filtros
    if "unidade" in f:
        cond.append("r.unidade_id = (SELECT id FROM unidade WHERE nome = ? COLLATE NOCASE)")
        p.append(f["unidade"])
    if "profissional" in f:
        cond.append("r.profissional_id = (SELECT id FROM profissional WHERE nome_norm = ?)")
        p.append(normalizar_nome(f["profissional"]))
    if "forma" in f:
        cond.append("r.forma_id = (SELECT id FROM forma_pagamento WHERE nome = ?)")
        p.append(normalizar_nome(f["forma"]))
    if "meio" in f:
        cond.append("r.meio_id = (SELECT id FROM meio_recebimento WHERE nome = ? COLLATE NOCASE)")
        p.append(f["meio"])
    if "tipo" in f:
        cond.append("r.tipo = ?")
        p.append(f["tipo"])
    if "paciente" in f:
        if not c.gestor:
            raise ErroFinanceiro("Filtro por paciente não permitido para este perfil.")
        cond.append("r.paciente_id IN (SELECT id FROM paciente_ref WHERE nome_norm = ?)")
        p.append(normalizar_nome(f["paciente"]))
    return " AND ".join(cond), p


def _linhas(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def _texto_grupos(titulo: str, rows: list[dict[str, Any]], chave: str, valor: str) -> str:
    if not rows:
        return f"{titulo}: sem lançamentos no período."
    linhas = [f"{r[chave]}: {formatar_brl(r[valor])}" for r in rows]
    return f"{titulo}:\n" + "\n".join(linhas)


def _exigir_gestor(c: Ctx, o_que: str) -> None:
    if not c.gestor:
        raise ErroFinanceiro(f"{o_que} é restrito ao gestor.")


def _totais(c: Ctx) -> dict[str, int]:
    w, p = _where_rec(c)
    ent = c.con.execute(
        f"SELECT COALESCE(SUM(r.valor_centavos),0) e, COUNT(*) n FROM recebimento r WHERE {w}",
        p,
    ).fetchone()
    sai = c.con.execute(
        "SELECT COALESCE(SUM(valor_centavos),0) FROM despesa WHERE data BETWEEN ? AND ?",
        (c.de, c.ate),
    ).fetchone()[0]
    return {
        "entradas_centavos": ent["e"],
        "saidas_centavos": sai,
        "resultado_centavos": ent["e"] - sai,
        "lancamentos": ent["n"],
    }


def resumo_mensal(c: Ctx) -> dict[str, Any]:
    t = _totais(c)
    if "unidade" in c.filtros or "profissional" in c.filtros:
        t["saidas_centavos"] = 0
        t["resultado_centavos"] = t["entradas_centavos"]
    if c.gestor:
        sums = (
            "SELECT COALESCE(SUM(p.repasse_centavos),0) a,"
            " COALESCE(SUM(p.parte_clinica_centavos),0) b,"
            " COALESCE(SUM(p.taxa_centavos),0) t FROM repasse p"
        )
        if c.filtros:
            w, p = _where_rec(c)
            rep = c.con.execute(
                f"{sums} JOIN recebimento r ON r.id = p.recebimento_id WHERE {w}",
                p,
            ).fetchone()
        else:
            rep = c.con.execute(
                f"{sums} WHERE p.data_pagamento BETWEEN ? AND ?",
                (c.de, c.ate),
            ).fetchone()
        t.update(
            repasse_centavos=rep["a"], parte_clinica_centavos=rep["b"], taxas_centavos=rep["t"]
        )
    texto = (
        f"Entradas {formatar_brl(t['entradas_centavos'])}, saídas"
        f" {formatar_brl(t['saidas_centavos'])}, "
        f"resultado {formatar_brl(t['resultado_centavos'])} ({t['lancamentos']} lançamentos)."
    )
    return {"dados": t, "texto": texto}


def _grupo_rec(c: Ctx, expr: str, juncao: str, vazio: str) -> dict[str, Any]:
    w, p = _where_rec(c)
    rows = c.con.execute(
        f"SELECT COALESCE({expr}, '{vazio}') AS chave, SUM(r.valor_centavos) AS total,"
        f" COUNT(*) AS n FROM recebimento r {juncao} WHERE {w} GROUP BY 1 ORDER BY 2 DESC, 1",
        p,
    ).fetchall()
    return {"dados": _linhas(rows), "texto": ""}


def por_forma(c: Ctx) -> dict[str, Any]:
    r = _grupo_rec(c, "f.nome", "JOIN forma_pagamento f ON f.id = r.forma_id", "?")
    r["texto"] = _texto_grupos("Por forma", r["dados"], "chave", "total")
    return r


def por_conta(c: Ctx) -> dict[str, Any]:
    r = _grupo_rec(c, "m.nome", "LEFT JOIN meio_recebimento m ON m.id = r.meio_id", "sem conta")
    r["texto"] = _texto_grupos("Por conta", r["dados"], "chave", "total")
    return r


def por_unidade(c: Ctx) -> dict[str, Any]:
    r = _grupo_rec(c, "u.nome", "LEFT JOIN unidade u ON u.id = r.unidade_id", "sem unidade")
    r["texto"] = _texto_grupos("Por unidade", r["dados"], "chave", "total")
    return r


def por_profissional(c: Ctx) -> dict[str, Any]:
    _exigir_gestor(c, "A consulta por profissional")
    r = _grupo_rec(
        c, "pr.nome", "LEFT JOIN profissional pr ON pr.id = r.profissional_id", "sem profissional"
    )
    r["texto"] = _texto_grupos("Por profissional", r["dados"], "chave", "total")
    return r


def sem_nf(c: Ctx) -> dict[str, Any]:
    w, p = _where_rec(c)
    resumo = c.con.execute(
        f"SELECT r.nf_status AS status, COUNT(*) AS n, SUM(r.valor_centavos) AS total"
        f" FROM recebimento r WHERE {w} AND r.tipo <> 'estorno' AND r.nf_status <> 'emitida'"
        " GROUP BY 1 ORDER BY 1",
        p,
    ).fetchall()
    dados: dict[str, Any] = {"por_status": _linhas(resumo)}
    if c.gestor:
        lim = min(c.filtros.get("limite", 50), LIMITE_MAX)
        lista = c.con.execute(
            "SELECT r.id, r.data_pagamento AS data, pa.nome AS paciente, r.valor_centavos,"
            " r.nf_status FROM recebimento r JOIN paciente_ref pa ON pa.id = r.paciente_id"
            f" WHERE {w}"
            " AND r.tipo <> 'estorno' AND r.nf_status <> 'emitida' ORDER BY r.data_pagamento, r.id"
            " LIMIT ?",
            [*p, lim],
        ).fetchall()
        dados["lancamentos"] = _linhas(lista)
    n = sum(r["n"] for r in resumo)
    t = sum(r["total"] for r in resumo)
    return {
        "dados": dados,
        "texto": f"{n} lançamento(s) sem NF emitida, somando {formatar_brl(t)}.",
    }


def despesas_por_categoria(c: Ctx) -> dict[str, Any]:
    rows = c.con.execute(
        "SELECT cd.grupo, cd.nome AS categoria, SUM(d.valor_centavos) AS total, COUNT(*) AS n"
        " FROM despesa d JOIN categoria_despesa cd ON cd.id = d.categoria_id"
        " WHERE d.data BETWEEN ? AND ? GROUP BY cd.id ORDER BY cd.grupo, total DESC",
        (c.de, c.ate),
    ).fetchall()
    dados = _linhas(rows)
    total = sum(r["total"] for r in dados)
    return {
        "dados": dados,
        "texto": f"Despesas no período: {formatar_brl(total)} em {len(dados)} categorias.",
    }


def resultado_mensal(c: Ctx) -> dict[str, Any]:
    ent = dict(
        c.con.execute(
            "SELECT substr(data_pagamento,1,7) m, SUM(valor_centavos) FROM recebimento"
            " WHERE data_pagamento BETWEEN ? AND ? GROUP BY 1",
            (c.de, c.ate),
        ).fetchall()
    )
    sai = dict(
        c.con.execute(
            "SELECT substr(data,1,7) m, SUM(valor_centavos) FROM despesa"
            " WHERE data BETWEEN ? AND ? GROUP BY 1",
            (c.de, c.ate),
        ).fetchall()
    )
    meses = [
        {
            "mes": m,
            "entradas_centavos": ent.get(m, 0),
            "saidas_centavos": sai.get(m, 0),
            "resultado_centavos": ent.get(m, 0) - sai.get(m, 0),
        }
        for m in sorted(set(ent) | set(sai))
    ]
    total = sum(m["resultado_centavos"] for m in meses)
    return {"dados": meses, "texto": f"Resultado do período: {formatar_brl(total)}."}


def ultimos_lancamentos(c: Ctx) -> dict[str, Any]:
    lim = min(c.filtros.get("limite", 20), LIMITE_MAX)
    nome = "pa.nome" if c.gestor else "NULL"
    rec = c.con.execute(
        f"SELECT 'recebimento' AS tipo, r.id, r.data_pagamento AS data, r.valor_centavos,"
        f" r.tipo AS subtipo, {nome} AS paciente, f.nome AS forma FROM recebimento r"
        " JOIN forma_pagamento f ON f.id = r.forma_id JOIN paciente_ref pa ON pa.id = r.paciente_id"
        " WHERE r.data_pagamento BETWEEN ? AND ? ORDER BY r.data_pagamento DESC, r.id DESC LIMIT ?",
        (c.de, c.ate, lim),
    ).fetchall()
    des = c.con.execute(
        "SELECT 'despesa' AS tipo, d.id, d.data AS data, d.valor_centavos, cd.grupo AS subtipo,"
        " NULL AS paciente, cd.nome AS forma FROM despesa d JOIN categoria_despesa cd"
        " ON cd.id = d.categoria_id WHERE d.data BETWEEN ? AND ? ORDER BY d.data DESC, d.id DESC"
        " LIMIT ?",
        (c.de, c.ate, lim),
    ).fetchall()
    linhas = sorted(
        [dict(r) for r in [*rec, *des]], key=lambda r: (r["data"], r["id"]), reverse=True
    )[:lim]
    if not c.gestor:
        for r in linhas:
            r.pop("paciente", None)
    return {"dados": linhas, "texto": f"{len(linhas)} lançamento(s) mais recentes."}


def _repasse_agrupado(c: Ctx) -> list[dict[str, Any]]:
    _exigir_gestor(c, "O repasse")
    w, p = _where_rec(c)
    rows = c.con.execute(
        "SELECT COALESCE(pr.nome, 'sem profissional') AS profissional, SUM(r.valor_centavos) AS"
        " bruto,"
        " SUM(p.taxa_centavos) AS taxas, SUM(p.liquido_centavos) AS liquido,"
        " SUM(p.repasse_centavos) AS repasse, SUM(p.parte_clinica_centavos) AS parte_clinica"
        " FROM repasse p JOIN recebimento r ON r.id = p.recebimento_id"
        f" LEFT JOIN profissional pr ON pr.id = p.profissional_id WHERE {w} GROUP BY pr.id"
        " ORDER BY repasse DESC",
        p,
    ).fetchall()
    return _linhas(rows)


def repasse_por_profissional(c: Ctx) -> dict[str, Any]:
    dados = _repasse_agrupado(c)
    total = sum(r["repasse"] for r in dados)
    return {"dados": dados, "texto": f"Repasse no período: {formatar_brl(total)}."}


def parte_clinica(c: Ctx) -> dict[str, Any]:
    _exigir_gestor(c, "A parte da clínica")
    w, p = _where_rec(c)
    rows = c.con.execute(
        "SELECT COALESCE(u.nome, 'sem unidade') AS unidade, SUM(p.parte_clinica_centavos) AS total"
        " FROM repasse p JOIN recebimento r ON r.id = p.recebimento_id"
        f" LEFT JOIN unidade u ON u.id = r.unidade_id WHERE {w} GROUP BY u.id ORDER BY 1",
        p,
    ).fetchall()
    dados = _linhas(rows)
    total = sum(r["total"] for r in dados)
    return {"dados": dados, "texto": f"Parte da clínica: {formatar_brl(total)}."}


def taxas_por_meio(c: Ctx) -> dict[str, Any]:
    _exigir_gestor(c, "O custo de taxas por meio")
    w, p = _where_rec(c)
    rows = c.con.execute(
        "SELECT COALESCE(m.nome, 'sem conta') AS meio, SUM(r.valor_centavos) AS bruto,"
        " SUM(p.taxa_centavos) AS taxas, COUNT(*) AS n FROM repasse p JOIN recebimento r"
        f" ON r.id = p.recebimento_id LEFT JOIN meio_recebimento m ON m.id = r.meio_id WHERE {w}"
        " GROUP BY m.id ORDER BY taxas DESC",
        p,
    ).fetchall()
    dados = _linhas(rows)
    total = sum(r["taxas"] for r in dados)
    return {"dados": dados, "texto": f"Taxas de meios no período: {formatar_brl(total)}."}


def a_receber_calc(con: sqlite3.Connection, ate: str) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT p.id AS pacote_id, pa.nome AS paciente, p.status,"
        " COALESCE((SELECT SUM(valor_centavos) FROM cobranca_prevista c"
        "   WHERE c.pacote_id = p.id AND c.vencimento <= ?), 0) AS previsto,"
        " COALESCE((SELECT SUM(valor_centavos) FROM recebimento r WHERE r.pacote_id = p.id"
        "   AND r.data_pagamento <= ?), 0) AS recebido"
        " FROM pacote p JOIN paciente_ref pa ON pa.id = p.paciente_id WHERE p.status <>"
        " 'cancelado'",
        (ate, ate),
    ).fetchall()
    out = []
    for r in rows:
        saldo = r["previsto"] - r["recebido"]
        out.append(
            {
                "pacote_id": r["pacote_id"],
                "paciente": r["paciente"],
                "previsto_centavos": r["previsto"],
                "recebido_centavos": r["recebido"],
                "a_receber_centavos": max(0, saldo),
                "credito_centavos": max(0, -saldo),
            }
        )
    return out


def a_receber(c: Ctx) -> dict[str, Any]:
    rows = a_receber_calc(c.con, c.ate)
    total = sum(r["a_receber_centavos"] for r in rows)
    credito = sum(r["credito_centavos"] for r in rows)
    dados: dict[str, Any] = {
        "a_receber_centavos": total,
        "credito_centavos": credito,
        "pacotes": len(rows),
    }
    if c.gestor:
        dados["por_pacote"] = [r for r in rows if r["a_receber_centavos"] or r["credito_centavos"]]
    return {
        "dados": dados,
        "texto": f"A receber até {c.ate}: {formatar_brl(total)}; crédito explícito"
        " {formatar_brl(credito)}.",
    }


def saldo_pacote(c: Ctx) -> dict[str, Any]:
    cond, p = ["pk.status <> 'cancelado'"], []
    if "pacote_id" in c.filtros:
        cond.append("pk.id = ?")
        p.append(c.filtros["pacote_id"])
    if "paciente" in c.filtros:
        if not c.gestor:
            raise ErroFinanceiro("Filtro por paciente não permitido para este perfil.")
        cond.append("pa.nome_norm = ?")
        p.append(normalizar_nome(c.filtros["paciente"]))
    rows = c.con.execute(
        "SELECT pk.id AS pacote_id, pa.nome AS paciente, pk.sessoes_totais, pk.validade, pk.status,"
        " (SELECT COUNT(*) FROM sessao s JOIN modelo_pacote m ON m.id = pk.modelo_id"
        "  WHERE s.pacote_id = pk.id AND (s.status_agenda = 'COMPLETED' OR (m.falta_consome = 1"
        "  AND EXISTS (SELECT 1 FROM ocorrencia_efetiva o WHERE o.sessao_id = s.id"
        "  AND o.tipo IN ('falta', 'cancelamento_tardio'))))) AS consumidas"
        " FROM pacote pk JOIN paciente_ref pa ON pa.id = pk.paciente_id"
        f" WHERE {' AND '.join(cond)} ORDER BY pk.id LIMIT 200",
        p,
    ).fetchall()
    itens = [{**dict(r), "saldo": r["sessoes_totais"] - r["consumidas"]} for r in rows]
    agregado = {
        "pacotes": len(itens),
        "sessoes_restantes": sum(i["saldo"] for i in itens),
    }
    if c.gestor:
        return {"dados": {**agregado, "itens": itens}, "texto": f"{len(itens)} pacote(s)."}
    return {
        "dados": agregado,
        "texto": f"{agregado['pacotes']} pacote(s), {agregado['sessoes_restantes']} sessões"
        " restantes.",
    }


def faltas_por_periodo(c: Ctx) -> dict[str, Any]:
    cond = "substr(s.inicio_utc,1,10) BETWEEN ? AND ?"
    resumo = c.con.execute(
        "SELECT o.tipo, COUNT(*) AS n FROM ocorrencia_efetiva o JOIN sessao s ON s.id = o.sessao_id"
        f" WHERE {cond} GROUP BY o.tipo ORDER BY o.tipo",
        (c.de, c.ate),
    ).fetchall()
    dados: dict[str, Any] = {"por_tipo": _linhas(resumo)}
    if c.gestor:
        lista = c.con.execute(
            "SELECT substr(s.inicio_utc,1,10) AS data, pa.nome AS paciente, o.tipo, o.motivo"
            " FROM ocorrencia_efetiva o JOIN sessao s ON s.id = o.sessao_id"
            f" JOIN paciente_ref pa ON pa.id = s.paciente_id WHERE {cond} ORDER BY 1 LIMIT ?",
            (c.de, c.ate, min(c.filtros.get("limite", 50), LIMITE_MAX)),
        ).fetchall()
        dados["ocorrencias"] = _linhas(lista)
    return {"dados": dados, "texto": f"{sum(r['n'] for r in resumo)} ocorrência(s) no período."}


def pendencias(c: Ctx) -> dict[str, Any]:
    resumo = c.con.execute(
        "SELECT tipo, status, COUNT(*) AS n FROM pendencia_operacional GROUP BY tipo, status"
        " ORDER BY tipo, status"
    ).fetchall()
    dados: dict[str, Any] = {"resumo": _linhas(resumo)}
    if c.gestor:
        abertas = c.con.execute(
            "SELECT id, tipo, referencia, aba, linha, motivo, sugestao, criada_em"
            " FROM pendencia_operacional WHERE status = 'aberta' ORDER BY id LIMIT ?",
            (min(c.filtros.get("limite", 50), LIMITE_MAX),),
        ).fetchall()
        dados["abertas"] = _linhas(abertas)
    n = sum(r["n"] for r in resumo if r["status"] == "aberta")
    return {"dados": dados, "texto": f"{n} pendência(s) aberta(s)."}


def verificar_integridade(c: Ctx) -> dict[str, Any]:
    _exigir_gestor(c, "A verificação de integridade")
    viol = integridade.verificar(c.con)
    return {
        "dados": {"ok": not viol, "violacoes": viol},
        "texto": "Integridade OK." if not viol else f"{len(viol)} violação(ões) de invariante.",
    }


def simular_repasse(c: Ctx) -> dict[str, Any]:
    _exigir_gestor(c, "A simulação de repasse")
    w, p = _where_rec(c)
    rows = c.con.execute(
        "SELECT r.*, p.repasse_centavos AS gravado FROM recebimento r JOIN repasse p"
        f" ON p.recebimento_id = r.id WHERE {w} AND r.tipo <> 'estorno'",
        p,
    ).fetchall()
    atual = novo = sem_regra = 0
    for r in rows:
        atual += r["gravado"]
        try:
            snap = calcular_repasse(
                c.con,
                valor=r["valor_centavos"],
                data=date.fromisoformat(r["data_pagamento"]),
                meio_id=r["meio_id"],
                forma_id=r["forma_id"],
                parcelas=r["parcelas"],
                profissional_id=r["profissional_id"],
                unidade_id=r["unidade_id"],
                servico=r["servico"],
                tipo=r["tipo"],
            )
            novo += snap["repasse_centavos"]
        except ErroFinanceiro:
            sem_regra += 1
            novo += r["gravado"]
    dados = {
        "repasse_gravado_centavos": atual,
        "repasse_simulado_centavos": novo,
        "diferenca_centavos": novo - atual,
        "lancamentos_sem_regra": sem_regra,
        "gravou": False,
    }
    return {
        "dados": dados,
        "texto": f"Simulação: repasse {formatar_brl(novo)} contra {formatar_brl(atual)} gravado "
        f"(diferença {formatar_brl(novo - atual)}). Nada foi gravado.",
    }


def _operacoes_aguardando(c: Ctx) -> int | None:
    resumo = c.operacoes() if c.operacoes else None
    return None if resumo is None else resumo["aguardando_aprovacao"]


def painel_financeiro(c: Ctx) -> dict[str, Any]:
    fech = c.con.execute(
        "SELECT mes, unidade_id, status FROM fechamento_vigente WHERE mes = ?",
        (c.de[:7],),
    ).fetchall()
    dados: dict[str, Any] = {
        "periodo": {"de": c.de, "ate": c.ate},
        "resumo": resumo_mensal(c)["dados"],
        "por_unidade": por_unidade(c)["dados"],
        "por_forma": por_forma(c)["dados"],
        "sem_nf": sem_nf(c)["dados"]["por_status"],
        "pendencias_abertas": c.con.execute(
            "SELECT COUNT(*) FROM pendencia_operacional WHERE status = 'aberta'"
        ).fetchone()[0],
        "fechamento": _linhas(fech) or [{"mes": c.de[:7], "status": "aberto"}],
        "operacoes_aguardando_aprovacao": _operacoes_aguardando(c),
    }
    if c.gestor:
        dados["integridade_ok"] = not integridade.verificar(c.con)
    return {"dados": dados, "texto": f"Painel de {c.de} a {c.ate}. " + resumo_mensal(c)["texto"]}


CONSULTAS: dict[str, Callable[[Ctx], dict[str, Any]]] = {
    "resumo_mensal": resumo_mensal,
    "por_forma": por_forma,
    "por_conta": por_conta,
    "por_profissional": por_profissional,
    "por_unidade": por_unidade,
    "sem_nf": sem_nf,
    "despesas_por_categoria": despesas_por_categoria,
    "resultado_mensal": resultado_mensal,
    "ultimos_lancamentos": ultimos_lancamentos,
    "repasse_por_profissional": repasse_por_profissional,
    "taxas_por_meio": taxas_por_meio,
    "parte_clinica": parte_clinica,
    "a_receber": a_receber,
    "saldo_pacote": saldo_pacote,
    "faltas_por_periodo": faltas_por_periodo,
    "pendencias": pendencias,
    "painel_financeiro": painel_financeiro,
    "verificar_integridade": verificar_integridade,
    "simular_repasse": simular_repasse,
}


def consultar(
    con: sqlite3.Connection,
    quem: Quem,
    consulta: str,
    periodo: str | None,
    filtros: dict[str, Any] | None,
    *,
    hoje: date,
    operacoes: Callable[[], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    fn = CONSULTAS.get(consulta)
    if fn is None:
        raise ErroFinanceiro(f"Consulta desconhecida. Disponíveis: {', '.join(sorted(CONSULTAS))}.")
    de, ate = parse_periodo(periodo, hoje)
    ctx = Ctx(con, quem, hoje, de, ate, _filtros(filtros), operacoes)
    res = fn(ctx)
    return {
        "consulta": consulta,
        "periodo": {"de": de, "ate": ate},
        "agregado": not quem.gestor,
        **res,
    }
