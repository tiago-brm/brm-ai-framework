import sqlite3
from datetime import date

import pytest

from mcp_engine.modules.financeiro import handlers as h
from mcp_engine.modules.financeiro import integridade, params, sessoes
from mcp_engine.modules.financeiro.db import ErroFinanceiro, abrir, transacao
from mcp_engine.modules.financeiro.lancamentos import Quem
from mcp_engine.modules.financeiro.money import (
    ValorInvalido,
    bp_de_percentual,
    formatar_brl,
    meio_para_cima,
    parse_brl,
)
from tests.financeiro_util import criar_ledger, ctx, recebimento, semear

HOJE = date(2026, 2, 10)


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "hoje", lambda: HOJE)
    path = criar_ledger(tmp_path)
    ids = semear(path)
    return tmp_path, path, ids


def rec(tmp, role="admin", actor="gestor@clinica.test", key=None, **kw):
    return h.registrar_recebimento(ctx(tmp, role, actor, key), recebimento(**kw))


def consulta(tmp, nome, periodo="2026-02", role="admin", actor="gestor@clinica.test", **filtros):
    args = {"consulta": nome, "periodo": periodo}
    if filtros:
        args["filtros"] = filtros
    return h.consultar_financeiro(ctx(tmp, role, actor), args)


def linhas(path, sql, *p):
    con = abrir(path)
    try:
        return [dict(r) for r in con.execute(sql, p)]
    finally:
        con.close()


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("R$ 1.234,56", 123456),
        ("275", 27500),
        ("275,5", 27550),
        ("275,50", 27550),
        ("275.50", 27550),
        ("1.234.567,89", 123456789),
        (" r$ 10 ", 1000),
        ("0,01", 1),
        (275, 27500),
    ],
)
def test_parse_brl_validos(texto, esperado):
    assert parse_brl(texto) == esperado


@pytest.mark.parametrize(
    "texto",
    [
        "1.234",
        "1,234,56",
        "abc",
        "",
        "-5",
        "1.2.3",
        "12,345",
        "1,234.56",
        "R$",
        "275,",
        ",5",
        "1.234,5.6",
        "99999999999",
        2.5,
        True,
        None,
    ],
)
def test_parse_brl_recusa_ambiguidade(texto):
    with pytest.raises(ValorInvalido):
        parse_brl(texto)


def test_formatar_e_percentual():
    assert formatar_brl(123456) == "R$ 1.234,56"
    assert formatar_brl(-5) == "-R$ 0,05"
    assert bp_de_percentual("12,5%") == 1250
    assert bp_de_percentual("40") == 4000
    with pytest.raises(ValorInvalido):
        bp_de_percentual("101")
    assert meio_para_cima(5, 2) == 3 and meio_para_cima(7, 3) == 2


def test_banco_strict_wal_fk(ambiente):
    _, path, _ = ambiente
    assert sqlite3.sqlite_version_info >= (3, 37, 0)
    con = abrir(path)
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO recebimento (data_pagamento, data_competencia, paciente_id, forma_id,"
            " valor_centavos, tipo, criado_por, criado_em) VALUES ('2026-02-01','2026-02-01',1,1,"
            " 'abc','sessao','x','x')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO recebimento (data_pagamento, data_competencia, paciente_id, forma_id,"
            " valor_centavos, tipo, criado_por, criado_em) VALUES ('2026-02-01','2026-02-01',999,1,"
            " 100,'sessao','x','x')"
        )
    con.close()


def test_imutabilidade_por_gatilho(ambiente):
    tmp, path, _ = ambiente
    rid = rec(tmp)["recebimento_id"]
    h.registrar_despesa(
        ctx(tmp),
        {
            "data": "2026-02-05",
            "descricao": "Luz",
            "categoria": "ENERGISA",
            "grupo": "fixa",
            "valor": "100",
        },
    )
    con = abrir(path)
    for sql in (
        f"UPDATE recebimento SET valor_centavos = 1 WHERE id = {rid}",
        f"UPDATE recebimento SET data_pagamento = '2026-01-01' WHERE id = {rid}",
        f"DELETE FROM recebimento WHERE id = {rid}",
        f"UPDATE repasse SET repasse_centavos = 0 WHERE recebimento_id = {rid}",
        f"DELETE FROM repasse WHERE recebimento_id = {rid}",
        "UPDATE despesa SET valor_centavos = 1 WHERE id = 1",
        "DELETE FROM despesa WHERE id = 1",
        "UPDATE comissao_regra SET valor_bp = 1",
        "DELETE FROM taxa_meio",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="imutavel"):
            con.execute(sql)
    con.execute(
        "UPDATE recebimento SET comprovante_ref = 'a.pdf', nf_status = 'emitida', nf_numero = '9'"
    )
    con.execute("UPDATE despesa SET comprovante_ref = 'b.pdf'")
    con.close()


