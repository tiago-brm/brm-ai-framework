import json
from datetime import date
from pathlib import Path

import pytest

from mcp_engine.core.auth import Role, UserProfile, current_user_var, set_current_user
from mcp_engine.core.mcp_server import BRMEngine, build_server
from mcp_engine.modules import financeiro as fin
from mcp_engine.modules.financeiro import handlers as h
from mcp_engine.modules.financeiro import params
from mcp_engine.modules.financeiro.db import abrir, caminho_ledger, transacao
from scripts.novo_cliente import criar_cliente

CLIENTE = "clinica-teste"
SEC = UserProfile(identity="sec@clinica.test", role=Role.VIEWER)
GESTOR = UserProfile(identity="gestor@clinica.test", role=Role.ADMIN)
GESTOR2 = UserProfile(identity="gestor2@clinica.test", role=Role.ADMIN)
ROOT = UserProfile(identity="root@clinica.test", role=Role.SUPER_ADMIN)
RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _reset_user():
    token = current_user_var.set(None)
    yield
    current_user_var.reset(token)


@pytest.fixture
def motor(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "hoje", lambda: date(2026, 2, 10))
    cfg = tmp_path / "clients"
    criar_cliente(CLIENTE, ["financeiro"], cfg)
    engine = BRMEngine(client_id=CLIENTE, config_root=cfg, data_root=tmp_path / "data")
    path = caminho_ledger(tmp_path / "data", CLIENTE)
    con = abrir(path)
    with transacao(con):
        u = params.garantir_unidade(con, "Adultos")
        p = params.garantir_profissional(con, "Ana Souza")
        params.garantir_paciente(
            con, "Paciente A", unidade_id=u, profissional_id=p, valor_sessao_centavos=27500
        )
    con.close()
    params.materializar_meio(
        path, {"nome": "Sicoob", "tipo": "banco", "vigente_de": "2025-01-01"}, criado_por="t"
    )
    params.materializar_comissao(
        path,
        {
            "profissional": "Ana Souza",
            "tipo": "percentual",
            "valor": "40",
            "base": "liquido",
            "vigente_de": "2025-01-01",
        },
        criado_por="t",
    )
    return engine, path


def como(perfil):
    return set_current_user(perfil)


def linhas(path, sql):
    con = abrir(path)
    try:
        return [dict(r) for r in con.execute(sql)]
    finally:
        con.close()


ARGS = {
    "data_pagamento": "2026-02-09",
    "paciente": "Paciente A",
    "forma": "pix",
    "meio": "Sicoob",
    "valor": "275",
}


def propor_e_confirmar(engine, perfil, skill, args):
    como(perfil)
    prop = engine.call_skill(skill, args)
    assert prop["pendente"], prop
    return engine.confirmar_operacao(prop["operacao_id"]), prop


def test_cliente_gerado_expoe_as_skills_e_o_banco_migra(motor):
    engine, path = motor
    nomes = {t.name for t in build_server(engine)._tool_manager.list_tools()}
    assert {
        "registrar_recebimento",
        "estornar_lancamento",
        "consultar_financeiro",
        "fechar_mes",
        "exportar_planilha",
    } <= nomes
    assert {s.name: s.autonomia for s in engine.skills}["estornar_lancamento"] == "A3"
    assert linhas(path, "SELECT COUNT(*) n FROM _migracao")[0]["n"] == 3


def test_recebimento_proposta_confirmacao_e_execucao(motor):
    engine, path = motor
    res, prop = propor_e_confirmar(engine, SEC, "registrar_recebimento", ARGS)
    assert "Recebimento" in prop["resumo"] or "275" in prop["resumo"]
    assert res["allowed"] and res["status"] == "executada"
    assert json.loads(res["result"])["registrado"] is True
    linha = linhas(path, "SELECT operacao_id, criado_por FROM recebimento")[0]
    assert linha == {"operacao_id": prop["operacao_id"], "criado_por": "sec@clinica.test"}


def test_estorno_exige_aprovacao_de_outro_perfil(motor):
    engine, path = motor
    propor_e_confirmar(engine, SEC, "registrar_recebimento", ARGS)
    como(SEC)
    prop = engine.call_skill(
        "estornar_lancamento", {"tipo": "recebimento", "lancamento_id": 1, "motivo": "desistência"}
    )
    assert prop["status"] == "aguardando_aprovacao"
    como(SEC)
    assert engine.aprovar_operacao(prop["operacao_id"])["allowed"] is False
    como(GESTOR)
    ok = engine.aprovar_operacao(prop["operacao_id"])
    assert ok["allowed"] and json.loads(ok["result"])["estornado"]
    assert linhas(path, "SELECT SUM(valor_centavos) s FROM recebimento")[0]["s"] == 0


