"""Gatilhos sobre o módulo financeiro: eventos pós-commit e aviso de sessão sem desfecho."""

import json
from datetime import UTC, date, datetime, timedelta

import pytest
import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from audit.sink import FileSystemAuditSink
from mcp_engine.core.auth import Role, current_user_var, set_current_user
from mcp_engine.core.mcp_server import BRMEngine
from mcp_engine.core.state import EventoRow, GatilhoAlertaRow, GatilhoExecucaoRow
from mcp_engine.modules.financeiro import fatos, params, sessoes
from mcp_engine.modules.financeiro import handlers as h
from mcp_engine.modules.financeiro.db import abrir, caminho_ledger, transacao
from mcp_engine.vault.obsidian_adapter import ObsidianVaultProvider
from scripts.novo_cliente import criar_cliente
from tests.gatilhos_util import ANA, BIA, LUCAS, FakeEmbeddings, gatilho_bloco, gatilho_nota

CLIENTE = "clinica-gat"
AGORA = datetime(2026, 3, 2, 21, 0, 30, tzinfo=UTC)
ANTES = datetime(2026, 3, 2, 20, 55, tzinfo=UTC)
ARGS = {
    "data_pagamento": "2026-03-01",
    "paciente": "Paciente A",
    "forma": "pix",
    "meio": "Sicoob",
    "valor": "275",
}


def skill_usar(nome: str, autonomia: str) -> str:
    return (
        f"---\ntipo: skill\nid: {nome}\n---\nAdota {nome}.\n\n```brm-config\n"
        f"usar: financeiro.{nome}\nautonomia: {autonomia}\n```\n"
    )


class Ambiente:
    def __init__(self, raiz) -> None:
        self.raiz = raiz
        self.cfg = raiz / "clients"
        self.data = raiz / "data"
        criar_cliente(CLIENTE, ["financeiro"], self.cfg)
        arquivo = self.cfg / CLIENTE / "config" / "client_config.yaml"
        dados = yaml.safe_load(arquivo.read_text())
        dados["config_via_notas"] = True
        arquivo.write_text(yaml.safe_dump(dados))
        self.agora = ANTES
        provider = ObsidianVaultProvider(
            self.cfg, self.data, FileSystemAuditSink(self.data), FakeEmbeddings()
        )  # noqa: E501
        self.engine = BRMEngine(CLIENTE, self.cfg, self.data, vault_provider=provider)
        self.engine.gatilhos.relogio = lambda: self.agora
        self.ledger = caminho_ledger(self.data, CLIENTE)
        con = abrir(self.ledger)
        with transacao(con):
            u = params.garantir_unidade(con, "Adultos")
            p = params.garantir_profissional(con, "Ana Souza")
            self.paciente = params.garantir_paciente(
                con, "Paciente A", unidade_id=u, profissional_id=p, valor_sessao_centavos=27500
            )
        con.close()
        params.materializar_meio(
            self.ledger, {"nome": "Sicoob", "tipo": "banco", "vigente_de": "2025-01-01"},
            criado_por="t",
        )  # fmt: skip
        params.materializar_comissao(
            self.ledger,
            {"profissional": "Ana Souza", "tipo": "percentual", "valor": "40", "base": "liquido",
             "vigente_de": "2025-01-01"},
            criado_por="t",
        )  # fmt: skip
        for nome, nivel in (
            ("registrar_recebimento", "A2"),
            ("listar_pendencias", "A0"),
            ("consultar_financeiro", "A0"),
        ):
            self.ativar(f"skills/{nome}.md", skill_usar(nome, nivel))
        for tool in ("listar_pendencias", "consultar_financeiro"):
            self.engine.user_store.set_permission(Role.VIEWER, tool, True)

    def ativar(self, caminho: str, texto: str) -> None:
        destino = self.engine._vault_root() / caminho
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto)
        set_current_user(ANA)
        proposta = self.engine.config_propor(caminho)
        assert proposta["allowed"], proposta
        set_current_user(BIA)
        assert self.engine.aprovar_operacao(proposta["operacao_id"])["allowed"]

    def gatilho(self, **mudancas):
        bloco = gatilho_bloco(**mudancas)
        self.ativar(f"gatilhos/{bloco['id']}.md", gatilho_nota(bloco))

    def sessao_passada(self, agenda_id="ag-1", inicio="2026-03-01T13:00:00Z"):
        con = abrir(self.ledger)
        with transacao(con):
            sessoes.upsert_sessao(
                con, agenda_id=agenda_id, paciente_id=self.paciente, inicio_utc=inicio
            )
        con.close()

    def alertas(self):
        with Session(self.engine._state.engine) as s:
            return list(s.scalars(select(GatilhoAlertaRow).order_by(GatilhoAlertaRow.id)))

    def contar(self, modelo, *filtros):
        with Session(self.engine._state.engine) as s:
            return s.scalar(select(func.count()).select_from(modelo).where(*filtros)) or 0


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    for chave in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "RESEND_BRM_API_KEY"):
        monkeypatch.delenv(chave, raising=False)
    monkeypatch.setattr(h, "hoje", lambda: date(2026, 3, 2))
    monkeypatch.setattr(fatos, "agora", lambda: AGORA)
    token = current_user_var.set(None)
    yield
    current_user_var.reset(token)