def test_estorno_contra_lancamento_e_saldo(ambiente):
    tmp, path, _ = ambiente
    rid = rec(tmp, forma="debito", meio="Stone")["recebimento_id"]
    e1 = h.estornar_lancamento(
        ctx(tmp), {"tipo": "recebimento", "lancamento_id": rid, "valor": "100", "motivo": "parcial"}
    )
    assert e1["estornado"]
    with pytest.raises(ErroFinanceiro, match="excede"):
        h.estornar_lancamento(
            ctx(tmp), {"tipo": "recebimento", "lancamento_id": rid, "valor": "200", "motivo": "x"}
        )
    e2 = h.estornar_lancamento(
        ctx(tmp), {"tipo": "recebimento", "lancamento_id": rid, "motivo": "resto"}
    )
    linhas_rep = linhas(
        path,
        "SELECT SUM(taxa_centavos) t, SUM(repasse_centavos) r, SUM(parte_clinica_centavos) c"
        " FROM repasse",
    )
    assert linhas_rep == [{"t": 0, "r": 0, "c": 0}]
    assert linhas(path, "SELECT SUM(valor_centavos) s FROM recebimento")[0]["s"] == 0
    assert e2["estorno_id"] > e1["estorno_id"]
    with pytest.raises(ErroFinanceiro, match="totalmente estornado"):
        h.estornar_lancamento(
            ctx(tmp), {"tipo": "recebimento", "lancamento_id": rid, "motivo": "x"}
        )
    assert integridade.verificar(abrir(path)) == []


def test_trigger_recusa_estorno_acima_do_saldo_direto(ambiente):
    tmp, path, ids = ambiente
    rid = rec(tmp)["recebimento_id"]
    con = abrir(path)
    with pytest.raises(sqlite3.IntegrityError, match="estorno_acima_do_saldo"):
        con.execute(
            "INSERT INTO recebimento (data_pagamento, data_competencia, paciente_id, forma_id,"
            " valor_centavos, tipo, estorno_de, criado_por, criado_em) VALUES"
            f" ('2026-02-10','2026-02-10',{ids['pac_a']},1,-99999,'estorno',{rid},'x','x')"
        )
    with pytest.raises(sqlite3.IntegrityError, match="estorno_original_invalido"):
        con.execute(
            "INSERT INTO recebimento (data_pagamento, data_competencia, paciente_id, forma_id,"
            " valor_centavos, tipo, estorno_de, criado_por, criado_em) VALUES"
            f" ('2026-02-10','2026-02-10',{ids['pac_b']},1,-100,'estorno',{rid},'x','x')"
        )
    con.close()


def test_estorno_de_despesa(ambiente):
    tmp, path, _ = ambiente
    d = h.registrar_despesa(
        ctx(tmp),
        {
            "data": "2026-02-05",
            "descricao": "Material",
            "categoria": "MATERIAL",
            "grupo": "variada",
            "valor": "80",
        },
    )
    h.estornar_lancamento(
        ctx(tmp),
        {"tipo": "despesa", "lancamento_id": d["despesa_id"], "valor": "30", "motivo": "devolução"},
    )
    with pytest.raises(ErroFinanceiro):
        h.estornar_lancamento(
            ctx(tmp),
            {"tipo": "despesa", "lancamento_id": d["despesa_id"], "valor": "60", "motivo": "x"},
        )
    assert linhas(path, "SELECT SUM(valor_centavos) s FROM despesa")[0]["s"] == 5000


def test_mes_fechado_e_reabertura(ambiente):
    tmp, path, _ = ambiente
    rid = rec(tmp, data_pagamento="2026-01-15")["recebimento_id"]
    res = h.fechar_mes(ctx(tmp), {"mes": "2026-01"})
    assert res["fechado"] and len(res["hash"]) == 64
    with pytest.raises(ErroFinanceiro, match="Mês fechado"):
        rec(tmp, data_pagamento="2026-01-20", valor="300")
    with pytest.raises(ErroFinanceiro, match="Mês fechado"):
        h.registrar_despesa(
            ctx(tmp),
            {
                "data": "2026-01-20",
                "descricao": "x",
                "categoria": "MATERIAL",
                "grupo": "variada",
                "valor": "1",
            },
        )
    est = h.estornar_lancamento(
        ctx(tmp), {"tipo": "recebimento", "lancamento_id": rid, "motivo": "ajuste"}
    )
    datas = linhas(
        path,
        "SELECT data_pagamento, data_competencia FROM recebimento WHERE id = ?",
        est["estorno_id"],
    )
    assert datas == [{"data_pagamento": "2026-02-10", "data_competencia": "2026-02-10"}]
    with pytest.raises(ErroFinanceiro, match="já está fechado"):
        h.fechar_mes(ctx(tmp), {"mes": "2026-01"})
    with pytest.raises(ErroFinanceiro, match="super_admin"):
        h.reabrir_mes(ctx(tmp, "admin"), {"mes": "2026-01", "motivo": "erro"})
    h.reabrir_mes(
        ctx(tmp, "super_admin", "root@x"), {"mes": "2026-01", "motivo": "erro de digitação"}
    )
    rec(tmp, data_pagamento="2026-01-20", valor="300")


def test_nao_fecha_mes_corrente_nem_com_violacao(ambiente):
    tmp, path, _ = ambiente
    with pytest.raises(ErroFinanceiro, match="encerrados"):
        h.fechar_mes(ctx(tmp), {"mes": "2026-02"})
    rec(tmp, data_pagamento="2026-01-15")
    con = abrir(path)
    con.execute("DROP TRIGGER repasse_no_update")
    con.execute(
        "UPDATE repasse SET parte_clinica_centavos = parte_clinica_centavos + 1,"
        " liquido_centavos = liquido_centavos + 1"
    )
    con.close()
    with pytest.raises(ErroFinanceiro, match="violação"):
        h.fechar_mes(ctx(tmp), {"mes": "2026-01"})


def test_hash_do_fechamento_detecta_edicao_externa(ambiente):
    tmp, path, _ = ambiente
    rec(tmp, data_pagamento="2026-01-15")
    h.fechar_mes(ctx(tmp), {"mes": "2026-01"})
    assert integridade.verificar(abrir(path)) == []
    con = abrir(path)
    con.execute("DROP TRIGGER recebimento_no_update")
    con.execute("UPDATE recebimento SET valor_centavos = 1")
    con.close()
    invs = {v["invariante"] for v in integridade.verificar(abrir(path))}
    assert "F5" in invs and "F6" in invs


