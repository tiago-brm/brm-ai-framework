"""Cadastros e parâmetros com vigência (comissão, taxa, meio, modelo de pacote).

Não há skill de escrita direta para parâmetros: as funções `materializar_*` são o contrato
chamado pelo mecanismo de notas de configuração aprovadas (spec 030). Alterar um parâmetro
cria nova linha e encerra a anterior; a história nunca é reescrita.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from mcp_engine.modules.financeiro.db import (
    ErroFinanceiro,
    abrir,
    normalizar_nome,
    parse_data,
    transacao,
)
from mcp_engine.modules.financeiro.money import ValorInvalido, bp_de_percentual, parse_brl

MODOS_COBRANCA = ("antecipado", "por_sessao", "parcelado", "mensal")


def garantir_unidade(con: sqlite3.Connection, nome: str) -> int:
    nome = nome.strip()
    row = con.execute("SELECT id FROM unidade WHERE nome = ?", (nome,)).fetchone()
    if row:
        return row["id"]
    return con.execute("INSERT INTO unidade (nome) VALUES (?)", (nome,)).lastrowid


def garantir_profissional(
    con: sqlite3.Connection, nome: str, *, email: str | None = None, external_ref: str | None = None
) -> int:
    norm = normalizar_nome(nome)
    if not norm:
        raise ErroFinanceiro("Nome de profissional vazio.")
    row = con.execute("SELECT id FROM profissional WHERE nome_norm = ?", (norm,)).fetchone()
    if row:
        if email:
            con.execute("UPDATE profissional SET email = ? WHERE id = ?", (email, row["id"]))
        return row["id"]
    return con.execute(
        "INSERT INTO profissional (nome, nome_norm, email, external_ref) VALUES (?, ?, ?, ?)",
        (nome.strip(), norm, email, external_ref),
    ).lastrowid


def garantir_categoria(con: sqlite3.Connection, grupo: str, nome: str) -> int:
    nome = nome.strip()
    row = con.execute(
        "SELECT id FROM categoria_despesa WHERE grupo = ? AND nome = ?", (grupo, nome)
    ).fetchone()
    if row:
        return row["id"]
    return con.execute(
        "INSERT INTO categoria_despesa (grupo, nome) VALUES (?, ?)", (grupo, nome)
    ).lastrowid


def garantir_paciente(
    con: sqlite3.Connection,
    nome: str,
    *,
    unidade_id: int | None = None,
    profissional_id: int | None = None,
    valor_sessao_centavos: int | None = None,
    external_ref: str | None = None,
    status: str = "ativo",
) -> int:
    norm = normalizar_nome(nome)
    if not norm:
        raise ErroFinanceiro("Nome de paciente vazio.")
    row = con.execute("SELECT id FROM paciente_ref WHERE nome_norm = ?", (norm,)).fetchone()
    if row:
        return row["id"]
    return con.execute(
        "INSERT INTO paciente_ref (nome, nome_norm, unidade_id, profissional_id,"
        " valor_sessao_centavos, external_ref, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            nome.strip(),
            norm,
            unidade_id,
            profissional_id,
            valor_sessao_centavos,
            external_ref,
            status,
        ),
    ).lastrowid


def id_forma(con: sqlite3.Connection, nome: str) -> int:
    row = con.execute(
        "SELECT id FROM forma_pagamento WHERE nome = ?", (normalizar_nome(str(nome)),)
    ).fetchone()
    if not row:
        raise ErroFinanceiro(f"Forma de pagamento desconhecida: {nome!r}.")
    return row["id"]


def id_meio(con: sqlite3.Connection, nome: str) -> int:
    row = con.execute(
        "SELECT id FROM meio_recebimento WHERE nome = ? COLLATE NOCASE", (str(nome).strip(),)
    ).fetchone()
    if not row:
        raise ErroFinanceiro(f"Meio de recebimento desconhecido: {nome!r}.")
    return row["id"]


def meio_vigente(con: sqlite3.Connection, nome: str, em: date) -> int:
    row = con.execute(
        "SELECT id FROM meio_recebimento WHERE nome = ? COLLATE NOCASE AND vigente_de <= ?"
        " AND (vigente_ate IS NULL OR vigente_ate >= ?)",
        (str(nome).strip(), em.isoformat(), em.isoformat()),
    ).fetchone()
    if not row:
        raise ErroFinanceiro(f"Meio {nome!r} não está vigente em {em.isoformat()}.")
    return row["id"]


def taxa_vigente(
    con: sqlite3.Connection, meio_id: int | None, forma_id: int, parcelas: int, em: date
) -> int:
    if meio_id is None:
        return 0
    rows = con.execute(
        "SELECT taxa_bp FROM taxa_meio WHERE meio_id = ? AND forma_id = ? AND parcelas = ?"
        " AND vigente_de <= ? AND (vigente_ate IS NULL OR vigente_ate >= ?)",
        (meio_id, forma_id, parcelas, em.isoformat(), em.isoformat()),
    ).fetchall()
    if len(rows) > 1:
        raise ErroFinanceiro("Mais de uma taxa vigente para o mesmo meio, forma e parcelas.")
    return rows[0]["taxa_bp"] if rows else 0


def comissao_vigente(
    con: sqlite3.Connection,
    profissional_id: int,
    servico: str | None,
    forma_id: int,
    unidade_id: int | None,
    em: date,
) -> sqlite3.Row:
    """Regra mais específica vigente: serviço (4) > forma (2) > unidade (1) > profissional."""
    rows = con.execute(
        "SELECT * FROM comissao_regra WHERE profissional_id = ?"
        " AND vigente_de <= ? AND (vigente_ate IS NULL OR vigente_ate >= ?)"
        " AND (servico IS NULL OR servico = ?)"
        " AND (forma_id IS NULL OR forma_id = ?)"
        " AND (unidade_id IS NULL OR unidade_id = ?)",
        (profissional_id, em.isoformat(), em.isoformat(), servico, forma_id, unidade_id),
    ).fetchall()
    if not rows:
        raise ErroFinanceiro(
            "Nenhuma regra de comissão vigente para este profissional, serviço e forma. "
            "Cadastre a comissão antes de lançar."
        )

    def score(r: sqlite3.Row) -> int:
        return (
            (4 if r["servico"] is not None else 0)
            + (2 if r["forma_id"] is not None else 0)
            + (1 if r["unidade_id"] is not None else 0)
        )

    melhor = max(score(r) for r in rows)
    topo = [r for r in rows if score(r) == melhor]
    if len(topo) > 1:
        raise ErroFinanceiro(
            "Regras de comissão empatadas para este lançamento: corrija a vigência."
        )
    return topo[0]


def _dia_anterior(d: date) -> str:
    return (d - timedelta(days=1)).isoformat()


def _fechar_vigencia(
    con: sqlite3.Connection, tabela: str, anteriores: list[sqlite3.Row], nova_de: date
) -> None:
    for ant in anteriores:
        if date.fromisoformat(ant["vigente_de"]) >= nova_de:
            raise ErroFinanceiro(
                "A nova vigência deve começar depois da vigência atual (a história não é"
                " reescrita)."
            )
        con.execute(
            f"UPDATE {tabela} SET vigente_ate = ? WHERE id = ?",
            (_dia_anterior(nova_de), ant["id"]),
        )


def _obrigatorio(dados: dict[str, Any], campo: str) -> Any:
    valor = dados.get(campo)
    if valor in (None, ""):
        raise ErroFinanceiro(f"Parâmetro obrigatório ausente: {campo}.")
    return valor


def _montar(path: Path | str, fn: Any, dados: dict[str, Any], criado_por: str) -> int:
    con = abrir(path)
    try:
        with transacao(con):
            return fn(con, dados, criado_por)
    except ValorInvalido as exc:
        raise ErroFinanceiro(str(exc)) from None
    finally:
        con.close()


def _comissao(con: sqlite3.Connection, dados: dict[str, Any], criado_por: str) -> int:
    prof = garantir_profissional(con, str(_obrigatorio(dados, "profissional")))
    de = parse_data(_obrigatorio(dados, "vigente_de"))
    servico = normalizar_nome(str(dados["servico"])) if dados.get("servico") else None
    forma = id_forma(con, dados["forma"]) if dados.get("forma") else None
    unidade = garantir_unidade(con, str(dados["unidade"])) if dados.get("unidade") else None
    tipo = _obrigatorio(dados, "tipo")
    base = dados.get("base", "liquido")
    if tipo == "percentual":
        valor_bp, valor_c = bp_de_percentual(_obrigatorio(dados, "valor")), None
    elif tipo == "fixo":
        valor_bp, valor_c = None, parse_brl(_obrigatorio(dados, "valor"))
    else:
        raise ErroFinanceiro("Tipo de comissão deve ser 'percentual' ou 'fixo'.")
    anteriores = con.execute(
        "SELECT * FROM comissao_regra WHERE profissional_id = ? AND servico IS ? AND forma_id IS ?"
        " AND unidade_id IS ? AND vigente_ate IS NULL",
        (prof, servico, forma, unidade),
    ).fetchall()
    _fechar_vigencia(con, "comissao_regra", anteriores, de)
    return con.execute(
        "INSERT INTO comissao_regra (profissional_id, unidade_id, servico, forma_id, tipo,"
        " valor_bp, valor_centavos, base, vigente_de, criado_por) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (prof, unidade, servico, forma, tipo, valor_bp, valor_c, base, de.isoformat(), criado_por),
    ).lastrowid


def _taxa(con: sqlite3.Connection, dados: dict[str, Any], criado_por: str) -> int:
    meio = id_meio(con, _obrigatorio(dados, "meio"))
    forma = id_forma(con, _obrigatorio(dados, "forma"))
    parcelas = int(dados.get("parcelas", 1))
    de = parse_data(_obrigatorio(dados, "vigente_de"))
    bp = bp_de_percentual(_obrigatorio(dados, "taxa"))
    anteriores = con.execute(
        "SELECT * FROM taxa_meio WHERE meio_id = ? AND forma_id = ? AND parcelas = ?"
        " AND vigente_ate IS NULL",
        (meio, forma, parcelas),
    ).fetchall()
    _fechar_vigencia(con, "taxa_meio", anteriores, de)
    return con.execute(
        "INSERT INTO taxa_meio (meio_id, forma_id, parcelas, taxa_bp, vigente_de, criado_por)"
        " VALUES (?,?,?,?,?,?)",
        (meio, forma, parcelas, bp, de.isoformat(), criado_por),
    ).lastrowid


def _meio(con: sqlite3.Connection, dados: dict[str, Any], criado_por: str) -> int:
    nome = str(_obrigatorio(dados, "nome")).strip()
    de = parse_data(_obrigatorio(dados, "vigente_de"))
    if dados.get("substitui"):
        antigo = con.execute(
            "SELECT * FROM meio_recebimento WHERE nome = ? COLLATE NOCASE AND vigente_ate IS NULL",
            (str(dados["substitui"]).strip(),),
        ).fetchone()
        if not antigo:
            raise ErroFinanceiro(f"Meio a substituir não está vigente: {dados['substitui']!r}.")
        if date.fromisoformat(antigo["vigente_de"]) >= de:
            raise ErroFinanceiro("A troca de meio deve começar depois da vigência do meio atual.")
        con.execute(
            "UPDATE meio_recebimento SET vigente_ate = ?, ativo = 0 WHERE id = ?",
            (_dia_anterior(de), antigo["id"]),
        )
    return con.execute(
        "INSERT INTO meio_recebimento (nome, tipo, vigente_de) VALUES (?,?,?)",
        (nome, _obrigatorio(dados, "tipo"), de.isoformat()),
    ).lastrowid


def _modelo_pacote(con: sqlite3.Connection, dados: dict[str, Any], criado_por: str) -> int:
    nome = str(_obrigatorio(dados, "nome")).strip()
    modo = _obrigatorio(dados, "modo_cobranca")
    if modo not in MODOS_COBRANCA:
        raise ErroFinanceiro(f"Modo de cobrança inválido: {modo!r}.")
    de = parse_data(_obrigatorio(dados, "vigente_de"))
    total = parse_brl(dados["preco_total"]) if dados.get("preco_total") else None
    unitario = parse_brl(dados["preco_unitario"]) if dados.get("preco_unitario") else None
    anteriores = con.execute(
        "SELECT * FROM modelo_pacote WHERE nome = ? AND vigente_ate IS NULL", (nome,)
    ).fetchall()
    _fechar_vigencia(con, "modelo_pacote", anteriores, de)
    return con.execute(
        "INSERT INTO modelo_pacote (nome, modo_cobranca, n_sessoes, preco_total_centavos,"
        " preco_unitario_centavos, n_parcelas, intervalo_dias, validade_dias, falta_consome,"
        " falta_cobrada, antecedencia_horas, vigente_de, criado_por)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            nome,
            modo,
            int(_obrigatorio(dados, "n_sessoes")),
            total,
            unitario,
            int(dados.get("n_parcelas", 1)),
            int(dados.get("intervalo_dias", 30)),
            int(dados.get("validade_dias", 90)),
            int(bool(dados.get("falta_consome", False))),
            int(bool(dados.get("falta_cobrada", False))),
            int(dados.get("antecedencia_horas", 24)),
            de.isoformat(),
            criado_por,
        ),
    ).lastrowid


def materializar_comissao(path: Path | str, dados: dict[str, Any], *, criado_por: str) -> int:
    return _montar(path, _comissao, dados, criado_por)


def materializar_taxa(path: Path | str, dados: dict[str, Any], *, criado_por: str) -> int:
    return _montar(path, _taxa, dados, criado_por)


def materializar_meio(path: Path | str, dados: dict[str, Any], *, criado_por: str) -> int:
    return _montar(path, _meio, dados, criado_por)


def materializar_modelo_pacote(path: Path | str, dados: dict[str, Any], *, criado_por: str) -> int:
    return _montar(path, _modelo_pacote, dados, criado_por)