def test_fechar_mes_e_reabertura_so_super_admin(motor):
    engine, path = motor
    propor_e_confirmar(
        engine, GESTOR, "registrar_recebimento", {**ARGS, "data_pagamento": "2026-01-15"}
    )
    como(GESTOR)
    prop = engine.call_skill("fechar_mes", {"mes": "2026-01"})
    assert prop["status"] == "aguardando_aprovacao"
    como(GESTOR2)
    assert engine.aprovar_operacao(prop["operacao_id"])["allowed"]
    fechamento = linhas(path, "SELECT aprovado_por FROM fechamento_mensal WHERE status = 'fechado'")
    assert fechamento == [{"aprovado_por": GESTOR2.identity}]
    como(GESTOR)
    negado = engine.call_skill("reabrir_mes", {"mes": "2026-01", "motivo": "erro"})
    assert negado["allowed"] is False and negado["rule_id"] == "reabrir-mes-so-super-admin"
    res, _ = propor_e_confirmar(
        engine, ROOT, "reabrir_mes", {"mes": "2026-01", "motivo": "erro de digitação"}
    )
    assert res["allowed"] and json.loads(res["result"])["reaberto"]


def test_consulta_direta_a0_e_agregada_para_secretaria(motor):
    engine, _ = motor
    propor_e_confirmar(engine, SEC, "registrar_recebimento", ARGS)
    como(SEC)
    r = engine.call_skill(
        "consultar_financeiro", {"consulta": "ultimos_lancamentos", "periodo": "2026-02"}
    )
    assert r["allowed"] is True
    corpo = json.loads(r["result"])
    assert corpo["agregado"] is True and "Paciente" not in r["result"]
    como(GESTOR)
    g = engine.call_skill(
        "consultar_financeiro", {"consulta": "ultimos_lancamentos", "periodo": "2026-02"}
    )
    assert "Paciente A" in g["result"]


def test_relatorio_mensal_do_ledger_e_deterministico(motor):
    engine, _ = motor
    propor_e_confirmar(engine, SEC, "registrar_recebimento", ARGS)
    como(GESTOR)

    primeiro = engine.gerar_relatorio_mensal("2026-02")
    segundo = engine.gerar_relatorio_mensal("2026-02")

    assert primeiro["allowed"] and primeiro["estado"] == "criado"
    assert segundo["allowed"] and segundo["estado"] == "igual"
    assert primeiro["hash"] == segundo["hash"]
    corpo = (engine._vault_root() / primeiro["nota"]).read_text()
    assert primeiro["nota"] == "relatorios/financeiro-mensal-2026-02.md"
    assert "Entradas: R$ 275,00" in corpo
    assert "Saídas: R$ 0,00" in corpo
    assert "Resultado: R$ 275,00" in corpo


def test_duplicidade_confirmada_por_secretaria_exige_aprovacao_do_gestor(motor):
    engine, _ = motor
    propor_e_confirmar(engine, SEC, "registrar_recebimento", ARGS)
    como(SEC)
    r = engine.call_skill("registrar_recebimento", {**ARGS, "confirmar_duplicidade": True})
    assert r["status"] == "aguardando_aprovacao"


def test_resultado_de_falha_do_handler_chega_saneado(motor):
    engine, _ = motor
    como(SEC)
    prop = engine.call_skill("registrar_recebimento", {**ARGS, "valor": "1.234"})
    res = engine.confirmar_operacao(prop["operacao_id"])
    assert res["allowed"] is False and "ambíguo" in res["reason"]
    assert "Traceback" not in str(res)


def test_nucleo_continua_agnostico():
    termos = ("financeiro", "recebimento", "ledger")
    for arq in (RAIZ / "mcp_engine" / "core").glob("*.py"):
        texto = arq.read_text().lower()
        assert not any(f"modules.{t}" in texto for t in termos), arq.name


def test_defaults_do_modulo_batem_com_manifesto():
    import yaml

    defaults = yaml.safe_load((Path(fin.__file__).parent / "defaults" / "skills.yaml").read_text())
    usados = {s["usar"].split(".", 1)[1] for s in defaults["skills"]}
    assert usados == set(fin.MODULE.manifest.skills)


def test_clinica_piloto_valida_no_config_loader():
    from scripts.novo_cliente import validar

    validar("clinica-piloto", RAIZ / "clients")
    assert (RAIZ / "clients" / "clinica-piloto" / "vault" / "cadastros" / "README.md").exists()
    assert not list((RAIZ / "clients" / "clinica-piloto" / "vault").rglob("*.xlsx"))
    from mcp_engine.modules.financeiro.importers.mapeamento import Mapeamento

    mapa = Mapeamento.carregar(RAIZ / "clients" / "clinica-piloto" / "config" / "importacao.yaml")
    assert mapa.resolver("DEB", date(2026, 2, 1)).meio == "Stone"
    assert mapa.resolver("holerite", date(2026, 2, 1), despesa=True).forma is None