def test_repasse_bases_e_arredondamento(ambiente):
    tmp, path, _ = ambiente
    a = rec(tmp, forma="debito", meio="Stone")
    assert a["repasse"] == {
        "taxa_centavos": 413,
        "repasse_centavos": 10835,
        "parte_clinica_centavos": 16252,
    }
    b = rec(
        tmp,
        paciente="Paciente B",
        forma="debito",
        meio="Stone",
        valor="330",
        data_pagamento="2026-02-03",
    )
    assert b["repasse"]["repasse_centavos"] == 16500
    assert b["repasse"]["taxa_centavos"] == 495
    assert b["repasse"]["parte_clinica_centavos"] == 33000 - 495 - 16500


def test_repasse_limitado_quando_comissao_excede_liquido(ambiente):
    tmp, path, _ = ambiente
    params.materializar_taxa(
        path,
        {"meio": "Sicoob", "forma": "pix", "taxa": "10", "vigente_de": "2025-01-01"},
        criado_por="t",
    )
    params.materializar_comissao(
        path,
        {
            "profissional": "Bruno Lima",
            "tipo": "percentual",
            "valor": "95",
            "base": "bruto",
            "vigente_de": "2026-01-01",
        },
        criado_por="t",
    )
    r = rec(tmp, paciente="Paciente B", valor="100")
    assert r["repasse"]["repasse_centavos"] == 9000 and r["repasse"]["parte_clinica_centavos"] == 0
    assert linhas(
        path, "SELECT limitado FROM repasse WHERE recebimento_id = ?", r["recebimento_id"]
    ) == [{"limitado": 1}]


def test_vigencia_de_comissao_antes_no_dia_e_depois(ambiente):
    tmp, path, _ = ambiente
    params.materializar_comissao(
        path,
        {
            "profissional": "Ana Souza",
            "tipo": "percentual",
            "valor": "50",
            "base": "liquido",
            "vigente_de": "2026-02-10",
        },
        criado_por="t",
    )
    antes = rec(tmp, data_pagamento="2026-02-09")
    no_dia = rec(tmp, data_pagamento="2026-02-10")
    depois = rec(tmp, data_pagamento="2026-02-10", valor="300")
    assert antes["repasse"]["repasse_centavos"] == 11000
    assert no_dia["repasse"]["repasse_centavos"] == 13750
    assert depois["repasse"]["repasse_centavos"] == 15000
    regras = linhas(
        path,
        "SELECT vigente_de, vigente_ate FROM comissao_regra WHERE profissional_id = 1 ORDER BY id",
    )
    assert regras == [
        {"vigente_de": "2025-01-01", "vigente_ate": "2026-02-09"},
        {"vigente_de": "2026-02-10", "vigente_ate": None},
    ]
    snap = linhas(
        path,
        "SELECT repasse_centavos FROM repasse WHERE recebimento_id = ?",
        antes["recebimento_id"],
    )
    assert snap == [{"repasse_centavos": 11000}]
    assert integridade.verificar(abrir(path)) == []


def test_vigencia_regressiva_recusada(ambiente):
    _, path, _ = ambiente
    with pytest.raises(ErroFinanceiro, match="nova vigência"):
        params.materializar_comissao(
            path,
            {
                "profissional": "Ana Souza",
                "tipo": "percentual",
                "valor": "50",
                "base": "liquido",
                "vigente_de": "2024-01-01",
            },
            criado_por="t",
        )


def test_precedencia_de_comissao_e_empate(ambiente):
    tmp, path, _ = ambiente
    for extra in (
        {"servico": "avaliacao", "valor": "60"},
        {"forma": "pix", "valor": "30"},
        {"servico": "avaliacao", "forma": "pix", "valor": "70"},
    ):
        params.materializar_comissao(
            path,
            {
                "profissional": "Ana Souza",
                "tipo": "percentual",
                "base": "bruto",
                "vigente_de": "2025-06-01",
                **extra,
            },
            criado_por="t",
        )
    base = rec(tmp, forma="ted", meio="Sicoob")
    assert base["repasse"]["repasse_centavos"] == 11000
    so_forma = rec(tmp, valor="100", data_pagamento="2026-02-04")
    assert so_forma["repasse"]["repasse_centavos"] == 3000
    so_servico = rec(
        tmp,
        valor="100",
        servico="Avaliação",
        forma="ted",
        meio="Sicoob",
        data_pagamento="2026-02-05",
    )
    assert so_servico["repasse"]["repasse_centavos"] == 6000
    ambos = rec(tmp, valor="100", servico="avaliacao", data_pagamento="2026-02-06")
    assert ambos["repasse"]["repasse_centavos"] == 7000
    con = abrir(path)
    for _ in range(2):
        con.execute(
            "INSERT INTO comissao_regra (profissional_id, servico, tipo, valor_centavos, base,"
            " vigente_de, criado_por) VALUES (1, 'x', 'fixo', 1000, 'bruto', '2025-06-01', 't')"
        )
    with pytest.raises(ErroFinanceiro, match="empatadas"):
        h.registrar_recebimento(
            ctx(tmp), recebimento(servico="x", valor="100", data_pagamento="2026-02-07")
        )
    assert "F7" in {v["invariante"] for v in integridade.verificar(con)}
    con.close()


