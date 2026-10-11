import tempfile
from datetime import date, timedelta
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mcp_engine.modules.financeiro.importers import importar
from mcp_engine.modules.financeiro.importers.sessoes_parser import formatar, interpretar
from tests.fixtures import sinteticos as syn
from tests.test_importador import contar, opcoes, preparar, soma

SEM_PRAZO = settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@given(st.text(max_size=60), st.dates(min_value=date(2020, 1, 1), max_value=date(2030, 12, 31)))
@settings(max_examples=400, deadline=None)
def test_parser_nunca_levanta_excecao_com_entrada_arbitraria(texto, pagamento):
    r = interpretar(texto, pagamento)
    assert 0.0 <= r.confianca <= 1.0
    assert r.erro is None or r.datas == ()


@given(
    st.lists(st.integers(-30, 30), min_size=1, max_size=8, unique=True),
    st.dates(min_value=date(2021, 1, 1), max_value=date(2029, 12, 31)),
)
@settings(max_examples=300, deadline=None)
def test_parser_ida_e_volta_de_datas_completas(deslocamentos, pagamento):
    datas = tuple(sorted(pagamento + timedelta(days=d) for d in deslocamentos))
    r = interpretar(formatar(datas), pagamento)
    assert r.datas == datas and r.erro is None and r.confianca == 1.0


linha = st.tuples(
    st.integers(1, 27),
    st.integers(0, 12),
    st.sampled_from(["PIX SICOOB", "PIX STONE", "DEB", "DINH", "TED SICOOB", "ACORDO"]),
    st.integers(100, 90_000),
    st.sampled_from(["Ana Souza", "Bruno Lima"]),
)


def _montar(registros):
    return [
        (
            syn.d(2, dia),
            f"Paciente {pac:02d}",
            forma,
            syn.d(2, dia),
            centavos / 100,
            "S/NF",
            prof,
            None,
            None,
        )
        for dia, pac, forma, centavos, prof in registros
    ]


@given(st.lists(linha, min_size=1, max_size=15), st.lists(linha, min_size=0, max_size=8))
@SEM_PRAZO
def test_idempotencia_e_total_da_planilha_igual_ao_ledger(base, extras):
    raiz = Path(tempfile.mkdtemp())
    path = preparar(raiz)
    a = syn.criar_secretarias(raiz / "a.xlsx", _montar(base))
    rel = importar(path, a, opcoes(aplicar=True))
    assert rel["ok"] and rel["linhas"]["pendentes"] == 0
    assert soma(path) == sum(round(x[3]) for x in base)
    n = contar(path, "recebimento")
    again = importar(path, a, opcoes(aplicar=True))
    assert again["linhas"]["inseridas"] == 0 and contar(path, "recebimento") == n
    b = syn.criar_secretarias(raiz / "b.xlsx", _montar(base + extras))
    sup = importar(path, b, opcoes(aplicar=True))
    assert sup["linhas"]["inseridas"] == len(extras)
    assert contar(path, "recebimento") == n + len(extras)
    assert soma(path) == sum(round(x[3]) for x in base + extras)