@pytest.fixture
def amb(tmp_path) -> Ambiente:
    return Ambiente(tmp_path)


class TestAvisoDeSessaoSemDesfecho:
    def gatilho(self, amb):
        amb.gatilho(
            id="aviso-sessao-sem-desfecho",
            descricao="Avisa sobre sessões passadas sem desfecho",
            fatos_de_modulo=["financeiro.situacao"],
            condicao={">": [{"var": "sessoes_sem_desfecho"}, 0]},
            acao={"skill": "listar_pendencias", "argumentos": {}},
            permissoes=["listar_pendencias"],
            saida={"tipo": "alerta", "nivel": "aviso",
                   "mensagem": "{{ sessoes_sem_desfecho }} sessão(ões) passada(s) sem desfecho."},
        )  # fmt: skip

    def test_avisa_quando_ha_sessao_passada_sem_desfecho(self, amb):
        self.gatilho(amb)
        amb.sessao_passada()
        amb.engine.gatilhos.tick(ANTES)
        amb.agora = AGORA
        amb.engine.gatilhos.tick(AGORA)
        textos = [a.texto for a in amb.alertas()]
        assert textos == ["1 sessão(ões) passada(s) sem desfecho."]

    def test_nao_avisa_sem_sessoes(self, amb):
        self.gatilho(amb)
        amb.engine.gatilhos.tick(ANTES)
        amb.engine.gatilhos.tick(AGORA)
        assert amb.alertas() == []
        with Session(amb.engine._state.engine) as s:
            assert s.scalar(select(GatilhoExecucaoRow.status)) == "condicao_falsa"

    def test_sessao_futura_ou_com_desfecho_nao_conta(self, amb):
        self.gatilho(amb)
        amb.sessao_passada("futura", "2026-03-05T13:00:00Z")
        con = abrir(amb.ledger)
        with transacao(con):
            sessoes.upsert_sessao(
                con, agenda_id="feita", paciente_id=amb.paciente,
                inicio_utc="2026-03-01T12:00:00Z", status_agenda="COMPLETED",
            )  # fmt: skip
        con.close()
        amb.engine.gatilhos.tick(ANTES)
        amb.engine.gatilhos.tick(AGORA)
        assert [a.texto for a in amb.alertas()] == []

    def test_ator_de_sistema_so_ve_agregados(self, amb):
        self.gatilho(amb)
        amb.sessao_passada()
        resultado = amb.engine.gatilhos.simular(config_id="aviso-sessao-sem-desfecho")
        assert (
            resultado["decisao"] == "executaria" and resultado["fatos"]["sessoes_sem_desfecho"] == 1
        )
        assert "Paciente A" not in json.dumps(resultado)

    def test_rbac_default_deny_exige_conceder_a_skill_ao_papel(self, amb):
        self.gatilho(amb)
        amb.engine.user_store.set_permission(Role.VIEWER, "listar_pendencias", False)
        amb.sessao_passada()
        amb.engine.gatilhos.tick(ANTES)
        amb.engine.gatilhos.tick(AGORA)
        with Session(amb.engine._state.engine) as s:
            assert s.scalar(select(GatilhoExecucaoRow.status)) == "negado"