def test_sem_regra_de_comissao_e_erro_explicito(ambiente):
    tmp, path, _ = ambiente
    con = abrir(path)
    with transacao(con):
        params.garantir_profissional(con, "Carla Nova")
        params.garantir_paciente(con, "Paciente C", profissional_id=3)
    con.close()
    with pytest.raises(ErroFinanceiro, match="Nenhuma regra de comissão"):
        rec(tmp, paciente="Paciente C")
    assert linhas(path, "SELECT COUNT(*) n FROM recebimento")[0]["n"] == 0


def test_troca_de_maquininha_no_meio_do_mes(ambiente):
    tmp, path, _ = ambiente
    params.materializar_meio(
        path,
        {
            "nome": "Nova Maq",
            "tipo": "maquininha",
            "vigente_de": "2026-02-16",
            "substitui": "Stone",
        },
        criado_por="t",
    )
    params.materializar_taxa(
        path,
        {"meio": "Nova Maq", "forma": "debito", "taxa": "1", "vigente_de": "2026-02-16"},
        criado_por="t",
    )
    velho = rec(tmp, forma="debito", meio="Stone", data_pagamento="2026-02-10")
    novo = rec(tmp, forma="debito", meio="Nova Maq", data_pagamento="2026-02-16", valor="200")
    assert velho["repasse"]["taxa_centavos"] == 413
    assert novo["repasse"]["taxa_centavos"] == 200
    with pytest.raises(ErroFinanceiro, match="não está vigente"):
        rec(tmp, forma="debito", meio="Stone", data_pagamento="2026-02-17", valor="10")
    with pytest.raises(ErroFinanceiro, match="não está vigente"):
        rec(tmp, forma="debito", meio="Nova Maq", data_pagamento="2026-02-15", valor="10")
    assert integridade.verificar(abrir(path)) == []


def test_taxa_nova_vigencia_nao_reescreve_historico(ambiente):
    tmp, path, _ = ambiente
    antes = rec(tmp, forma="credito", meio="Stone", parcelas=1)
    params.materializar_taxa(
        path,
        {"meio": "Stone", "forma": "credito", "taxa": "5", "vigente_de": "2026-02-11"},
        criado_por="t",
    )
    assert linhas(
        path, "SELECT taxa_centavos FROM repasse WHERE recebimento_id = ?", antes["recebimento_id"]
    ) == [{"taxa_centavos": 963}]
    depois = rec(tmp, forma="credito", meio="Stone", data_pagamento="2026-02-11", valor="100")
    assert depois["repasse"]["taxa_centavos"] == 500


def test_duplicidade_e_atipico_e_retroativo(ambiente):
    tmp, path, _ = ambiente
    primeiro = rec(tmp)
    assert primeiro["registrado"]
    dup = rec(tmp)
    assert dup["registrado"] is False and dup["duplicidade_suspeita"]
    forcado = h.registrar_recebimento(ctx(tmp), recebimento(confirmar_duplicidade=True))
    assert forcado["registrado"] and forcado["fatos"]["duplicidade_suspeita"]
    assert consulta(tmp, "pendencias")["dados"]["abertas"][0]["tipo"] == "duplicidade_suspeita"

    sec = "sec@clinica.test"
    atip = rec(tmp, role="viewer", actor=sec, valor="2750", data_pagamento="2026-02-04")
    assert atip["registrado"] is False and atip["requer_gestor"] and atip["fatos"]["valor_atipico"]
    ok_gestor = rec(tmp, valor="2750", data_pagamento="2026-02-04")
    assert ok_gestor["registrado"] and ok_gestor["fatos"]["valor_atipico"]
    retro = rec(
        tmp,
        role="viewer",
        actor=sec,
        data_pagamento="2026-01-20",
        valor="275",
        paciente="Paciente B",
    )
    assert retro["registrado"] is False and retro["fatos"]["retroativo_dias"] == 21
    normal = rec(
        tmp,
        role="viewer",
        actor=sec,
        data_pagamento="2026-02-09",
        valor="275",
        paciente="Paciente B",
    )
    assert normal["registrado"]


def test_paciente_desconhecido_e_a_confirmar(ambiente):
    tmp, path, _ = ambiente
    r = rec(tmp, paciente="Fulano Inexistente")
    assert r["registrado"] is False and "Agenda" in r["motivo"]
    con = abrir(path)
    with transacao(con):
        params.garantir_paciente(con, "Paciente Novo", profissional_id=1, status="a_confirmar")
    con.close()
    sec = rec(tmp, role="viewer", actor="s@x", paciente="Paciente Novo")
    assert sec["registrado"] is False and sec["fatos"]["paciente_a_confirmar"]
    gestor = rec(tmp, paciente="Paciente Novo")
    assert gestor["registrado"] and gestor["fatos"]["paciente_a_confirmar"]


def test_idempotencia_pela_chave_da_operacao(ambiente):
    tmp, path, _ = ambiente
    a = rec(tmp, key="op-1")
    b = rec(tmp, key="op-1")
    assert b["reutilizado"] and b["recebimento_id"] == a["recebimento_id"]
    assert linhas(path, "SELECT COUNT(*) n FROM recebimento")[0]["n"] == 1


def test_entradas_invalidas(ambiente):
    tmp, _, _ = ambiente
    for kw in (
        {"valor": "1.234"},
        {"valor": "0"},
        {"forma": "cheque"},
        {"data_pagamento": "31/02/2026"},
        {"meio": "Inexistente"},
        {"tipo": "estorno"},
    ):
        with pytest.raises(ErroFinanceiro):
            rec(tmp, **kw)
    with pytest.raises(ErroFinanceiro):
        h.registrar_recebimento(ctx(tmp), {"paciente": "Paciente A"})


