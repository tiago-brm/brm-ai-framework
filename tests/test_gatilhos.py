"""Gatilhos e automações (spec 031): esquema, cron, agendador, autonomia, eventos e limites."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from mcp_engine.core import cron
from mcp_engine.core.auth import Role, current_user_var, set_current_user
from mcp_engine.core.http_app import create_app
from mcp_engine.core.state import (
    EventoRow,
    GatilhoAlertaRow,
    GatilhoExecucaoRow,
    GatilhoRow,
    OperacaoRow,
)
from tests.gatilhos_util import (
    ANA,
    ANTES_21H,
    BIA,
    LUCAS,
    ROOT,
    SEG_21H,
    Cenario,
    gatilho_bloco,
    gatilho_nota,
    skill_nota,
)


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    for chave in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "RESEND_BRM_API_KEY"):
        monkeypatch.delenv(chave, raising=False)
    token = current_user_var.set(None)
    yield
    current_user_var.reset(token)


@pytest.fixture
def cen(tmp_path) -> Cenario:
    return Cenario(tmp_path)


def contar(eng, modelo, *filtros) -> int:
    with Session(eng._state.engine) as s:
        return s.scalar(select(func.count()).select_from(modelo).where(*filtros)) or 0


def execucoes(eng, **filtro):
    with Session(eng._state.engine) as s:
        stmt = select(GatilhoExecucaoRow).order_by(GatilhoExecucaoRow.id)
        for chave, valor in filtro.items():
            stmt = stmt.where(getattr(GatilhoExecucaoRow, chave) == valor)
        rows = list(s.scalars(stmt))
        for r in rows:
            s.expunge(r)
        return rows


def ops_de(cen, skill):
    return [o for o in cen.engine.operacoes.listar() if o.skill == skill]


def alertas(eng):
    with Session(eng._state.engine) as s:
        return list(s.scalars(select(GatilhoAlertaRow).order_by(GatilhoAlertaRow.id)))


def linha_gatilho(eng, gid="g1") -> GatilhoRow:
    row = eng.gatilhos._get(gid)
    assert row is not None
    return row


def eventos_audit(eng, prefixo):
    return [e for e in eng._audit_sink.iter_events("example") if e.action.startswith(prefixo)]


class TestCron:
    def test_proxima_ocorrencia_em_dia_util(self):
        c = cron.parse("0 18 * * 1-5")
        seg = datetime(2026, 3, 2, 20, 55, tzinfo=UTC)
        assert cron.proxima(c, seg) == datetime(2026, 3, 2, 21, 0, tzinfo=UTC)
        sex = datetime(2026, 3, 6, 21, 0, tzinfo=UTC)
        assert cron.proxima(c, sex) == datetime(2026, 3, 9, 21, 0, tzinfo=UTC)

    def test_passos_listas_e_intervalos(self):
        c = cron.parse("*/15 8-9 * * *")
        assert c.minutos == (0, 15, 30, 45) and c.horas == (8, 9)
        assert cron.parse("0 9 1,15 * *").dias == frozenset({1, 15})
        assert cron.parse("5-10/2 * * * *").minutos == (5, 7, 9)

    def test_dia_do_mes_ou_dia_da_semana(self):
        c = cron.parse("0 0 13 * 5")
        assert c.dia_casa(datetime(2026, 3, 13).date())
        assert c.dia_casa(datetime(2026, 3, 6).date())
        assert not c.dia_casa(datetime(2026, 3, 7).date())

    @pytest.mark.parametrize(
        "expr",
        [
            "",
            "* * * *",
            "60 * * * *",
            "* 24 * * *",
            "* * 0 * *",
            "* * * 13 *",
            "*/0 * * * *",
            "a * * * *",
            "5-1 * * * *",
            "@daily",
            "* * * * * *",
        ],
    )
    def test_expressoes_invalidas(self, expr):
        with pytest.raises(cron.CronErro):
            cron.parse(expr)

    def test_dia_inexistente_nunca_dispara(self):
        with pytest.raises(cron.CronErro):
            cron.proxima(cron.parse("0 0 31 2 *"), datetime(2026, 1, 1, tzinfo=UTC))

    def test_fuso_do_cliente(self):
        c = cron.parse("0 18 * * *")
        sp = cron.proxima(c, datetime(2026, 3, 2, 12, 0, tzinfo=UTC), "America/Sao_Paulo")
        ny = cron.proxima(c, datetime(2026, 3, 2, 12, 0, tzinfo=UTC), "America/New_York")
        assert sp.hour == 21 and ny.hour == 23


class TestEsquemaDoGatilho:
    def validar(self, cen, **mudancas):
        r = cen.propor_gatilho(**mudancas)
        return r

    def test_gatilho_valido_vira_proposta_com_resumo_do_ator(self, cen):
        r = self.validar(cen)
        assert r["status"] == "aguardando_aprovacao"
        assert "sistema:g1" in r["resumo"] and "Se rodasse agora" in r["resumo"]
        assert contar(cen.engine, GatilhoRow) == 0

    @pytest.mark.parametrize(
        "mudanca",
        [
            {"quando": {"agenda": "0 18 * * *", "evento": "a.b"}},
            {"quando": {}},
            {"quando": {"agenda": "99 * * * *"}},
            {"quando": {"evento": "semponto"}},
            {"quando": {"agenda": "0 18 * * *", "fuso": "Marte/Olimpo"}},
            {"acao": {"skill": "naoexiste", "argumentos": {}}, "permissoes": ["naoexiste"]},
            {"permissoes": ["efeito_a1"]},
            {"permissoes": ["leitura", "fantasma"]},
            {
                "autonomia": "A0",
                "acao": {"skill": "efeito_a2", "argumentos": {}},
                "permissoes": ["efeito_a2"],
            },
            {
                "saida": {"tipo": "email", "mensagem": "x"},
                "destinatario": "nao-e-email",
                "permissoes": ["leitura", "enviar_email"],
            },
            {"saida": {"tipo": "email", "mensagem": "x"}, "destinatario": "a@b.com"},
            {"saida": {"tipo": "alerta", "mensagem": "x", "assunto": "y"}},
            {"condicao": {"==": [{"var": "inexistente"}, 1]}},
            {"acao": {"skill": "leitura", "argumentos": {"a": "{{ nada }}"}}},
            {"acao": {"skill": "leitura", "argumentos": {"a": "{{ hoje"}}},
            {"fatos_de_modulo": ["fantasma.fato"]},
            {"campo_desconhecido": 1},
            {"papel": "super_admin"},
            {"limites": {"max_por_hora": 0}},
            {"limites": {"horario": {"das": "8h", "ate": "20:00"}}},
            {"permissoes": []},
        ],
    )
    def test_blocos_invalidos_sao_recusados(self, cen, mudanca):
        r = self.validar(cen, **mudanca)
        assert r["allowed"] is False and r.get("erros")

    def test_url_de_esquema_proibido_no_argumento(self, cen):
        r = self.validar(
            cen, acao={"skill": "leitura", "argumentos": {"url": "file:///etc/passwd"}}
        )
        assert r["allowed"] is False

    def test_evento_aceita_variavel_do_evento_na_condicao(self, cen):
        r = self.validar(
            cen,
            quando={"evento": "recebimento.registrado"},
            condicao={">": [{"var": "evento.valor_centavos"}, 100]},
        )
        assert r["status"] == "aguardando_aprovacao"

    def test_agenda_nao_aceita_variavel_de_evento(self, cen):
        r = self.validar(cen, condicao={">": [{"var": "evento.valor_centavos"}, 100]})
        assert r["allowed"] is False

    def test_aprovacao_materializa_e_projeta_o_compilado(self, cen):
        cen.gatilho()
        row = linha_gatilho(cen.engine)
        assert row.ativo and row.aprovado_por == BIA.identity and row.versao == 1
        compilado = cen.engine.caderno.compilado / "gatilhos.yaml"
        assert "g1" in compilado.read_text()
        assert cen.engine.caderno.divergencias() == []

    def test_edicao_posterior_nao_altera_o_gatilho_ativo(self, cen):
        cen.gatilho()
        cen.escrever("gatilhos/g1.md", gatilho_nota(gatilho_bloco(descricao="MUDOU")))
        assert json.loads(linha_gatilho(cen.engine).definicao)["descricao"] == "Gatilho de teste"

    def test_nova_versao_aprovada_substitui_e_reabre(self, cen):
        cen.gatilho()
        cen.engine.gatilhos.desativar("g1", "teste")
        assert not linha_gatilho(cen.engine).ativo
        cen.gatilho(descricao="Versão dois")
        row = linha_gatilho(cen.engine)
        assert row.ativo and row.versao == 2

    def test_reiniciar_reconcilia_a_tabela_com_os_snapshots(self, cen):
        cen.gatilho()
        with Session(cen.engine._state.engine) as s:
            s.query(GatilhoRow).delete()
            s.commit()
        novo = cen.subir()
        assert novo.gatilhos._get("g1") is not None


class TestAgendador:
    def preparar(self, cen, **mudancas):
        cen.gatilho(**mudancas)
        assert cen.engine.gatilhos.tick(ANTES_21H) == {"agendas": 0, "eventos": 0}
        proxima = linha_gatilho(cen.engine).proxima_execucao
        assert proxima == datetime(2026, 3, 2, 21, 0)

    def test_agenda_dispara_na_janela_uma_vez(self, cen):
        self.preparar(cen)
        assert cen.engine.gatilhos.tick(ANTES_21H)["agendas"] == 0
        assert cen.ctr["leitura"] == 0
        assert cen.engine.gatilhos.tick(SEG_21H)["agendas"] == 1
        assert cen.engine.gatilhos.tick(SEG_21H + timedelta(seconds=5))["agendas"] == 0
        assert cen.ctr["leitura"] == 1
        ex = execucoes(cen.engine)
        assert [e.status for e in ex] == ["ok"] and ex[0].chave == "g1@2026-03-02T21:00+00:00"
        assert linha_gatilho(cen.engine).proxima_execucao == datetime(2026, 3, 3, 21, 0)

    def test_nao_duplica_apos_reinicio(self, cen):
        self.preparar(cen)
        cen.engine.gatilhos.tick(SEG_21H)
        reiniciado = cen.subir()
        reiniciado.gatilhos.tick(SEG_21H + timedelta(seconds=2))
        assert cen.ctr["leitura"] == 1 and len(execucoes(reiniciado)) == 1

    def test_dois_agendadores_concorrentes_nao_duplicam(self, cen):
        self.preparar(cen)
        outro = cen.subir()
        a = cen.engine.gatilhos.executar(
            "g1", janela="2026-03-02T21:00+00:00", origem="agenda", agora=SEG_21H
        )
        b = outro.gatilhos.executar(
            "g1", janela="2026-03-02T21:00+00:00", origem="agenda", agora=SEG_21H
        )
        assert a["status"] == "ok" and b["status"] == "ja_executada"
        assert cen.ctr["leitura"] == 1

    def test_queda_no_meio_da_janela_retoma_sem_duplicar(self, cen):
        self.preparar(cen)
        with Session(cen.engine._state.engine) as s:
            s.add(
                GatilhoExecucaoRow(
                    gatilho_id="g1",
                    chave="g1@2026-03-02T21:00+00:00",
                    janela="2026-03-02T21:00+00:00",
                    origem="agenda",
                    status="executando",
                    hash_definicao="x",
                    iniciado_em=datetime(2026, 3, 2, 20, 50),
                )
            )
            s.commit()
        cen.engine.gatilhos.tick(SEG_21H)
        reiniciado = cen.subir()
        reiniciado.gatilhos.tick(SEG_21H + timedelta(seconds=3))
        ex = execucoes(cen.engine)
        assert [e.status for e in ex] == ["ok"] and cen.ctr["leitura"] == 1

    def test_execucao_em_andamento_de_outro_processo_nao_e_roubada(self, cen):
        self.preparar(cen)
        with Session(cen.engine._state.engine) as s:
            s.add(
                GatilhoExecucaoRow(
                    gatilho_id="g1",
                    chave="g1@2026-03-02T21:00+00:00",
                    janela="2026-03-02T21:00+00:00",
                    origem="agenda",
                    status="executando",
                    hash_definicao="x",
                    iniciado_em=datetime(2026, 3, 2, 21, 0, 10),
                )
            )
            s.commit()
        cen.engine.gatilhos.tick(SEG_21H)
        assert cen.ctr["leitura"] == 0
        assert linha_gatilho(cen.engine).proxima_execucao == datetime(2026, 3, 2, 21, 0)

    def test_janela_vencida_alem_do_atraso_e_registrada_como_perdida(self, cen):
        self.preparar(cen)
        tarde = SEG_21H + timedelta(hours=3)
        cen.engine.gatilhos.tick(tarde)
        assert cen.ctr["leitura"] == 0
        assert [e.status for e in execucoes(cen.engine)] == ["perdida"]
        assert linha_gatilho(cen.engine).proxima_execucao == datetime(2026, 3, 3, 21, 0)

    def test_atraso_curto_ainda_executa_so_a_primeira_janela_vencida(self, cen):
        self.preparar(cen, limites={"atraso_maximo_min": 600})
        cen.engine.gatilhos.tick(SEG_21H + timedelta(hours=2))
        assert cen.ctr["leitura"] == 1 and len(execucoes(cen.engine)) == 1

    def test_t3_gatilho_inativo_nao_dispara(self, cen):
        self.preparar(cen)
        cen.engine.gatilhos.desativar("g1", "teste")
        cen.engine.gatilhos.tick(SEG_21H)
        assert cen.ctr["leitura"] == 0 and execucoes(cen.engine) == []
        r = cen.engine.gatilhos.executar("g1", janela="x", origem="agenda", agora=SEG_21H)
        assert r["status"] == "inativo" and cen.ctr["leitura"] == 0

    def test_t3_pausa_global_e_retomada(self, cen):
        self.preparar(cen)
        cen.engine.gatilhos.pausar(True, ROOT.identity)
        cen.engine.gatilhos.tick(SEG_21H)
        assert cen.ctr["leitura"] == 0
        cen.engine.gatilhos.pausar(False, ROOT.identity)
        cen.engine.gatilhos.tick(SEG_21H + timedelta(seconds=1))
        assert cen.ctr["leitura"] == 1
        assert eventos_audit(cen.engine, "gatilhos:pausa") and eventos_audit(
            cen.engine, "gatilhos:retomada"
        )

    def test_cliente_sem_gatilhos_ativos_nao_roda(self, cen):
        self.preparar(cen)
        cen.engine._client_config = cen.engine._client_config.model_copy(
            update={"gatilhos_ativos": False}
        )
        assert cen.engine.gatilhos.tick(SEG_21H) == {"agendas": 0, "eventos": 0}
        assert cen.ctr["leitura"] == 0

    def test_fora_do_horario_nao_executa(self, cen):
        self.preparar(cen, limites={"horario": {"das": "08:00", "ate": "17:00"}})
        cen.engine.gatilhos.tick(SEG_21H)
        assert cen.ctr["leitura"] == 0
        assert [e.status for e in execucoes(cen.engine)] == ["fora_do_horario"]

    def test_limite_por_hora_na_agenda(self, cen):
        cen.gatilho(quando={"agenda": "*/5 * * * *"}, limites={"max_por_hora": 2})
        t = ANTES_21H
        cen.engine.gatilhos.tick(t)
        for i in range(1, 6):
            cen.relogio.agora = t + timedelta(minutes=5 * i, seconds=1)
            cen.engine.gatilhos.tick(cen.relogio.agora)
        status = [e.status for e in execucoes(cen.engine)]
        assert status.count("ok") == 2 and status.count("limitado") == 3

    def test_auditoria_liga_gatilho_nota_aprovada_e_trace(self, cen):
        self.preparar(cen)
        cen.engine.gatilhos.tick(SEG_21H)
        evt = eventos_audit(cen.engine, "gatilho:ok")[0]
        assert evt.actor == "sistema:g1" and evt.trace_id
        assert evt.details["aprovado_por"] == BIA.identity
        assert evt.details["nota"] == "gatilhos/g1.md" and len(evt.details["hash_aprovado"]) == 64
        assert execucoes(cen.engine)[0].trace_id == evt.trace_id

    def test_loop_assincrono_executa_ticks_e_para(self, cen):
        self.preparar(cen)
        cen.relogio.agora = SEG_21H

        async def correr():
            parar = asyncio.Event()
            tarefa = asyncio.create_task(cen.engine.gatilhos.rodar(intervalo=0.01, parar=parar))
            await asyncio.sleep(0.3)
            parar.set()
            await asyncio.wait_for(tarefa, timeout=2)

        asyncio.run(correr())
        assert cen.ctr["leitura"] == 1
        assert cen.engine.gatilhos._controle("agendador_batimento")


class TestAutonomiaEPermissoes:
    def rodar(self, cen, **mudancas):
        cen.gatilho(**mudancas)
        cen.engine.gatilhos.tick(ANTES_21H)
        cen.engine.gatilhos.tick(SEG_21H)

    def test_a1_executa_sozinho_como_ator_de_sistema(self, cen):
        self.rodar(
            cen,
            acao={"skill": "efeito_a1", "argumentos": {}},
            autonomia="A1",
            permissoes=["efeito_a1"],
            saida={"tipo": "alerta", "mensagem": "feito"},
        )
        assert cen.ctr["efeito_a1"] == 1
        assert cen.ctr["efeito_a1:ctx"] == ("sistema:g1", "viewer", None, None)
        assert [e.status for e in execucoes(cen.engine)] == ["ok"]

    @pytest.mark.parametrize("nivel,skill", [("A2", "efeito_a2"), ("A3", "efeito_a3")])
    def test_t1_a2_e_a3_viram_operacao_aguardando_pessoa(self, cen, nivel, skill):
        self.rodar(
            cen,
            acao={"skill": skill, "argumentos": {}},
            autonomia=nivel,
            permissoes=[skill],
            saida={"tipo": "alerta", "mensagem": "{{ resultado.status }}"},
        )
        assert cen.ctr[skill] == 0
        ops = ops_de(cen, skill)
        assert len(ops) == 1 and ops[0].status == "aguardando_aprovacao"
        assert ops[0].proposto_por == "sistema:g1" and ops[0].skill == skill
        assert [e.status for e in execucoes(cen.engine)] == ["proposta"]
        assert execucoes(cen.engine)[0].operacao_id == ops[0].id
        assert "aguardando_aprovacao" in alertas(cen.engine)[0].texto
        set_current_user(ANA)
        assert cen.engine.confirmar_operacao(ops[0].id)["allowed"] is False
        assert cen.ctr[skill] == 0
        set_current_user(LUCAS)
        assert cen.engine.aprovar_operacao(ops[0].id)["allowed"] is False
        set_current_user(BIA)
        assert cen.engine.aprovar_operacao(ops[0].id)["allowed"] is True
        assert cen.ctr[skill] == 1
        assert cen.ctr[f"{skill}:ctx"] == ("sistema:g1", "viewer", None, BIA.identity)

    @pytest.mark.parametrize("skill", ["efeito_a2", "efeito_a3"])
    def test_t1_chamada_direta_como_sistema_nunca_executa(self, cen, skill):
        set_current_user(ANA)
        resposta = cen.engine._call_skill(skill, {}, _sistema=True)
        assert resposta["allowed"] is False and cen.ctr[skill] == 0
        assert ops_de(cen, skill)[0].status == "aguardando_aprovacao"

    def test_t1_a1_com_condicao_fora_do_limite_sobe_para_proposta(self, cen):
        extra = 'a1_condicao: {"==": [{"var": "limite_ok"}, true]}\n'
        cen.ativar("skills/a1_cond.md", skill_nota("a1_cond", "A1", "t.a1", extra))
        self.rodar(
            cen, acao={"skill": "a1_cond", "argumentos": {}}, autonomia="A1", permissoes=["a1_cond"]
        )
        assert cen.ctr["efeito_a1"] == 0
        assert ops_de(cen, "a1_cond")[0].status == "aguardando_aprovacao"

    def test_sistema_nao_pode_ser_criado_como_usuario(self, cen):
        set_current_user(ROOT)
        r = cen.engine.criar_usuario("sistema:g1", "super_admin")
        assert r["allowed"] is False
        assert cen.engine.criar_usuario("Sistema:x", "admin")["allowed"] is False

    def test_rbac_do_cliente_pode_negar_ao_papel_do_gatilho(self, cen):
        cen.engine.user_store.set_permission(Role.VIEWER, "leitura", False)
        self.rodar(cen)
        assert cen.ctr["leitura"] == 0
        ex = execucoes(cen.engine)[0]
        assert ex.status == "negado" and "RBAC" in ex.erro
        assert alertas(cen.engine)[0].nivel == "critico"

    def test_papel_admin_declarado_chega_ao_handler(self, cen):
        self.rodar(cen, papel="admin")
        assert cen.ctr["leitura:ctx"][:2] == ("sistema:g1", "admin")

    def test_regra_deny_do_cliente_vale_para_o_ator_de_sistema(self, cen):
        regra = (
            "---\ntipo: regra\nid: bloqueia\n---\nx\n\n```brm-config\nid: bloqueia\n"
            'description: d\ncondition: {"==": [{"var": "actor_role"}, "viewer"]}\n'
            "effect: deny\nmessage: bloqueado\n```\n"
        )
        cen.ativar("regras/bloqueia.md", regra)
        self.rodar(cen)
        assert cen.ctr["leitura"] == 0
        assert execucoes(cen.engine)[0].status == "negado"

    def test_ator_de_sistema_sem_login_humano_e_identificado_na_auditoria(self, cen):
        self.rodar(cen)
        eventos = [
            e for e in cen.engine._audit_sink.iter_events("example") if e.actor == "sistema:g1"
        ]
        assert eventos and all(e.user_email == "sistema:g1" for e in eventos)


class TestEventosEOutbox:
    def test_evento_so_existe_depois_do_efeito_commitado(self, cen):
        set_current_user(LUCAS)
        cen.ativar("skills/emissor.md", skill_nota("emissor", "A2", "t.emite"))
        prop = cen.engine.call_skill("emissor", {})
        assert prop["pendente"] and contar(cen.engine, EventoRow) == 0
        cen.engine.confirmar_operacao(prop["operacao_id"])
        assert contar(cen.engine, EventoRow) == 1

    def test_handler_que_falha_nao_publica(self, cen):
        set_current_user(LUCAS)
        cen.engine.call_skill("quebra", {})
        assert contar(cen.engine, EventoRow) == 0

    def test_a2_pendente_ou_rejeitada_nao_publica(self, cen):
        cen.ativar("skills/emissor.md", skill_nota("emissor", "A3", "t.emite"))
        set_current_user(LUCAS)
        prop = cen.engine.call_skill("emissor", {})
        set_current_user(BIA)
        cen.engine.rejeitar_operacao(prop["operacao_id"], "não")
        assert contar(cen.engine, EventoRow) == 0

    def test_evento_chaveado_nao_duplica(self, cen):
        cen.publicar("demo.ocorreu", {"n": 1}, dedup="k1")
        cen.publicar("demo.ocorreu", {"n": 1}, dedup="k1")
        assert contar(cen.engine, EventoRow) == 1

    def test_evento_invalido_e_descartado(self, cen):
        cen.publicar("SemPonto", {})
        cen.publicar("demo.grande", {"x": "a" * 5000})
        assert contar(cen.engine, EventoRow) == 0

    def test_gatilho_por_evento_usa_payload_na_condicao_e_no_modelo(self, cen):
        cen.gatilho(
            quando={"evento": "demo.ocorreu"},
            condicao={">=": [{"var": "evento.valor"}, 100]},
            saida={"tipo": "alerta", "mensagem": "valor {{ evento.valor }} (n={{ resultado.n }})"},
        )
        cen.publicar("demo.ocorreu", {"valor": 50}, dedup="a")
        cen.publicar("demo.ocorreu", {"valor": 150}, dedup="b")
        resumo = cen.engine.gatilhos.tick(SEG_21H)
        assert resumo["eventos"] == 2 and cen.ctr["leitura"] == 1
        status = sorted(e.status for e in execucoes(cen.engine))
        assert status == ["condicao_falsa", "ok"]
        assert alertas(cen.engine)[0].texto == "valor 150 (n=2)"
        assert contar(cen.engine, EventoRow, EventoRow.processado_em.is_(None)) == 0

    def test_evento_nao_dispara_duas_vezes_apos_reinicio(self, cen):
        cen.gatilho(quando={"evento": "demo.ocorreu"})
        cen.publicar("demo.ocorreu", {}, dedup="a")
        cen.engine.gatilhos.tick(SEG_21H)
        with Session(cen.engine._state.engine) as s:
            s.execute(update(EventoRow).values(processado_em=None))
            s.commit()
        cen.subir().gatilhos.tick(SEG_21H + timedelta(seconds=1))
        assert cen.ctr["leitura"] == 1

    def test_evento_sem_gatilho_e_consumido(self, cen):
        cen.publicar("demo.outro", {}, dedup="a")
        cen.engine.gatilhos.tick(SEG_21H)
        assert contar(cen.engine, EventoRow, EventoRow.processado_em.is_(None)) == 0

    def test_trace_do_evento_liga_a_execucao_do_gatilho(self, cen):
        cen.gatilho(quando={"evento": "demo.ocorreu"})
        set_current_user(LUCAS)
        cen.engine.call_skill("emite", {})
        with Session(cen.engine._state.engine) as s:
            trace = s.scalar(select(EventoRow.trace_id))
        assert trace
        cen.engine.gatilhos.tick(SEG_21H)
        assert execucoes(cen.engine)[0].trace_id == trace

    def test_loop_de_eventos_e_cortado_e_alertado(self, cen):
        cen.gatilho(
            quando={"evento": "demo.ocorreu"},
            acao={"skill": "emite", "argumentos": {}},
            autonomia="A1",
            permissoes=["emite"],
            limites={"max_por_hora": 60, "max_profundidade": 2},
            saida={"tipo": "alerta", "mensagem": "emitiu"},
        )
        cen.publicar("demo.ocorreu", {}, dedup="inicio")
        for i in range(10):
            cen.engine.gatilhos.tick(SEG_21H + timedelta(seconds=i))
        status = [e.status for e in execucoes(cen.engine)]
        assert status == ["ok", "ok", "ok", "cortado"]
        assert cen.ctr["emite"] == 3
        assert any("Encadeamento cortado" in a.texto for a in alertas(cen.engine))
        assert contar(cen.engine, EventoRow, EventoRow.processado_em.is_(None)) == 0

    def test_tempestade_de_eventos_aciona_o_disjuntor(self, cen):
        cen.gatilho(
            quando={"evento": "demo.ocorreu"},
            limites={"max_por_hora": 1, "rejeicoes_por_hora": 3},
        )
        for i in range(8):
            cen.publicar("demo.ocorreu", {}, dedup=f"e{i}")
        cen.engine.gatilhos.tick(SEG_21H)
        status = [e.status for e in execucoes(cen.engine)]
        assert status == ["ok", "limitado", "limitado", "limitado"]
        row = linha_gatilho(cen.engine)
        assert not row.ativo and "disjuntor" in row.motivo_desativacao
        assert cen.ctr["leitura"] == 1
        assert any(a.nivel == "critico" and "desativado" in a.texto for a in alertas(cen.engine))
        assert eventos_audit(cen.engine, "gatilho:desativado")
        cen.publicar("demo.ocorreu", {}, dedup="depois")
        cen.engine.gatilhos.tick(SEG_21H + timedelta(seconds=5))
        assert cen.ctr["leitura"] == 1

    def test_falhas_seguidas_acionam_o_disjuntor(self, cen):
        cen.gatilho(
            quando={"evento": "demo.ocorreu"},
            acao={"skill": "quebra", "argumentos": {}},
            permissoes=["quebra"],
            limites={"falhas_seguidas": 2},
            saida={"tipo": "alerta", "mensagem": "x"},
        )
        for i in range(4):
            cen.publicar("demo.ocorreu", {}, dedup=f"e{i}")
        cen.engine.gatilhos.tick(SEG_21H)
        assert cen.ctr["quebra"] == 2
        assert not linha_gatilho(cen.engine).ativo
        assert "2 falhas seguidas" in linha_gatilho(cen.engine).motivo_desativacao

    def test_reativar_pelo_painel_limpa_o_disjuntor(self, cen):
        cen.gatilho(quando={"evento": "demo.ocorreu"})
        cen.engine.gatilhos.desativar("g1", "teste")
        assert cen.engine.gatilhos.reativar("g1", ROOT.identity)
        assert linha_gatilho(cen.engine).ativo
        assert not cen.engine.gatilhos.reativar("naoexiste", ROOT.identity)


class TestSaidas:
    def rodar(self, cen, **mudancas):
        cen.gatilho(**mudancas)
        cen.engine.gatilhos.tick(ANTES_21H)
        cen.engine.gatilhos.tick(SEG_21H)

    def test_alerta_vai_para_o_banco_e_para_sistema_alertas_md(self, cen):
        self.rodar(
            cen, saida={"tipo": "alerta", "mensagem": "tudo {{ resultado.texto }}", "nivel": "info"}
        )
        a = alertas(cen.engine)[0]
        assert a.texto == "tudo tudo certo" and a.destinatario == "gestor"
        pagina = (cen.engine._vault_root() / "sistema" / "alertas.md").read_text()
        assert "tudo tudo certo" in pagina and "## Alertas de gatilhos" in pagina

    def test_pagina_de_alertas_e_deterministica(self, cen):
        self.rodar(cen)
        cen.engine.caderno.atualizar_paginas()
        antes = (cen.engine._vault_root() / "sistema" / "alertas.md").read_text()
        cen.engine.caderno.atualizar_paginas()
        assert (cen.engine._vault_root() / "sistema" / "alertas.md").read_text() == antes

    def test_painel_registra_quais_alertas_geraram_acao(self, cen):
        self.rodar(cen)
        alerta = alertas(cen.engine)[0]
        assert cen.engine.gatilhos.reconhecer_alerta(alerta.id, ANA.identity)
        assert not cen.engine.gatilhos.reconhecer_alerta(alerta.id, BIA.identity)
        cen.engine.caderno.atualizar_paginas()
        pagina = (cen.engine._vault_root() / "sistema" / "alertas.md").read_text()
        assert f"visto por {ANA.identity}" in pagina

    def test_dlp_bloqueia_conteudo_sensivel_no_alerta(self, cen):
        self.rodar(cen, saida={"tipo": "alerta", "mensagem": "CPF 123.456.789-09 em atraso"})
        assert "bloqueada pelo DLP" in alertas(cen.engine)[0].texto
        pagina = (cen.engine._vault_root() / "sistema" / "alertas.md").read_text()
        assert "123.456.789-09" not in pagina

    def test_nota_e_escrita_pelo_motor_em_relatorios(self, cen):
        self.rodar(cen, saida={"tipo": "nota", "mensagem": "Resumo: n={{ resultado.n }}"})
        arquivos = list((cen.engine._vault_root() / "relatorios" / "gatilhos" / "g1").glob("*.md"))
        assert len(arquivos) == 1 and "Resumo: n=2" in arquivos[0].read_text()

    def test_email_usa_skill_existente_sem_rede(self, cen):
        self.rodar(
            cen,
            saida={"tipo": "email", "mensagem": "n={{ resultado.n }}", "assunto": "Aviso g1"},
            destinatario="gestor@clinica.test",
            permissoes=["leitura", "enviar_email"],
        )
        assert cen.enviados == [
            {"destinatario": "gestor@clinica.test", "assunto": "Aviso g1", "corpo": "n=2"}
        ]

    def test_email_de_skill_a2_vira_proposta_e_nao_envia(self, cen):
        cen.ativar("skills/mail2.md", skill_nota("mail2", "A2", "t.email"))
        self.rodar(
            cen,
            saida={"tipo": "email", "mensagem": "x", "skill": "mail2"},
            destinatario="gestor@clinica.test",
            permissoes=["leitura", "mail2"],
        )
        assert cen.enviados == []
        assert ops_de(cen, "mail2")[0].status == "aguardando_aprovacao"

    def test_modelo_com_variavel_ausente_na_acao_falha_sem_executar(self, cen):
        cen.gatilho(
            quando={"evento": "demo.ocorreu"},
            acao={"skill": "efeito_a1", "argumentos": {"x": "{{ evento.nao_existe }}"}},
            autonomia="A1",
            permissoes=["efeito_a1"],
        )
        cen.publicar("demo.ocorreu", {}, dedup="a")
        cen.engine.gatilhos.tick(SEG_21H)
        assert cen.ctr["efeito_a1"] == 0 and execucoes(cen.engine)[0].status == "falhou"

    def test_argumentos_modelados_chegam_renderizados(self, cen):
        recebidos = []
        cen.engine.handlers.register("t.eco", lambda ctx, args: recebidos.append(args) or {"ok": 1})
        cen.ativar("skills/eco.md", skill_nota("eco", "A0", "t.eco"))
        cen.gatilho(
            acao={
                "skill": "eco",
                "argumentos": {
                    "mes": "{{ mes_atual }}",
                    "ant": "{{ mes_anterior }}",
                    "dia": "{{ hoje }}",
                },
            },
            permissoes=["eco"],
            saida={"tipo": "alerta", "mensagem": "ok"},
        )
        cen.engine.gatilhos.tick(ANTES_21H)
        cen.engine.gatilhos.tick(SEG_21H)
        assert recebidos == [{"mes": "2026-03", "ant": "2026-02", "dia": "2026-03-02"}]


class TestFatosEmGatilhos:
    def test_condicao_usa_fato_de_modulo_e_nucleo(self, cen):
        cen.engine.extensoes.provedores.registrar(
            "demo.estado", lambda ctx, args: {"qtd": cen.ctr["qtd"]}, ["qtd"]
        )
        cen.gatilho(
            fatos_de_modulo=["demo.estado", "nucleo.operacoes"],
            condicao={
                "or": [{">": [{"var": "qtd"}, 0]}, {">": [{"var": "operacoes_aguardando"}, 0]}]
            },
            saida={"tipo": "alerta", "mensagem": "qtd={{ qtd }}"},
        )
        cen.engine.gatilhos.tick(ANTES_21H)
        cen.engine.gatilhos.tick(SEG_21H)
        assert [e.status for e in execucoes(cen.engine)] == ["condicao_falsa"]
        cen.ctr["qtd"] = 3
        cen.engine.gatilhos.tick(SEG_21H + timedelta(days=1))
        assert alertas(cen.engine)[-1].texto == "qtd=3"

    def test_provedor_que_falha_registra_falha_sem_executar(self, cen):
        def quebra(ctx, args):
            raise RuntimeError("x")

        cen.engine.extensoes.provedores.registrar("demo.ruim", quebra, ["qtd"])
        cen.gatilho(fatos_de_modulo=["demo.ruim"], condicao={">": [{"var": "qtd"}, 0]})
        cen.engine.gatilhos.tick(ANTES_21H)
        cen.engine.gatilhos.tick(SEG_21H)
        assert cen.ctr["leitura"] == 0 and execucoes(cen.engine)[0].status == "falhou"


class TestSimulacao:
    def totais(self, eng):
        return (
            contar(eng, GatilhoExecucaoRow),
            contar(eng, EventoRow),
            contar(eng, GatilhoAlertaRow),
            contar(eng, OperacaoRow),
        )

    @pytest.mark.parametrize(
        "skill,nivel,decisao",
        [
            ("leitura", "A0", "executaria"),
            ("efeito_a1", "A1", "executaria"),
            ("efeito_a2", "A2", "criaria_operacao_proposta"),
            ("efeito_a3", "A3", "criaria_operacao_proposta"),
        ],
    )
    def test_simulacao_nao_tem_efeito(self, cen, skill, nivel, decisao):
        cen.gatilho(acao={"skill": skill, "argumentos": {}}, autonomia=nivel, permissoes=[skill])
        antes = self.totais(cen.engine)
        r = cen.engine.gatilhos.simular(config_id="g1")
        assert r["decisao"] == decisao and r["efeito"] == "nenhum"
        assert self.totais(cen.engine) == antes
        assert cen.ctr[skill] == 0 and cen.enviados == []

    def test_simulacao_reporta_condicao_falsa_e_negacao(self, cen):
        cen.gatilho(condicao={"==": [{"var": "hora"}, 99]})
        assert cen.engine.gatilhos.simular(config_id="g1")["decisao"] == "nao_dispararia"
        cen.engine.user_store.set_permission(Role.VIEWER, "leitura", False)
        cen.gatilho(id="g2")
        assert cen.engine.gatilhos.simular(config_id="g2")["decisao"] == "negado"

    def test_simulacao_de_gatilho_por_evento_aceita_exemplo(self, cen):
        cen.gatilho(
            quando={"evento": "demo.ocorreu"},
            condicao={">": [{"var": "evento.valor"}, 10]},
        )
        assert cen.engine.gatilhos.simular(config_id="g1", evento={"valor": 5})["condicao"] is False
        assert cen.engine.gatilhos.simular(config_id="g1", evento={"valor": 50})["condicao"] is True

    def test_gatilho_desconhecido(self, cen):
        assert cen.engine.gatilhos.simular(config_id="nada")["allowed"] is False


class TestPainelEApi:
    @pytest.fixture
    def http(self, cen):
        cen.gatilho()
        cen.engine.user_store.create_user(ROOT.identity, Role.SUPER_ADMIN, "tok-root")
        cen.engine.user_store.create_user(ANA.identity, Role.ADMIN, "tok-ana")
        return TestClient(create_app(cen.engine)), cen

    def test_somente_super_admin(self, http):
        client, _ = http
        for caminho in ("/api/gatilhos",):
            assert (
                client.get(caminho, headers={"Authorization": "Bearer tok-ana"}).status_code == 403
            )
            assert client.get(caminho).status_code == 401
        r = client.post(
            "/api/gatilhos/pausa",
            json={"pausado": True},
            headers={"Authorization": "Bearer tok-ana"},
        )
        assert r.status_code == 403

    def test_lista_ativos_execucoes_e_falhas(self, http):
        client, cen = http
        cen.engine.gatilhos.tick(ANTES_21H)
        cen.engine.gatilhos.tick(SEG_21H)
        r = client.get("/api/gatilhos", headers={"Authorization": "Bearer tok-root"})
        corpo = r.json()
        assert corpo["habilitado"] and not corpo["pausado"]
        g = corpo["gatilhos"][0]
        assert g["id"] == "g1" and g["ativo"] and g["ultimo_status"] == "ok"
        assert g["aprovado_por"] == BIA.identity and g["destinatario"] == "gestor"
        assert corpo["ultimas_execucoes"][0]["status"] == "ok" and corpo["falhas"] == []
        assert corpo["alertas"][0]["texto"] == "n=2"

    def test_pausa_reativacao_simulacao_e_reconhecimento(self, http):
        client, cen = http
        cab = {"Authorization": "Bearer tok-root"}
        assert client.post("/api/gatilhos/pausa", json={"pausado": True}, headers=cab).json() == {
            "pausado": True
        }
        assert (
            client.post("/api/gatilhos/pausa", json={"pausado": False}, headers=cab).status_code
            == 200
        )
        cen.engine.gatilhos.desativar("g1", "teste")
        assert client.post("/api/gatilhos/g1/reativar", headers=cab).status_code == 200
        assert client.post("/api/gatilhos/nada/reativar", headers=cab).status_code == 404
        sim = client.post("/api/gatilhos/g1/simular", json={}, headers=cab)
        assert sim.status_code == 200 and sim.json()["decisao"] == "executaria"
        assert client.post("/api/gatilhos/nada/simular", json={}, headers=cab).status_code == 404
        alerta_id = alertas(cen.engine)[0].id
        assert (
            client.post(f"/api/gatilhos/alertas/{alerta_id}/reconhecer", headers=cab).status_code
            == 200
        )
        assert (
            client.post(f"/api/gatilhos/alertas/{alerta_id}/reconhecer", headers=cab).status_code
            == 404
        )

    def test_reprocessar_janela_registra_nova_execucao(self, http):
        client, cen = http
        cab = {"Authorization": "Bearer tok-root"}
        cen.engine.gatilhos.tick(ANTES_21H)
        cen.engine.gatilhos.tick(SEG_21H)
        janela = execucoes(cen.engine)[0].janela
        r = client.post("/api/gatilhos/g1/reprocessar", json={"janela": janela}, headers=cab)
        assert r.status_code == 200 and cen.ctr["leitura"] == 2
        assert (
            client.post(
                "/api/gatilhos/g1/reprocessar", json={"janela": "x"}, headers=cab
            ).status_code
            == 404
        )

    def test_startup_sobe_o_agendador_e_desliga_por_variavel(self, http, monkeypatch):
        _, cen = http
        with TestClient(create_app(cen.engine)):
            import time

            time.sleep(1.3)
        assert cen.engine.gatilhos._controle("agendador_batimento")

    def test_startup_respeita_brm_gatilhos_off(self, cen, monkeypatch):
        monkeypatch.setenv("BRM_GATILHOS", "off")
        cen.gatilho()
        with TestClient(create_app(cen.engine)):
            import time

            time.sleep(1.3)
        assert cen.engine.gatilhos._controle("agendador_batimento") is None


class TestScript:
    def test_uma_vez_executa_um_passo(self, cen, monkeypatch, capsys):
        from scripts import rodar_gatilhos

        cen.gatilho()
        cen.engine.gatilhos.tick(ANTES_21H)
        monkeypatch.setattr(rodar_gatilhos, "criar_motor", lambda args: cen.engine)
        cen.relogio.agora = SEG_21H
        assert rodar_gatilhos.principal(["example", "--uma-vez"]) == 0
        assert cen.ctr["leitura"] == 1
        assert "agendas" in capsys.readouterr().out

    def test_pausar_retomar_e_listar(self, cen, monkeypatch, capsys):
        from scripts import rodar_gatilhos

        cen.gatilho()
        monkeypatch.setattr(rodar_gatilhos, "criar_motor", lambda args: cen.engine)
        assert rodar_gatilhos.principal(["example", "--pausar"]) == 0
        assert cen.engine.gatilhos.pausado()
        assert rodar_gatilhos.principal(["example", "--retomar"]) == 0
        assert not cen.engine.gatilhos.pausado()
        assert rodar_gatilhos.principal(["example", "--listar"]) == 0
        assert "g1" in capsys.readouterr().out

    def test_simular_pelo_script(self, cen, monkeypatch, capsys):
        from scripts import rodar_gatilhos

        cen.gatilho()
        monkeypatch.setattr(rodar_gatilhos, "criar_motor", lambda args: cen.engine)
        assert rodar_gatilhos.principal(["example", "--simular", "g1"]) == 0
        assert "executaria" in capsys.readouterr().out
        assert cen.ctr["leitura"] == 0