class TestEventoDeRecebimento:
    def gatilho(self, amb):
        amb.gatilho(
            id="recebimento-grande",
            descricao="Avisa recebimentos acima de R$ 200",
            quando={"evento": "recebimento.registrado"},
            condicao={">=": [{"var": "evento.valor_centavos"}, 20000]},
            acao={"skill": "consultar_financeiro",
                  "argumentos": {"consulta": "sem_nf", "periodo": "{{ mes_atual }}"}},
            permissoes=["consultar_financeiro"],
            saida={"tipo": "alerta",
                   "mensagem": "Recebimento {{ evento.recebimento_id }} de "
                               "{{ evento.valor_centavos }} centavos ({{ evento.nf_status }})."},
        )  # fmt: skip

    def test_evento_nasce_depois_da_confirmacao_e_dispara_o_gatilho(self, amb):
        self.gatilho(amb)
        set_current_user(LUCAS)
        prop = amb.engine.call_skill("registrar_recebimento", ARGS)
        assert prop["status"] == "proposta"
        assert amb.contar(EventoRow) == 0
        amb.engine.confirmar_operacao(prop["operacao_id"])
        assert amb.contar(EventoRow) == 1
        amb.engine.gatilhos.tick(AGORA)
        assert [a.texto for a in amb.alertas()] == ["Recebimento 1 de 27500 centavos (pendente)."]

    def test_lancamento_recusado_nao_publica(self, amb):
        self.gatilho(amb)
        set_current_user(LUCAS)
        prop = amb.engine.call_skill("registrar_recebimento", {**ARGS, "paciente": "Fulano"})
        res = amb.engine.confirmar_operacao(prop["operacao_id"])
        assert json.loads(res["result"])["registrado"] is False
        assert amb.contar(EventoRow) == 0

    def test_reexecucao_idempotente_nao_duplica_o_evento(self, amb):
        set_current_user(LUCAS)
        prop = amb.engine.call_skill("registrar_recebimento", ARGS)
        amb.engine.confirmar_operacao(prop["operacao_id"])
        amb.engine._state.save_execucao  # noqa: B018
        op = amb.engine.operacoes.get(prop["operacao_id"])
        with Session(amb.engine._state.engine) as s:
            s.query(
                __import__("mcp_engine.core.state", fromlist=["ExecucaoRow"]).ExecucaoRow
            ).delete()
            s.commit()
        amb.engine._call_skill(op.skill, dict(op.argumentos), idempotency_key=op.id, _aprovada=True)
        assert amb.contar(EventoRow) == 1

    def test_valor_abaixo_do_corte_nao_alerta(self, amb):
        self.gatilho(amb)
        set_current_user(BIA)
        prop = amb.engine.call_skill("registrar_recebimento", {**ARGS, "valor": "150"})
        amb.engine.confirmar_operacao(prop["operacao_id"])
        amb.engine.gatilhos.tick(AGORA)
        assert amb.alertas() == []

    def test_fechar_mes_publica_evento_com_aprovador_real(self, amb):
        set_current_user(BIA)
        prop = amb.engine.call_skill(
            "registrar_recebimento", {**ARGS, "data_pagamento": "2026-02-10", "valor": "275"}
        )
        amb.engine.confirmar_operacao(prop["operacao_id"])
        amb.ativar("skills/fechar_mes.md", skill_usar("fechar_mes", "A3"))
        set_current_user(ANA)
        fechar = amb.engine.call_skill("fechar_mes", {"mes": "2026-02"})
        set_current_user(BIA)
        res = amb.engine.aprovar_operacao(fechar["operacao_id"])
        assert json.loads(res["result"])["aprovado_por"] == BIA.identity
        with Session(amb.engine._state.engine) as s:
            tipos = list(s.scalars(select(EventoRow.tipo).order_by(EventoRow.id)))
        assert tipos == ["recebimento.registrado", "mes.fechado"]


