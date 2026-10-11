import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mcp_engine.modules.financeiro import consultas, integridade, params
from mcp_engine.modules.financeiro.db import ErroFinanceiro, abrir, transacao
from mcp_engine.modules.financeiro.lancamentos import (
    Quem,
    dividir,
    estornar_recebimento,
    fechar_mes,
    inserir_despesa,
    inserir_recebimento,
)
from mcp_engine.modules.financeiro.money import formatar_brl, parse_brl
from tests.financeiro_util import criar_ledger, semear

QUEM = Quem("gestor@clinica.test", "admin")
HOJE = date(2026, 6, 15)
SEM_PRAZO = settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@given(
    valor=st.integers(1, 10**9),
    taxa_bp=st.integers(0, 10000),
    tipo=st.sampled_from(["percentual", "fixo"]),
    base=st.sampled_from(["bruto", "liquido"]),
    valor_bp=st.integers(0, 10000),
    fixo=st.integers(0, 10**9),
)
@settings(max_examples=500, deadline=None)
def test_f6_repasse_mais_clinica_igual_valor_menos_taxa(valor, taxa_bp, tipo, base, valor_bp, fixo):
    taxa, liquido, repasse, _ = dividir(
        valor,
        taxa_bp,
        tipo,
        base,
        valor_bp if tipo == "percentual" else None,
        fixo if tipo == "fixo" else None,
    )
    clinica = liquido - repasse
    assert repasse + clinica == valor - taxa
    assert 0 <= repasse <= liquido and clinica >= 0
    assert 0 <= taxa <= valor


@given(centavos=st.integers(0, 10**9))
@settings(max_examples=300, deadline=None)
def test_f4_ida_e_volta_de_dinheiro_em_centavos(centavos):
    texto = formatar_brl(centavos)
    assert parse_brl(texto) == centavos


operacao = st.one_of(
    st.tuples(
        st.just("rec"),
        st.integers(100, 500_000),
        st.sampled_from(["pix", "debito", "credito", "dinheiro"]),
        st.integers(1, 28),
        st.sampled_from(["pac_a", "pac_b"]),
        st.sampled_from(["adultos", "infantil", None]),
    ),
    st.tuples(st.just("est"), st.integers(0, 50), st.integers(1, 100)),
    st.tuples(st.just("desp"), st.integers(100, 100_000), st.integers(1, 28)),
)