def test_anexar_comprovante_e_nf(ambiente):
    tmp, path, _ = ambiente
    rid = rec(tmp)["recebimento_id"]
    h.anexar_comprovante(
        ctx(tmp), {"tipo": "recebimento", "lancamento_id": rid, "comprovante_ref": "pix_0202.pdf"}
    )
    for ref in ("../etc/passwd", "a/b.pdf", "", "x" * 300):
        with pytest.raises(ErroFinanceiro):
            h.anexar_comprovante(
                ctx(tmp), {"tipo": "recebimento", "lancamento_id": rid, "comprovante_ref": ref}
            )
    h.atualizar_status_nf(
        ctx(tmp), {"recebimento_id": rid, "nf_status": "emitida", "nf_numero": "8462"}
    )
    outro = rec(tmp, paciente="Paciente B", data_pagamento="2026-02-04")["recebimento_id"]
    r = h.atualizar_status_nf(
        ctx(tmp), {"recebimento_id": outro, "nf_status": "emitida", "nf_numero": "8462"}
    )
    assert r["avisos"]
    with pytest.raises(ErroFinanceiro):
        h.atualizar_status_nf(ctx(tmp), {"recebimento_id": rid, "nf_status": "emitida"})
    assert linhas(path, "SELECT comprovante_ref, nf_numero FROM recebimento WHERE id = ?", rid) == [
        {"comprovante_ref": "pix_0202.pdf", "nf_numero": "8462"}
    ]


def popular(tmp):
    seq = [
        ("2026-02-02", "Paciente A", "pix", "Sicoob", "275"),
        ("2026-02-02", "Paciente A", "debito", "Stone", "230,50"),
        ("2026-02-03", "Paciente B", "dinheiro", None, "250"),
        ("2026-02-04", "Paciente B", "credito", "Stone", "519,57"),
        ("2026-02-05", "Paciente A", "ted", "Sicoob", "500"),
    ]
    for data, pac, forma, meio, valor in seq:
        kw = {"data_pagamento": data, "paciente": pac, "forma": forma, "valor": valor}
        kw["meio"] = meio
        h.registrar_recebimento(ctx(tmp), {k: v for k, v in kw.items() if v is not None})
    h.registrar_despesa(
        ctx(tmp),
        {
            "data": "2026-02-05",
            "descricao": "Aluguel",
            "categoria": "ALUGUEL",
            "grupo": "fixa",
            "valor": "1000",
        },
    )
    h.registrar_despesa(
        ctx(tmp),
        {
            "data": "2026-02-06",
            "descricao": "Café",
            "categoria": "COPA",
            "grupo": "variada",
            "valor": "50,25",
        },
    )
    return 27500 + 23050 + 25000 + 51957 + 50000, 105025


def test_consultas_batem_com_calculo_independente(ambiente):
    tmp, path, _ = ambiente
    entradas, saidas = popular(tmp)
    r = consulta(tmp, "resumo_mensal")["dados"]
    assert r["entradas_centavos"] == entradas and r["saidas_centavos"] == saidas
    assert r["resultado_centavos"] == entradas - saidas
    assert r["repasse_centavos"] + r["parte_clinica_centavos"] + r["taxas_centavos"] == entradas
    forma = {x["chave"]: x["total"] for x in consulta(tmp, "por_forma")["dados"]}
    assert forma == {
        "pix": 27500,
        "debito": 23050,
        "dinheiro": 25000,
        "credito": 51957,
        "ted": 50000,
    }
    conta = {x["chave"]: x["total"] for x in consulta(tmp, "por_conta")["dados"]}
    assert conta == {"Sicoob": 77500, "Stone": 75007, "Caixa": 25000}
    unid = {x["chave"]: x["total"] for x in consulta(tmp, "por_unidade")["dados"]}
    assert unid == {"Adultos": 100550, "Infantil": 76957}
    prof = {x["chave"]: x["total"] for x in consulta(tmp, "por_profissional")["dados"]}
    assert prof == {"Ana Souza": 100550, "Bruno Lima": 76957}
    assert sum(forma.values()) == sum(unid.values()) == entradas
    cat = {x["categoria"]: x["total"] for x in consulta(tmp, "despesas_por_categoria")["dados"]}
    assert cat == {"ALUGUEL": 100000, "COPA": 5025}
    res = consulta(tmp, "resultado_mensal")["dados"]
    assert res == [
        {
            "mes": "2026-02",
            "entradas_centavos": entradas,
            "saidas_centavos": saidas,
            "resultado_centavos": entradas - saidas,
        }
    ]
    ult = consulta(tmp, "ultimos_lancamentos", limite=3)["dados"]
    assert len(ult) == 3 and ult[0]["data"] >= ult[-1]["data"]
    sem = consulta(tmp, "sem_nf")["dados"]
    assert sem["por_status"] == [{"status": "pendente", "n": 5, "total": entradas}]
    taxas = {x["meio"]: x["taxas"] for x in consulta(tmp, "taxas_por_meio")["dados"]}
    assert taxas["Stone"] == 346 + 1818 and taxas["Sicoob"] == 0
    rp = {x["profissional"]: x for x in consulta(tmp, "repasse_por_profissional")["dados"]}
    assert rp["Ana Souza"]["bruto"] == 100550
    assert rp["Bruno Lima"]["repasse"] == 12500 + 25979
    pc = consulta(tmp, "parte_clinica")["dados"]
    assert sum(x["total"] for x in pc) == r["parte_clinica_centavos"]
    assert consulta(tmp, "painel_financeiro")["dados"]["integridade_ok"] is True
    filtrado = consulta(tmp, "resumo_mensal", forma="pix")["dados"]
    assert filtrado["entradas_centavos"] == 27500
    assert consulta(tmp, "verificar_integridade")["dados"]["ok"] is True