class TestFatosDeSituacao:
    def test_provedor_situacao_e_somente_leitura_e_rapido(self, amb):
        import time

        from mcp_engine.core.fatos import ContextoFatos

        amb.sessao_passada()
        ctx = ContextoFatos(CLIENTE, "sistema:x", "viewer", amb.data)
        antes = amb.ledger.stat().st_mtime_ns
        t0 = time.perf_counter()
        fatos = amb.engine.extensoes.provedores.calcular(["financeiro.situacao"], ctx, {})
        assert time.perf_counter() - t0 < 0.05
        assert fatos["sessoes_sem_desfecho"] == 1 and fatos["mes_anterior_aberto"] is True
        assert fatos["dias_para_fim_do_mes"] == 29 and fatos["qtd_sem_nf"] == 0
        assert amb.ledger.stat().st_mtime_ns == antes

    def test_mes_anterior_fechado_muda_o_fato(self, amb):
        con = abrir(amb.ledger)
        with transacao(con):
            con.execute(
                "INSERT INTO fechamento_mensal (mes, unidade_id, status, aprovado_por, hash,"
                " criado_em) VALUES ('2026-02', NULL, 'fechado', 'x', 'h', '2026-03-01')"
            )
        con.close()
        from mcp_engine.core.fatos import ContextoFatos

        ctx = ContextoFatos(CLIENTE, "sistema:x", "viewer", amb.data)
        fatos = amb.engine.extensoes.provedores.calcular(["financeiro.situacao"], ctx, {})
        assert fatos["mes_anterior_aberto"] is False

    def test_lembrete_de_fechar_o_mes_usa_o_fato(self, amb):
        amb.gatilho(
            id="lembrete-fechar-mes",
            fatos_de_modulo=["financeiro.situacao"],
            condicao={"==": [{"var": "mes_anterior_aberto"}, True]},
            acao={"skill": "listar_pendencias", "argumentos": {}},
            permissoes=["listar_pendencias"],
            saida={"tipo": "alerta", "mensagem": "Feche {{ mes_anterior }}."},
        )
        amb.engine.gatilhos.tick(ANTES)
        amb.engine.gatilhos.tick(AGORA)
        assert [a.texto for a in amb.alertas()] == ["Feche 2026-02."]

    def test_operacao_aguardando_ha_horas_usa_fato_do_nucleo(self, amb):
        amb.gatilho(
            id="operacao-parada",
            fatos_de_modulo=["nucleo.operacoes"],
            condicao={">": [{"var": "operacoes_aguardando_horas_max"}, 1]},
            acao={"skill": "listar_pendencias", "argumentos": {}},
            permissoes=["listar_pendencias"],
            saida={"tipo": "alerta", "nivel": "critico",
                   "mensagem": "{{ operacoes_aguardando }} operação(ões) parada(s)."},
        )  # fmt: skip
        set_current_user(ANA)
        amb.engine.call_skill("registrar_recebimento", {**ARGS, "valor": "100"})
        amb.engine.gatilhos.tick(ANTES)
        amb.engine.gatilhos.tick(AGORA)
        assert amb.alertas() == []
        with Session(amb.engine._state.engine) as s:
            from mcp_engine.core.state import OperacaoRow

            s.query(OperacaoRow).update({"criado_em": datetime.now(UTC) - timedelta(hours=3)})
            s.commit()
        amb.engine.gatilhos.tick(AGORA + timedelta(days=1))
        assert amb.contar(GatilhoExecucaoRow) == 2
