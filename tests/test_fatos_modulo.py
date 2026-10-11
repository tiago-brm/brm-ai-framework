"""Fatos de módulo para as regras, aprovador real, visibilidade de operações e saúde (031 A1-A4)."""

import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from audit.sink import FileSystemAuditSink
from mcp_engine.core.auth import Role, UserProfile, current_user_var, set_current_user
from mcp_engine.core.executor import HandlerContext
from mcp_engine.core.fatos import ContextoFatos, FatosErro, FatosIndisponiveis, ProvedoresFatos
from mcp_engine.core.http_app import create_app
from mcp_engine.core.mcp_server import BRMEngine
from mcp_engine.modules import (
    BaseModule,
    ModuleError,
    ModuleHost,
    register_module,
    unregister_module,
)
from mcp_engine.modules.financeiro import handlers as h
from mcp_engine.modules.financeiro import params
from mcp_engine.modules.financeiro.db import abrir, caminho_ledger, transacao
from mcp_engine.vault.obsidian_adapter import ObsidianVaultProvider
from scripts.novo_cliente import criar_cliente

CLIENTE = "clinica-fatos"
SEC = UserProfile(identity="sec@clinica.test", role=Role.VIEWER)
GESTOR = UserProfile(identity="gestor@clinica.test", role=Role.ADMIN)
GESTOR2 = UserProfile(identity="gestor2@clinica.test", role=Role.ADMIN)
ARGS = {
    "data_pagamento": "2026-02-09",
    "paciente": "Paciente A",
    "forma": "pix",
    "meio": "Sicoob",
    "valor": "275",
}


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    for chave in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "RESEND_BRM_API_KEY"):
        monkeypatch.delenv(chave, raising=False)
    token = current_user_var.set(None)
    yield
    current_user_var.reset(token)


class FakeEmbeddings:
    dim = 384

    def encode(self, texts):
        return [[0.0] * 384 for _ in texts]


def novo_motor(cfg, data):
    provider = ObsidianVaultProvider(cfg, data, FileSystemAuditSink(data), FakeEmbeddings())
    return BRMEngine(client_id=CLIENTE, config_root=cfg, data_root=data, vault_provider=provider)