def test_simular_repasse_nao_grava(ambiente):
    tmp, path, _ = ambiente
    popular(tmp)
    params.materializar_comissao(
        path,
        {
            "profissional": "Ana Souza",
            "tipo": "percentual",
            "valor": "50",
            "base": "liquido",
            "vigente_de": "2026-03-01",
        },
        criado_por="t",
    )
    antes = linhas(path, "SELECT * FROM repasse")
    sim = consulta(tmp, "simular_repasse", periodo="2026-02")["dados"]
    assert sim["diferenca_centavos"] == 0 and sim["gravou"] is False
    params.materializar_comissao(
        path,
        {
            "profissional": "Bruno Lima",
            "tipo": "percentual",
            "valor": "60",
            "base": "bruto",
            "vigente_de": "2026-02-20",
        },
        criado_por="t",
    )
    assert linhas(path, "SELECT * FROM repasse") == antes


def test_perfil_sem_permissao_recebe_agregados(ambiente):
    tmp, path, _ = ambiente
    popular(tmp)
    sec = {"role": "viewer", "actor": "sec@clinica.test"}
    ult = consulta(tmp, "ultimos_lancamentos", **sec)
    assert ult["agregado"] is True
    assert all("paciente" not in x for x in ult["dados"])
    assert "Paciente" not in str(ult)
    nf = consulta(tmp, "sem_nf", **sec)["dados"]
    assert "lancamentos" not in nf
    r = consulta(tmp, "resumo_mensal", **sec)["dados"]
    assert "repasse_centavos" not in r
    for restrita in (
        "parte_clinica",
        "taxas_por_meio",
        "verificar_integridade",
        "simular_repasse",
        "repasse_por_profissional",
        "por_profissional",
    ):
        with pytest.raises(ErroFinanceiro):
            consulta(tmp, restrita, **sec)
    with pytest.raises(ErroFinanceiro, match="paciente"):
        consulta(tmp, "resumo_mensal", paciente="Paciente A", **sec)
    assert "Paciente" not in str(consulta(tmp, "pendencias", **sec))
    ap = consulta(tmp, "a_receber", **sec)["dados"]
    assert "por_pacote" not in ap


def test_profissional_nao_ve_nem_o_proprio_repasse(ambiente):
    tmp, _, _ = ambiente
    popular(tmp)
    for nome in ("repasse_por_profissional", "por_profissional"):
        with pytest.raises(ErroFinanceiro, match="restrit"):
            consulta(tmp, nome, role="viewer", actor="ana@clinica.test")
        with pytest.raises(ErroFinanceiro, match="restrit"):
            consulta(tmp, nome, role="viewer", actor="ana@clinica.test", profissional="Ana Souza")


def test_recebimento_de_perfil_comum_nao_devolve_o_repasse(ambiente):
    tmp, _, _ = ambiente
    popular(tmp)
    hoje = date.today().isoformat()
    r = rec(tmp, role="viewer", actor="sec@clinica.test", data_pagamento=hoje, key="k-sec")
    assert r["registrado"], r
    assert "repasse" not in r and "repasse" not in str(r)


@pytest.mark.parametrize(
    "carga",
    [
        "' OR 1=1 --",
        "x'; DROP TABLE recebimento; --",
        '" OR ""="',
        "%",
        "Paciente A' UNION SELECT 1 --",
    ],
)
def test_injecao_de_sql_em_filtros(ambiente, carga):
    tmp, path, _ = ambiente
    popular(tmp)
    for chave in ("unidade", "profissional", "forma", "meio", "paciente"):
        r = consulta(tmp, "resumo_mensal", **{chave: carga})
        assert r["dados"]["entradas_centavos"] == 0
    with pytest.raises(ErroFinanceiro):
        consulta(tmp, carga)
    with pytest.raises(ErroFinanceiro):
        consulta(tmp, "resumo_mensal", periodo=carga)
    with pytest.raises(ErroFinanceiro, match="não permitido"):
        consulta(tmp, "resumo_mensal", **{"1=1; DROP TABLE x": "a"})
    with pytest.raises(ErroFinanceiro):
        consulta(tmp, "ultimos_lancamentos", limite="1; DROP TABLE despesa")
    assert linhas(path, "SELECT COUNT(*) n FROM recebimento")[0]["n"] == 5


def test_filtros_e_periodos_invalidos(ambiente):
    tmp, _, _ = ambiente
    for periodo in ("2026-13", "2026-02-10..2026-02-01", "ontem", "2026-02-30..2026-03-01"):
        with pytest.raises(ErroFinanceiro):
            consulta(tmp, "resumo_mensal", periodo=periodo)
    popular(tmp)
    assert (
        consulta(tmp, "resumo_mensal", periodo="2026-02-02..2026-02-03")["dados"]["lancamentos"]
        == 3
    )
    with pytest.raises(ErroFinanceiro):
        h.consultar_financeiro(ctx(tmp), {"consulta": "resumo_mensal", "filtros": "x"})
    with pytest.raises(ErroFinanceiro):
        consulta(tmp, "resumo_mensal", forma=123)