def _executar(ops):
    raiz = Path(tempfile.mkdtemp())
    path = criar_ledger(raiz)
    ids = semear(path)
    meios = {"pix": "Sicoob", "debito": "Stone", "credito": "Stone", "dinheiro": "Caixa"}
    con = abrir(path)
    criados: list[int] = []
    try:
        con.execute("BEGIN IMMEDIATE")
        cat = params.garantir_categoria(con, "variada", "GERAL")
        con.execute("COMMIT")
        for op in ops:
            try:
                with transacao(con):
                    if op[0] == "rec":
                        _, valor, forma, dia, pac, unid = op
                        data = date(2026, 5, dia)
                        meio = params.meio_vigente(con, meios[forma], data)
                        rid, _ = inserir_recebimento(
                            con,
                            {
                                "data_pagamento": data,
                                "paciente_id": ids[pac],
                                "forma_id": params.id_forma(con, forma),
                                "meio_id": meio,
                                "valor": valor,
                                "tipo": "sessao",
                                "profissional_id": ids["ana"] if pac == "pac_a" else ids["bruno"],
                                "unidade_id": ids[unid] if unid else None,
                            },
                            QUEM,
                            hoje=HOJE,
                        )
                        criados.append(rid)
                    elif op[0] == "est" and criados:
                        orig = criados[op[1] % len(criados)]
                        total = con.execute(
                            "SELECT valor_centavos FROM recebimento WHERE id = ?", (orig,)
                        ).fetchone()[0]
                        estornar_recebimento(
                            con, orig, max(1, total * op[2] // 100), "teste", QUEM, hoje=HOJE
                        )
                    elif op[0] == "desp":
                        inserir_despesa(
                            con,
                            {
                                "data": date(2026, 5, op[2]),
                                "descricao": "d",
                                "categoria_id": cat,
                                "valor": op[1],
                            },
                            QUEM,
                        )
            except ErroFinanceiro:
                pass
        return con, path
    except BaseException:
        con.close()
        raise


@given(st.lists(operacao, min_size=1, max_size=25))
@SEM_PRAZO
def test_f1_a_f6_com_lancamentos_estornos_aleatorios(ops):
    con, _ = _executar(ops)
    try:
        assert integridade.verificar(con) == []
        total = con.execute("SELECT COALESCE(SUM(valor_centavos),0) FROM recebimento").fetchone()[0]
        for tabela, ref in (("forma_pagamento", "forma_id"), ("unidade", "unidade_id")):
            soma = con.execute(
                f"SELECT COALESCE(SUM(r.valor_centavos),0) FROM recebimento r"
                f" JOIN {tabela} t ON t.id = r.{ref}"
            ).fetchone()[0]
            nulos = con.execute(
                f"SELECT COALESCE(SUM(valor_centavos),0) FROM recebimento WHERE {ref} IS NULL"
            ).fetchone()[0]
            assert soma + nulos == total
        sobra = con.execute(
            "SELECT COUNT(*) FROM recebimento r JOIN repasse p ON p.recebimento_id = r.id"
            " WHERE p.repasse_centavos + p.parte_clinica_centavos <> r.valor_centavos -"
            " p.taxa_centavos"
        ).fetchone()[0]
        assert sobra == 0
        estornos = con.execute(
            "SELECT o.valor_centavos v, -SUM(e.valor_centavos) s FROM recebimento o"
            " JOIN recebimento e ON e.estorno_de = o.id GROUP BY o.id"
        ).fetchall()
        assert all(r["s"] <= r["v"] for r in estornos)
    finally:
        con.close()


@given(st.lists(operacao, min_size=1, max_size=15), st.integers(1, 28))
@SEM_PRAZO
def test_f5_mes_fechado_recusa_e_hash_estavel(ops, dia):
    con, path = _executar(ops)
    try:
        with transacao(con):
            digest = fechar_mes(con, "2026-05", None, QUEM, hoje=HOJE)
        assert integridade.verificar(con) == []
        ids = {r["nome"]: r["id"] for r in con.execute("SELECT id, nome FROM paciente_ref")}
        with pytest.raises(ErroFinanceiro, match="Mês fechado"):
            with transacao(con):
                inserir_recebimento(
                    con,
                    {
                        "data_pagamento": date(2026, 5, dia),
                        "paciente_id": ids["Paciente A"],
                        "forma_id": 1,
                        "valor": 100,
                        "tipo": "sessao",
                    },
                    QUEM,
                    hoje=HOJE,
                )
        from mcp_engine.modules.financeiro.lancamentos import hash_mes

        assert hash_mes(con, "2026-05", None) == digest
    finally:
        con.close()


@given(
    st.lists(
        st.tuples(st.integers(100, 300_000), st.sampled_from(["pix", "debito"])),
        min_size=1,
        max_size=12,
    )
)
@SEM_PRAZO
def test_f10_a_receber_nunca_negativo_e_credito_explicito(pagamentos):
    raiz = Path(tempfile.mkdtemp())
    path = criar_ledger(raiz)
    ids = semear(path)
    params.materializar_modelo_pacote(
        path,
        {
            "nome": "P",
            "modo_cobranca": "antecipado",
            "n_sessoes": 4,
            "preco_total": "1000",
            "vigente_de": "2026-01-01",
        },
        criado_por="t",
    )
    from mcp_engine.modules.financeiro import sessoes

    con = abrir(path)
    try:
        with transacao(con):
            pk = sessoes.registrar_pacote(
                con, paciente_id=ids["pac_a"], modelo_nome="P", inicio=date(2026, 5, 1), quem=QUEM
            )["pacote_id"]
        pago = 0
        for i, (valor, forma) in enumerate(pagamentos):
            with transacao(con):
                inserir_recebimento(
                    con,
                    {
                        "data_pagamento": date(2026, 5, 2 + i % 20),
                        "paciente_id": ids["pac_a"],
                        "forma_id": params.id_forma(con, forma),
                        "meio_id": params.meio_vigente(
                            con, "Sicoob" if forma == "pix" else "Stone", date(2026, 5, 2)
                        ),
                        "valor": valor,
                        "tipo": "pacote",
                        "pacote_id": pk,
                        "profissional_id": ids["ana"],
                    },
                    QUEM,
                    hoje=HOJE,
                )
            pago += valor
            r = consultas.a_receber_calc(con, "2026-12-31")[0]
            assert r["a_receber_centavos"] >= 0 and r["credito_centavos"] >= 0
            assert r["a_receber_centavos"] - r["credito_centavos"] == 100000 - pago
        assert integridade.verificar(con) == []
    finally:
        con.close()


@given(st.lists(st.integers(1, 40), min_size=1, max_size=8), st.integers(1, 99))
@SEM_PRAZO
def test_f7_no_maximo_uma_regra_vigente_e_historia_preservada(saltos, pct):
    raiz = Path(tempfile.mkdtemp())
    path = criar_ledger(raiz)
    ids = semear(path)
    inicio = date(2025, 1, 1)
    marcos = [inicio]
    for s in saltos:
        marcos.append(marcos[-1] + timedelta(days=s))
        params.materializar_comissao(
            path,
            {
                "profissional": "Ana Souza",
                "tipo": "percentual",
                "valor": str((pct + len(marcos)) % 100),
                "base": "bruto",
                "vigente_de": marcos[-1].isoformat(),
            },
            criado_por="t",
        )
    con = abrir(path)
    try:
        assert [v for v in integridade.verificar(con) if v["invariante"] == "F7"] == []
        for dia in marcos + [marcos[-1] + timedelta(days=100)]:
            n = con.execute(
                "SELECT COUNT(*) FROM comissao_regra WHERE profissional_id = ? AND servico IS NULL"
                " AND vigente_de <= ? AND (vigente_ate IS NULL OR vigente_ate >= ?)",
                (ids["ana"], dia.isoformat(), dia.isoformat()),
            ).fetchone()[0]
            assert n <= 1
            if dia >= inicio:
                regra = params.comissao_vigente(con, ids["ana"], None, 1, None, dia)
                assert regra["vigente_de"] <= dia.isoformat()
    finally:
        con.close()


@given(
    st.integers(1, 4),
    st.lists(
        st.sampled_from(["COMPLETED", "SCHEDULED", "CANCELLED", "falta"]), min_size=1, max_size=12
    ),
)
@SEM_PRAZO
def test_f9_sessoes_consumidas_nunca_passam_do_total(total, eventos):
    from mcp_engine.modules.financeiro import sessoes

    raiz = Path(tempfile.mkdtemp())
    path = criar_ledger(raiz)
    ids = semear(path)
    params.materializar_modelo_pacote(
        path,
        {
            "nome": "P",
            "modo_cobranca": "antecipado",
            "n_sessoes": total,
            "preco_total": "500",
            "falta_consome": True,
            "vigente_de": "2026-01-01",
        },
        criado_por="t",
    )
    con = abrir(path)
    try:
        with transacao(con):
            pk = sessoes.registrar_pacote(
                con, paciente_id=ids["pac_a"], modelo_nome="P", inicio=date(2026, 2, 1), quem=QUEM
            )["pacote_id"]
        for i, ev in enumerate(eventos):
            with transacao(con):
                status = (
                    "COMPLETED"
                    if ev == "COMPLETED"
                    else ("CANCELLED" if ev == "CANCELLED" else "SCHEDULED")
                )
                sid = sessoes.upsert_sessao(
                    con,
                    agenda_id=f"a{i}",
                    paciente_id=ids["pac_a"],
                    inicio_utc=f"2026-02-{1 + i:02d}T13:00:00Z",
                    status_agenda=status,
                    pacote_id=pk,
                )
                if ev == "falta":
                    sessoes.registrar_falta(con, sid, "falta", None, QUEM)
            assert sessoes.consumidas(con, pk) <= total
        assert [v for v in integridade.verificar(con) if v["invariante"] == "F9"] == []
    finally:
        con.close()
