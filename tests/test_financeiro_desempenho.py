import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest

from mcp_engine.modules.financeiro import handlers as h
from mcp_engine.modules.financeiro import integridade
from mcp_engine.modules.financeiro.db import ErroFinanceiro, abrir
from tests.financeiro_util import criar_ledger, ctx, recebimento, semear

ORCAMENTO_S = 0.2


def carregar(path, ids, n, meses):
    con = abrir(path)
    base = date(2026, 2, 1)
    con.execute("BEGIN")
    linhas = []
    for i in range(n):
        d = (base + timedelta(days=i % (28 * meses) + (i % (28 * meses)) // 28 * 3)).isoformat()
        valor = 10000 + (i % 400) * 25
        linhas.append(
            (
                d,
                d,
                ids["pac_a"] if i % 2 else ids["pac_b"],
                1 + i % 4,
                1 + i % 2,
                valor,
                ids["adultos"] if i % 3 else ids["infantil"],
                "sessao",
                "carga",
                "t",
            )
        )
    con.executemany(
        "INSERT INTO recebimento (data_pagamento, data_competencia, paciente_id, forma_id,"
        " meio_id, valor_centavos, unidade_id, tipo, criado_por, criado_em)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        linhas,
    )
    con.execute(
        "INSERT INTO repasse (recebimento_id, data_pagamento, taxa_bp, taxa_centavos,"
        " liquido_centavos,"
        " repasse_centavos, parte_clinica_centavos)"
        " SELECT id, data_pagamento, 0, 0, valor_centavos, valor_centavos * 4 / 10,"
        " valor_centavos - valor_centavos * 4 / 10 FROM recebimento"
    )
    con.execute("COMMIT")
    con.close()


@pytest.mark.parametrize("meses", [12, 1])
def test_consulta_mensal_abaixo_de_200ms_com_100_mil_linhas(tmp_path, meses):
    path = criar_ledger(tmp_path)
    ids = semear(path)
    carregar(path, ids, 100_000, meses)
    con = abrir(path)
    assert con.execute("SELECT COUNT(*) FROM recebimento").fetchone()[0] == 100_000
    con.close()
    for consulta in (
        "resumo_mensal",
        "por_forma",
        "por_unidade",
        "por_conta",
        "despesas_por_categoria",
    ):
        h.consultar_financeiro(ctx(tmp_path), {"consulta": consulta, "periodo": "2026-02"})
        inicio = time.perf_counter()
        r = h.consultar_financeiro(ctx(tmp_path), {"consulta": consulta, "periodo": "2026-02"})
        assert time.perf_counter() - inicio < ORCAMENTO_S, consulta
    resumo = h.consultar_financeiro(
        ctx(tmp_path), {"consulta": "resumo_mensal", "periodo": "2026-02"}
    )
    assert resumo["dados"]["lancamentos"] >= 8000
    assert r is not None


def test_escritas_concorrentes_nao_corrompem(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "hoje", lambda: date(2026, 2, 10))
    path = criar_ledger(tmp_path)
    semear(path)

    def lancar(i):
        r = h.registrar_recebimento(
            ctx(tmp_path),
            recebimento(
                valor=str(100 + i),
                data_pagamento=f"2026-02-{1 + i % 9:02d}",
                paciente="Paciente A" if i % 2 else "Paciente B",
            ),
        )
        return r["registrado"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        resultados = list(pool.map(lancar, range(60)))
    assert all(resultados)
    con = abrir(path)
    assert con.execute("SELECT COUNT(*) FROM recebimento").fetchone()[0] == 60
    assert con.execute("SELECT COUNT(*) FROM repasse").fetchone()[0] == 60
    assert integridade.verificar(con) == []
    con.close()


def test_estornos_concorrentes_do_mesmo_lancamento(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "hoje", lambda: date(2026, 2, 10))
    path = criar_ledger(tmp_path)
    semear(path)
    rid = h.registrar_recebimento(ctx(tmp_path), recebimento(valor="100"))["recebimento_id"]

    def estornar(_):
        try:
            h.estornar_lancamento(
                ctx(tmp_path),
                {"tipo": "recebimento", "lancamento_id": rid, "valor": "60", "motivo": "x"},
            )
            return True
        except ErroFinanceiro:
            return False

    with ThreadPoolExecutor(max_workers=6) as pool:
        ok = list(pool.map(estornar, range(6)))
    assert sum(ok) == 1
    con = abrir(path)
    assert integridade.verificar(con) == []
    con.close()