def modelo(path, **kw):
    dados = {
        "nome": "Pacote 4",
        "modo_cobranca": "antecipado",
        "n_sessoes": 4,
        "preco_total": "1000",
        "vigente_de": "2026-01-01",
    }
    dados.update(kw)
    return params.materializar_modelo_pacote(path, dados, criado_por="t")


def test_pacote_modos_de_cobranca(ambiente):
    tmp, path, _ = ambiente
    modelo(path, nome="Antecipado")
    modelo(
        path,
        nome="Parcelado",
        modo_cobranca="parcelado",
        n_parcelas=3,
        intervalo_dias=30,
        preco_total="1000",
    )
    modelo(
        path, nome="Mensal", modo_cobranca="mensal", n_parcelas=2, n_sessoes=4, preco_total="800"
    )
    modelo(
        path,
        nome="Avulso",
        modo_cobranca="por_sessao",
        n_sessoes=4,
        preco_unitario="250",
        preco_total=None,
    )
    for nome in ("Antecipado", "Parcelado", "Mensal", "Avulso"):
        r = h.registrar_pacote(
            ctx(tmp), {"paciente": "Paciente A", "modelo": nome, "inicio": "2026-02-01"}
        )
        assert r["registrado"]
    cobr = linhas(
        path,
        "SELECT pacote_id, COUNT(*) n, SUM(valor_centavos) s FROM cobranca_prevista GROUP BY"
        " pacote_id ORDER BY 1",
    )
    assert cobr == [
        {"pacote_id": 1, "n": 1, "s": 100000},
        {"pacote_id": 2, "n": 3, "s": 100000},
        {"pacote_id": 3, "n": 2, "s": 160000},
    ]
    assert linhas(
        path, "SELECT sessoes_totais, preco_acordado_centavos FROM pacote WHERE id = 3"
    ) == [{"sessoes_totais": 8, "preco_acordado_centavos": 160000}]
    assert linhas(
        path, "SELECT vencimento FROM cobranca_prevista WHERE pacote_id = 2 ORDER BY id"
    ) == [{"vencimento": "2026-02-01"}, {"vencimento": "2026-03-03"}, {"vencimento": "2026-04-02"}]
    assert integridade.verificar(abrir(path)) == []


def test_pacote_modelo_vigente_e_idempotente(ambiente):
    tmp, path, _ = ambiente
    modelo(path)
    modelo(path, preco_total="1200", vigente_de="2026-03-01")
    antigo = h.registrar_pacote(
        ctx(tmp, key="k1"), {"paciente": "Paciente A", "modelo": "Pacote 4", "inicio": "2026-02-01"}
    )
    assert (
        linhas(
            path, "SELECT preco_acordado_centavos p FROM pacote WHERE id = ?", antigo["pacote_id"]
        )[0]["p"]
        == 100000
    )
    again = h.registrar_pacote(
        ctx(tmp, key="k1"), {"paciente": "Paciente A", "modelo": "Pacote 4", "inicio": "2026-02-01"}
    )
    assert again["reutilizado"]
    novo = h.registrar_pacote(
        ctx(tmp),
        {
            "paciente": "Paciente B",
            "modelo": "Pacote 4",
            "inicio": "2026-03-05",
            "preco_acordado": "1100",
        },
    )
    assert (
        novo["avisos"]
        and linhas(
            path, "SELECT preco_acordado_centavos p FROM pacote WHERE id = ?", novo["pacote_id"]
        )[0]["p"]
        == 110000
    )
    with pytest.raises(ErroFinanceiro, match="não está vigente"):
        h.registrar_pacote(
            ctx(tmp), {"paciente": "Paciente A", "modelo": "Inexistente", "inicio": "2026-02-01"}
        )


def sessao(path, agenda, paciente_id, dia, status="SCHEDULED", pacote_id=None):
    con = abrir(path)
    try:
        with transacao(con):
            return sessoes.upsert_sessao(
                con,
                agenda_id=agenda,
                paciente_id=paciente_id,
                inicio_utc=f"{dia}T13:00:00+00:00",
                status_agenda=status,
                pacote_id=pacote_id,
            )
    finally:
        con.close()


def test_faltas_conflito_desfecho_e_resolucao(ambiente):
    tmp, path, ids = ambiente
    sessao(path, "ag-1", ids["pac_a"], "2026-02-03", "COMPLETED")
    sessao(path, "ag-2", ids["pac_a"], "2026-02-04")
    f = h.registrar_falta(ctx(tmp), {"agenda_id": "ag-2", "motivo": "avisou tarde"})
    assert f["registrado"] and not f["conflito_desfecho"]
    with pytest.raises(ErroFinanceiro, match="já tem"):
        h.registrar_falta(ctx(tmp), {"agenda_id": "ag-2"})
    conf = h.registrar_falta(ctx(tmp), {"paciente": "Paciente A", "data_sessao": "2026-02-03"})
    assert conf["conflito_desfecho"]
    assert integridade.verificar(abrir(path)) == []
    pend = consulta(tmp, "pendencias")["dados"]["abertas"]
    assert [p["tipo"] for p in pend] == ["conflito_desfecho"]
    with pytest.raises(ErroFinanceiro, match="gestor"):
        h.resolver_pendencia(
            ctx(tmp, "viewer", "s@x"), {"pendencia_id": pend[0]["id"], "acao": "descartar"}
        )
    h.resolver_pendencia(ctx(tmp), {"pendencia_id": pend[0]["id"], "acao": "manter_realizada"})
    assert (
        linhas(path, "SELECT COUNT(*) n FROM ocorrencia_efetiva WHERE sessao_id = 1")[0]["n"] == 0
    )
    assert integridade.verificar(abrir(path)) == []
    fp = consulta(tmp, "faltas_por_periodo", periodo="2026-02")["dados"]
    assert fp["por_tipo"] == [{"tipo": "falta", "n": 1}]
    with pytest.raises(ErroFinanceiro, match="Sessão não encontrada"):
        h.registrar_falta(ctx(tmp), {"agenda_id": "nao-existe"})