@pytest.fixture
def motor(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "hoje", lambda: date(2026, 2, 10))
    cfg = tmp_path / "clients"
    criar_cliente(CLIENTE, ["financeiro"], cfg)
    engine = novo_motor(cfg, tmp_path / "data")
    path = caminho_ledger(tmp_path / "data", CLIENTE)
    con = abrir(path)
    with transacao(con):
        u = params.garantir_unidade(con, "Adultos")
        p = params.garantir_profissional(con, "Ana Souza")
        params.garantir_paciente(
            con, "Paciente A", unidade_id=u, profissional_id=p, valor_sessao_centavos=27500
        )
        params.garantir_paciente(
            con, "Paciente Novo", unidade_id=u, profissional_id=p, status="a_confirmar"
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
    return engine, path, cfg


def como(perfil):
    return set_current_user(perfil)


def linhas(path, sql):
    con = abrir(path)
    try:
        return [dict(r) for r in con.execute(sql)]
    finally:
        con.close()


def auditoria(engine, prefixo):
    return [e for e in engine._audit_sink.iter_events(engine.client_id) if prefixo in e.action]


class TestRegistroDeProvedores:
    def test_nome_e_fatos_validados(self):
        p = ProvedoresFatos()
        fn = lambda ctx, args: {}  # noqa: E731
        with pytest.raises(FatosErro):
            p.registrar("semponto", fn, ["x"])
        with pytest.raises(FatosErro):
            p.registrar("m.ok", fn, [])
        for reservado in ("actor_role", "skill_name", "actor", "origem_sistema"):
            with pytest.raises(FatosErro):
                p.registrar("m.ok", fn, [reservado])
        p.registrar("m.ok", fn, ["x"])
        with pytest.raises(FatosErro):
            p.registrar("m.ok", fn, ["y"])

    def test_resultado_fora_do_contrato_nega(self):
        p = ProvedoresFatos()
        p.registrar("m.a", lambda c, a: {"x": 1, "extra": 2}, ["x"])
        p.registrar("m.b", lambda c, a: {"x": [1]}, ["x"])
        p.registrar("m.c", lambda c, a: (_ for _ in ()).throw(RuntimeError("boom")), ["x"])
        ctx = ContextoFatos("c", "u", "viewer", Path("."))
        for nome in ("m.a", "m.b", "m.c"):
            with pytest.raises(FatosIndisponiveis):
                p.calcular([nome], ctx, {})

    def test_provedor_nao_altera_os_argumentos(self):
        p = ProvedoresFatos()

        def malicioso(ctx, args):
            args["valor"] = "0"
            return {}

        p.registrar("m.a", malicioso, ["x"])
        ctx = ContextoFatos("c", "u", "viewer", Path("."))
        with pytest.raises(FatosIndisponiveis):
            p.calcular(["m.a"], ctx, {"valor": "1"})

    def test_colisao_com_parametro_e_provedor_inexistente(self):
        p = ProvedoresFatos()
        p.registrar("m.a", lambda c, a: {}, ["valor_atipico"])
        assert p.validar_uso(["m.a"], ["valor_atipico"], [])
        assert p.validar_uso(["m.zzz"], [], [])
        assert p.validar_uso(["m.a"], ["valor"], []) == []

    def test_host_isolado_nao_registra_nada(self, tmp_path):
        host = ModuleHost("m", "c", tmp_path, None, None, lambda *a: None, lambda *a: None)
        host.registrar_provedor_fatos("a", lambda c, a: {}, ["x"])
        host.registrar_verificacao_saude("v", lambda: {"ok": True})
        assert host.operacoes_agregadas() is None


class TestFatosNasRegras:
    def test_valor_atipico_por_secretaria_vira_aprovacao(self, motor):
        engine, path, _ = motor
        como(SEC)
        r = engine.call_skill("registrar_recebimento", {**ARGS, "valor": "100"})
        assert r["status"] == "aguardando_aprovacao"
        assert r["rule_id"] == "lancamento-atipico-exige-gestor"
        assert linhas(path, "SELECT COUNT(*) n FROM recebimento")[0]["n"] == 0

    def test_retroativo_e_paciente_a_confirmar(self, motor):
        engine, _, _ = motor
        como(SEC)
        retro = engine.call_skill("registrar_recebimento", {**ARGS, "data_pagamento": "2026-01-20"})
        novo = engine.call_skill("registrar_recebimento", {**ARGS, "paciente": "Paciente Novo"})
        assert retro["rule_id"] == novo["rule_id"] == "lancamento-atipico-exige-gestor"

    def test_lancamento_normal_segue_o_fluxo_de_confirmacao(self, motor):
        engine, _, _ = motor
        como(SEC)
        r = engine.call_skill("registrar_recebimento", ARGS)
        assert r["status"] == "proposta" and r["pendente"]

    def test_gestor_nao_cai_na_regra(self, motor):
        engine, _, _ = motor
        como(GESTOR)
        r = engine.call_skill("registrar_recebimento", {**ARGS, "valor": "100"})
        assert r["status"] == "proposta"

    def test_aprovacao_do_gestor_grava_de_verdade(self, motor):
        engine, path, _ = motor
        como(SEC)
        prop = engine.call_skill("registrar_recebimento", {**ARGS, "valor": "100"})
        como(GESTOR)
        res = engine.aprovar_operacao(prop["operacao_id"])
        assert res["allowed"] and json.loads(res["result"])["registrado"] is True
        linha = linhas(path, "SELECT criado_por, valor_centavos FROM recebimento")
        assert linha == [{"criado_por": SEC.identity, "valor_centavos": 10000}]

    def test_mes_fechado_e_negado_pela_regra(self, motor):
        engine, path, _ = motor
        como(GESTOR)
        prop = engine.call_skill("registrar_recebimento", {**ARGS, "data_pagamento": "2026-01-15"})
        assert engine.confirmar_operacao(prop["operacao_id"])["allowed"]
        fechar = engine.call_skill("fechar_mes", {"mes": "2026-01"})
        como(GESTOR2)
        assert engine.aprovar_operacao(fechar["operacao_id"])["allowed"]
        como(SEC)
        r = engine.call_skill("registrar_recebimento", {**ARGS, "data_pagamento": "2026-01-20"})
        assert r["allowed"] is False and r["rule_id"] == "lancamento-em-mes-fechado"

    def test_rede_de_protecao_sem_a_regra_mantem_a_pendencia(self, motor, tmp_path):
        _, path, cfg = motor
        arquivo = cfg / CLIENTE / "config" / "rules.yaml"
        dados = yaml.safe_load(arquivo.read_text())
        dados["rules"] = [r for r in dados["rules"] if r["id"] == "reabrir-mes-so-super-admin"]
        arquivo.write_text(yaml.safe_dump(dados))
        engine = BRMEngine(CLIENTE, cfg, tmp_path / "data")
        como(SEC)
        prop = engine.call_skill("registrar_recebimento", {**ARGS, "valor": "100"})
        assert prop["status"] == "proposta"
        res = engine.confirmar_operacao(prop["operacao_id"])
        corpo = json.loads(res["result"])
        assert corpo["registrado"] is False and corpo["requer_gestor"] is True
        assert linhas(path, "SELECT COUNT(*) n FROM recebimento")[0]["n"] == 0


class TestChamadorNaoForcaFatos:
    def test_forcar_valor_atipico_falso_nao_evita_a_regra(self, motor):
        engine, _, _ = motor
        como(SEC)
        r = engine.call_skill(
            "registrar_recebimento",
            {
                **ARGS,
                "valor": "100",
                "valor_atipico": False,
                "retroativo_dias": 0,
                "paciente_a_confirmar": False,
            },
        )
        assert r["status"] == "aguardando_aprovacao"
        divergentes = {e.details["fact"] for e in auditoria(engine, "fato_divergente")}
        assert "valor_atipico" in divergentes

    def test_forcar_mes_fechado_falso_nao_libera(self, motor):
        engine, _, _ = motor
        como(GESTOR)
        prop = engine.call_skill("registrar_recebimento", {**ARGS, "data_pagamento": "2026-01-15"})
        engine.confirmar_operacao(prop["operacao_id"])
        fechar = engine.call_skill("fechar_mes", {"mes": "2026-01"})
        como(GESTOR2)
        engine.aprovar_operacao(fechar["operacao_id"])
        como(SEC)
        r = engine.call_skill(
            "registrar_recebimento",
            {**ARGS, "data_pagamento": "2026-01-20", "mes_fechado": False},
        )
        assert r["allowed"] is False and r["rule_id"] == "lancamento-em-mes-fechado"

    def test_forcar_fatos_do_motor_nao_funciona(self, motor):
        engine, _, _ = motor
        como(SEC)
        r = engine.call_skill(
            "resolver_pendencia", {"pendencia_id": 1, "acao": "descartar", "actor_role": "admin"}
        )
        assert r["allowed"] is False and r["rule_id"] == "pendencia-so-gestor"
        r2 = engine.call_skill(
            "resolver_pendencia", {"pendencia_id": 1, "acao": "descartar", "skill_name": "outra"}
        )
        assert r2["rule_id"] == "pendencia-so-gestor"

    def test_fato_forcado_nunca_chega_ao_handler(self, motor):
        engine, _, _ = motor
        vistos = {}
        original = h.HANDLERS["registrar_recebimento"]

        def espiao(ctx, args):
            vistos.update(args)
            return original(ctx, args)

        engine.handlers._handlers["financeiro.registrar_recebimento"] = espiao
        como(GESTOR)
        prop = engine.call_skill("registrar_recebimento", {**ARGS, "valor_atipico": True})
        engine.confirmar_operacao(prop["operacao_id"])
        assert "valor_atipico" not in vistos

    def test_avaliar_skill_e_trace_trazem_os_fatos(self, motor):
        engine, _, _ = motor
        como(SEC)
        veredito = engine.avaliar_skill("registrar_recebimento", {**ARGS, "valor": "100"})
        assert veredito["efeito"] == "require_approval"
        assert veredito["fatos_de_modulo"]["valor_atipico"] is True
        trace: dict = {}
        engine.call_skill(
            "registrar_recebimento", {**ARGS, "valor": "100"}, simulacao=True, trace=trace
        )
        assert trace["fatos_de_modulo"]["valor_atipico"] is True

    def test_simulador_http_expoe_os_fatos_de_modulo(self, motor):
        engine, _, _ = motor
        engine.user_store.create_user("root@clinica.test", Role.SUPER_ADMIN, "tok-root")
        engine.user_store.create_user(SEC.identity, Role.VIEWER, "tok-sec")
        client = TestClient(create_app(engine))
        r = client.post(
            "/api/simulador/executar",
            headers={"Authorization": "Bearer tok-root"},
            json={
                "perfil_email": SEC.identity,
                "skill": "registrar_recebimento",
                "argumentos": {**ARGS, "valor": "100"},
            },
        )
        assert r.status_code == 200
        assert r.json()["fatos_de_modulo"]["valor_atipico"] is True
        assert r.json()["decisao"] == "pending_approval"


class TestProvedorFalhandoNega:
    @pytest.fixture
    def modulo(self, tmp_path):
        d = tmp_path / "mods" / "fragil"
        d.mkdir(parents=True)
        (d / "module.yaml").write_text(
            "name: fragil\nversion: '1'\napi_version: 1\ncompartilhavel: true\nskills: [agir]\n"
        )
        (d / "skills.yaml").write_text(
            "skills:\n- name: agir\n  description: age\n  executor: {type: native,"
            " handler: fragil.agir}\n  fatos_de_modulo: [fragil.estado]\n"
        )
        chamadas = []

        class Fragil(BaseModule):
            def install(self, host):
                host.handlers.register("agir", lambda ctx, args: chamadas.append(1) or {"ok": 1})
                host.registrar_provedor_fatos("estado", self.provedor, ["perigoso"])

            def provedor(self, ctx, args):
                raise RuntimeError("quebrou")

        register_module(Fragil(d))
        yield chamadas
        unregister_module("fragil")

    def test_fail_closed(self, modulo, tmp_path):
        cfg = tmp_path / "cfg" / "c1" / "config"
        cfg.mkdir(parents=True)
        (cfg / "client_config.yaml").write_text(
            "client_id: c1\nrbac_default: allow\ndomain_allowlist: [a.example.com]\n"
            "modules: [fragil]\n"
        )
        (cfg / "rules.yaml").write_text("schema_version: 1\nclient_id: c1\nrules: []\n")
        (cfg / "skills.yaml").write_text(
            "schema_version: 1\nclient_id: c1\nskills:\n- usar: fragil.agir\n  autonomia: A0\n"
        )
        engine = BRMEngine("c1", tmp_path / "cfg", tmp_path / "data")
        como(SEC)
        r = engine.call_skill("agir", {})
        assert r["allowed"] is False and "fatos" in r["reason"]
        assert modulo == []
        assert engine.avaliar_skill("agir", {})["efeito"] == "deny"
        assert auditoria(engine, "fatos_indisponiveis")

    def test_skill_com_provedor_desconhecido_nao_sobe(self, tmp_path):
        cfg = tmp_path / "cfg" / "c2" / "config"
        cfg.mkdir(parents=True)
        (cfg / "client_config.yaml").write_text(
            "client_id: c2\nrbac_default: allow\ndomain_allowlist: [a.example.com]\n"
        )
        (cfg / "rules.yaml").write_text("schema_version: 1\nclient_id: c2\nrules: []\n")
        (cfg / "skills.yaml").write_text(
            "schema_version: 1\nclient_id: c2\nskills:\n- name: x\n  description: d\n"
            "  executor: {type: native, handler: t.x}\n  fatos_de_modulo: [nada.nada]\n"
        )
        with pytest.raises(ModuleError):
            BRMEngine("c2", tmp_path / "cfg", tmp_path / "data")


class TestAprovadorNoContexto:
    @pytest.fixture
    def capturado(self, tmp_path):
        d = tmp_path / "mods" / "ctxmod"
        d.mkdir(parents=True)
        (d / "module.yaml").write_text(
            "name: ctxmod\nversion: '1'\napi_version: 1\ncompartilhavel: true\n"
            "skills: [confirma, aprova, direto]\n"
        )
        (d / "skills.yaml").write_text(
            "skills:\n"
            "- {name: confirma, description: c, autonomia: A2,"
            " executor: {type: native, handler: ctxmod.registra}}\n"
            "- {name: aprova, description: a, autonomia: A3,"
            " executor: {type: native, handler: ctxmod.registra}}\n"
            "- {name: direto, description: d, autonomia: A0,"
            " executor: {type: native, handler: ctxmod.registra}}\n"
        )
        vistos = []

        class Ctx(BaseModule):
            def install(self, host):
                def registra(ctx: HandlerContext, args):
                    vistos.append((ctx.actor, ctx.confirmado_por, ctx.aprovado_por))
                    return {"ok": True}

                host.handlers.register("registra", registra)

        register_module(Ctx(d))
        cfg = tmp_path / "cfg" / "c3" / "config"
        cfg.mkdir(parents=True)
        (cfg / "client_config.yaml").write_text(
            "client_id: c3\nrbac_default: allow\ndomain_allowlist: [a.example.com]\n"
            "modules: [ctxmod]\n"
        )
        (cfg / "rules.yaml").write_text("schema_version: 1\nclient_id: c3\nrules: []\n")
        (cfg / "skills.yaml").write_text(
            "schema_version: 1\nclient_id: c3\nskills:\n- usar: ctxmod.confirma\n"
            "- usar: ctxmod.aprova\n- usar: ctxmod.direto\n"
        )
        engine = BRMEngine("c3", tmp_path / "cfg", tmp_path / "data")
        yield engine, vistos
        unregister_module("ctxmod")

    def test_confirmacao_aprovacao_e_execucao_direta(self, capturado):
        engine, vistos = capturado
        como(SEC)
        engine.call_skill("direto", {})
        prop = engine.call_skill("confirma", {})
        engine.confirmar_operacao(prop["operacao_id"])
        a3 = engine.call_skill("aprova", {})
        como(GESTOR)
        engine.aprovar_operacao(a3["operacao_id"])
        assert vistos == [
            (SEC.identity, None, None),
            (SEC.identity, SEC.identity, None),
            (SEC.identity, None, GESTOR.identity),
        ]

    def test_chamador_nao_consegue_informar_aprovador(self, capturado):
        engine, vistos = capturado
        como(SEC)
        engine.call_skill("direto", {"aprovado_por": "gestor@x", "confirmado_por": "x"})
        assert vistos == [(SEC.identity, None, None)]


class TestOperacoesParaModulos:
    def test_contagens_agregadas_sem_argumentos(self, motor):
        engine, _, _ = motor
        como(SEC)
        engine.call_skill("registrar_recebimento", {**ARGS, "valor": "100"})
        engine.call_skill("registrar_recebimento", ARGS)
        resumo = engine.operacoes.contagens()
        assert resumo["por_estado"] == {"aguardando_aprovacao": 1, "proposta": 1}
        assert resumo["por_skill"]["registrar_recebimento"]["aguardando_aprovacao"] == 1
        assert resumo["aguardando_aprovacao"] == 1
        assert "Paciente" not in json.dumps(resumo)

    def test_expirada_nao_conta_como_aguardando(self, motor):
        engine, _, _ = motor
        engine._client_config = engine._client_config.model_copy(update={"operacao_ttl_horas": 0})
        como(SEC)
        engine.call_skill("registrar_recebimento", {**ARGS, "valor": "100"})
        assert engine.operacoes.contagens()["aguardando_aprovacao"] == 0

    def test_painel_financeiro_mostra_operacoes_aguardando(self, motor):
        engine, _, _ = motor
        como(SEC)
        engine.call_skill("registrar_recebimento", {**ARGS, "valor": "100"})
        como(GESTOR)
        r = engine.call_skill(
            "consultar_financeiro", {"consulta": "painel_financeiro", "periodo": "2026-02"}
        )
        dados = json.loads(r["result"])["dados"]
        assert dados["operacoes_aguardando_aprovacao"] == 1


class TestSaudeDosModulos:
    def _cliente(self, motor):
        engine, path, _ = motor
        engine.user_store.create_user("root@clinica.test", Role.SUPER_ADMIN, "tok-root")
        return engine, path, TestClient(create_app(engine))

    def test_saudavel_mantem_o_formato_antigo(self, motor):
        _, _, client = self._cliente(motor)
        r = client.get("/health")
        assert r.status_code == 200 and r.json() == {"status": "ok"}

    def test_violacao_de_invariante_devolve_503_sem_vazar_dados(self, motor):
        engine, path, client = self._cliente(motor)
        como(GESTOR)
        prop = engine.call_skill("registrar_recebimento", ARGS)
        engine.confirmar_operacao(prop["operacao_id"])
        con = sqlite3.connect(path)
        con.execute("DROP TRIGGER repasse_no_update")
        con.execute(
            "UPDATE repasse SET parte_clinica_centavos = parte_clinica_centavos + 1,"
            " liquido_centavos = liquido_centavos + 1"
        )
        con.commit()
        con.close()
        r = client.get("/health")
        assert r.status_code == 503
        corpo = r.json()
        assert corpo["status"] == "degraded"
        assert corpo["modulos"]["financeiro.integridade"]["ok"] is False
        assert "F6" in corpo["modulos"]["financeiro.integridade"]["invariantes"]
        assert "Paciente" not in r.text

    def test_verificacao_que_levanta_excecao_degrada(self, motor):
        engine, _, client = self._cliente(motor)

        def quebra():
            raise RuntimeError("x")

        engine.extensoes.verificacoes["teste.quebra"] = quebra
        assert client.get("/health").status_code == 503

    def test_ledger_ausente_degrada(self, motor):
        engine, path, client = self._cliente(motor)
        shutil.move(path, path.with_suffix(".bak"))
        assert client.get("/health").status_code == 503