def test_f8_detecta_conflito_sem_pendencia(ambiente):
    _, path, ids = ambiente
    sid = sessao(path, "ag-9", ids["pac_a"], "2026-02-03", "COMPLETED")
    con = abrir(path)
    con.execute(
        "INSERT INTO sessao_ocorrencia (sessao_id, tipo, registrado_por, registrado_em)"
        " VALUES (?, 'falta', 'x', 'x')",
        (sid,),
    )
    assert [v["invariante"] for v in integridade.verificar(con)] == ["F8"]


def test_pendencia_sessao_sem_desfecho(ambiente):
    _, path, ids = ambiente
    sessao(path, "ag-1", ids["pac_a"], "2020-02-03")
    sessao(path, "ag-2", ids["pac_a"], "2999-02-03")
    con = abrir(path)
    with transacao(con):
        assert sessoes.gerar_pendencias_sem_desfecho(con) == 1
        assert sessoes.gerar_pendencias_sem_desfecho(con) == 0
    con.close()


def test_pacote_nao_consome_alem_do_total_e_saldo(ambiente):
    tmp, path, ids = ambiente
    modelo(path, n_sessoes=2, falta_consome=True, preco_total="500")
    pk = h.registrar_pacote(
        ctx(tmp), {"paciente": "Paciente A", "modelo": "Pacote 4", "inicio": "2026-02-01"}
    )["pacote_id"]
    sessao(path, "a1", ids["pac_a"], "2026-02-02", "COMPLETED", pk)
    sessao(path, "a2", ids["pac_a"], "2026-02-03", "SCHEDULED", pk)
    h.registrar_falta(ctx(tmp), {"agenda_id": "a2"})
    sessao(path, "a3", ids["pac_a"], "2026-02-04", "COMPLETED", pk)
    assert linhas(path, "SELECT pacote_id FROM sessao WHERE agenda_id = 'a3'") == [
        {"pacote_id": None}
    ]
    assert any(
        p["tipo"] == "pacote_excedido" for p in consulta(tmp, "pendencias")["dados"]["abertas"]
    )
    saldo = consulta(tmp, "saldo_pacote", pacote_id=pk)["dados"]["itens"][0]
    assert saldo["consumidas"] == 2 and saldo["saldo"] == 0
    assert integridade.verificar(abrir(path)) == []
    agreg = consulta(tmp, "saldo_pacote", role="viewer", actor="s@x")["dados"]
    assert "itens" not in agreg and agreg["sessoes_restantes"] == 0


def test_a_receber_credito_e_por_sessao(ambiente):
    tmp, path, ids = ambiente
    modelo(
        path,
        nome="Avulso",
        modo_cobranca="por_sessao",
        n_sessoes=3,
        preco_unitario="100",
        preco_total=None,
    )
    pk = h.registrar_pacote(
        ctx(tmp), {"paciente": "Paciente A", "modelo": "Avulso", "inicio": "2026-02-01"}
    )["pacote_id"]
    assert consulta(tmp, "a_receber")["dados"]["a_receber_centavos"] == 0
    for i, ag in enumerate(("s1", "s2")):
        sessao(path, ag, ids["pac_a"], f"2026-02-0{i + 2}", "COMPLETED", pk)
    con = abrir(path)
    with transacao(con):
        assert sessoes.gerar_cobrancas_por_sessao(con, pk) == 2
        assert sessoes.gerar_cobrancas_por_sessao(con, pk) == 0
    con.close()
    assert consulta(tmp, "a_receber")["dados"]["a_receber_centavos"] == 20000
    rec(tmp, valor="150", pacote_id=pk, tipo="pacote")
    assert consulta(tmp, "a_receber")["dados"]["a_receber_centavos"] == 5000
    rec(tmp, valor="100", pacote_id=pk, tipo="pacote", data_pagamento="2026-02-05")
    d = consulta(tmp, "a_receber")["dados"]
    assert d["a_receber_centavos"] == 0 and d["credito_centavos"] == 5000


def test_execucao_via_registro_do_modulo(tmp_path):
    from mcp_engine.core.executor import HandlerRegistry
    from mcp_engine.modules import ModuleHost, NamespacedHandlers
    from mcp_engine.modules.financeiro import MODULE

    registry = HandlerRegistry()
    MODULE.install(
        ModuleHost(
            "financeiro",
            "c",
            tmp_path,
            NamespacedHandlers("financeiro", registry),
            None,  # type: ignore[arg-type]
            lambda *a: None,
            lambda *a: None,
        )
    )
    assert "financeiro.registrar_recebimento" in registry.names()
    assert len(registry.names()) == len(MODULE.manifest.skills)


def test_usuario_sem_ledger_nao_vaza_detalhe(tmp_path):
    with pytest.raises(ErroFinanceiro):
        h.consultar_financeiro(ctx(tmp_path), {"consulta": "resumo_mensal"})


def test_quem_gestor():
    assert Quem("a", "admin").gestor and Quem("a", "super_admin").gestor
    assert not Quem("a", "viewer").gestor
