import json
import re
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from mcp_engine.core.auth import Role, UserProfile, current_user_var, set_current_user
from mcp_engine.core.executor import ExecucaoFalhou, HandlerContext, HandlerRegistry, SecretStore
from mcp_engine.modules import (
    ModuleError,
    ModuleHost,
    NamespacedHandlers,
    _migrate,
    load_for_client,
)
from mcp_engine.modules import financeiro as fin
from mcp_engine.modules.agenda import MODULE, executar_sincronizacao
from mcp_engine.modules.agenda.fake import FakeAgendaProvider
from mcp_engine.modules.agenda.provider import (
    AgendaErro,
    AgendaIndisponivel,
    Agendamento,
    Cliente,
    Servico,
)
from mcp_engine.modules.agenda.sincronizacao import (
    CHAVE_CURSOR,
    MARGEM_CURSOR,
    ler_estado,
    sincronizar,
)
from mcp_engine.modules.financeiro import handlers as h
from mcp_engine.modules.financeiro import params, sessoes
from mcp_engine.modules.financeiro.db import abrir, caminho_ledger, preparar_banco, transacao
from mcp_engine.modules.financeiro.lancamentos import Quem
from scripts.novo_cliente import criar_cliente, validar
from tests.financeiro_util import recebimento

CLIENTE = "clinica-agenda"
AGORA = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)
GESTOR_CTX = dict(role="admin", actor="gestor@clinica.test")
SEC_CTX = dict(role="viewer", actor="sec@clinica.test")
RAIZ = Path(__file__).resolve().parent.parent


def ag(
    id="a1",
    cust="c1",
    nome="Maria Silva",
    fone="11999990001",
    inicio=datetime(2026, 10, 9, 13, 0, tzinfo=UTC),
    status="CONFIRMED",
    atualizado=datetime(2026, 10, 8, 10, 0, tzinfo=UTC),
    preco=15000,
    staff="Ana Souza",
    servico="Sessão individual",
):
    return Agendamento(
        id=id,
        customer_id=cust,
        customer_name=nome,
        customer_phone=fone,
        staff_id="st1" if staff else None,
        staff_name=staff,
        service_id="sv1",
        service_name=servico,
        preco_centavos=preco,
        duracao_min=50,
        inicio_utc=inicio,
        status=status,
        atualizado_em=atualizado,
        origem="web",
    )


class Amb:
    def __init__(self, raiz: Path) -> None:
        self.raiz = raiz
        self.ledger = preparar_banco(raiz, CLIENTE)
        self.fake = FakeAgendaProvider()
        self.auditoria: list[tuple[str, bool]] = []
        self.registry = HandlerRegistry()
        self.host = self.novo_host(self.registry)
        _migrate(self.host, MODULE.migrations)
        MODULE.install(self.host)

    def novo_host(self, registry) -> ModuleHost:
        return ModuleHost(
            module="agenda",
            client_id=CLIENTE,
            data_root=self.raiz,
            handlers=NamespacedHandlers("agenda", registry),
            secrets=SecretStore(self.raiz / "segredos"),
            add_vault_note=lambda *a, **k: None,
            audit=lambda nome, ok: self.auditoria.append((nome, ok)),
            modulos={"financeiro": fin.MODULE, "agenda": MODULE},
        )

    def sync(self, agora=AGORA):
        return sincronizar(self.host, self.fake, agora)

    def ctx(self, **papel) -> HandlerContext:
        return HandlerContext(
            client_id=CLIENTE,
            actor=papel["actor"],
            role=papel["role"],
            trace_id="t",
            data_root=self.raiz,
        )

    def chamar(self, nome, args=None, **papel):
        return self.registry.get(f"agenda.{nome}")(self.ctx(**(papel or GESTOR_CTX)), args or {})

    def sql(self, consulta, *p):
        con = abrir(self.ledger)
        try:
            return [dict(r) for r in con.execute(consulta, p)]
        finally:
            con.close()

    def sql_estado(self, consulta, *p):
        con = self.host.connect()
        try:
            return con.execute(consulta, p).fetchall()
        finally:
            con.close()


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.setattr(MODULE, "provider_factory", None, raising=False)
    token = current_user_var.set(None)
    yield
    current_user_var.reset(token)


@pytest.fixture
def amb(tmp_path, monkeypatch):
    a = Amb(tmp_path)
    monkeypatch.setattr(MODULE, "provider_factory", lambda host: a.fake)
    monkeypatch.setattr(h, "hoje", lambda: date(2026, 10, 9))
    return a


def test_sincroniza_cria_paciente_e_sessao_e_nao_duplica(amb):
    amb.fake.semear(ag())
    r1 = amb.sync()
    assert r1["processados"] == 1
    pac = amb.sql("SELECT nome, external_ref, status FROM paciente_ref")
    assert pac == [{"nome": "Maria Silva", "external_ref": "c1", "status": "ativo"}]
    sess = amb.sql("SELECT * FROM sessao")
    assert len(sess) == 1 and sess[0]["agenda_id"] == "a1"
    assert sess[0]["inicio_utc"] == "2026-10-09T13:00:00Z"
    assert sess[0]["valor_previsto_centavos"] == 15000
    assert sess[0]["valor_previsto_origem"] == "servico"
    r2 = amb.sync()
    assert r2["processados"] == 0 and r2["ignorados"] == 1
    assert len(amb.sql("SELECT * FROM sessao")) == 1
    assert len(amb.sql("SELECT * FROM paciente_ref")) == 1


def test_status_da_agenda_viram_status_da_sessao(amb):
    amb.fake.semear(
        ag("a1", status="COMPLETED"),
        ag("a2", status="CANCELLED"),
        ag("a3", status="IN_PROGRESS"),
        ag("a4", status="STATUS_NOVO"),
    )
    amb.sync()
    por = {r["agenda_id"]: r["status_agenda"] for r in amb.sql("SELECT * FROM sessao")}
    assert por == {
        "a1": "COMPLETED",
        "a2": "CANCELLED",
        "a3": "SCHEDULED",
        "a4": "SCHEDULED",
    }


def test_remarcacao_e_troca_de_nome_atualizam_sem_duplicar(amb):
    amb.fake.semear(ag(inicio=datetime(2026, 10, 12, 13, 0, tzinfo=UTC)))
    amb.sync()
    amb.fake.semear(
        ag(
            nome="Maria Silva Souza",
            fone="11888880000",
            inicio=datetime(2026, 10, 14, 17, 0, tzinfo=UTC),
            atualizado=datetime(2026, 10, 9, 9, 0, tzinfo=UTC),
        )
    )
    r = amb.sync()
    assert r["processados"] == 1
    s = amb.sql("SELECT inicio_utc FROM sessao")
    assert s == [{"inicio_utc": "2026-10-14T17:00:00Z"}]
    assert amb.sql("SELECT nome FROM paciente_ref") == [{"nome": "Maria Silva Souza"}]
    assert amb.sql("SELECT * FROM pendencia_operacional") == []


def test_cursor_retoma_apos_reinicio_sem_perder_nem_repetir(amb):
    amb.fake.semear(ag("a1", atualizado=datetime(2026, 10, 8, 10, 0, tzinfo=UTC)))
    amb.sync()
    cursor = ler_estado(amb.host, CHAVE_CURSOR)
    assert cursor == "2026-10-08T10:00:00Z"

    reiniciado = Amb.__new__(Amb)
    reiniciado.__dict__.update(amb.__dict__)
    reiniciado.host = amb.novo_host(HandlerRegistry())
    reiniciado.fake = FakeAgendaProvider(amb.fake.agendamentos.values())
    reiniciado.fake.semear(ag("a2", cust="c2", nome="Joao Lima", atualizado=AGORA))
    r = reiniciado.sync()
    assert (
        reiniciado.fake.consultas[-1]["desde"]
        == datetime(2026, 10, 8, 10, 0, tzinfo=UTC) - MARGEM_CURSOR
    )
    assert r["lidos"] == 2 and r["processados"] == 1 and r["ignorados"] == 1
    assert {s["agenda_id"] for s in amb.sql("SELECT agenda_id FROM sessao")} == {"a1", "a2"}
    assert ler_estado(reiniciado.host, CHAVE_CURSOR) == "2026-10-09T15:00:00Z"


def test_falha_da_agenda_nao_avanca_cursor_nem_grava(amb):
    amb.fake.semear(ag())
    amb.fake.erro = AgendaIndisponivel("A Agenda está indisponível no momento.")
    res = executar_sincronizacao(amb.host, amb.fake)
    assert res == {"ok": False, "erro": "A Agenda está indisponível no momento."}
    assert ler_estado(amb.host, CHAVE_CURSOR) is None
    assert amb.sql("SELECT * FROM sessao") == []
    assert amb.auditoria == [("agenda.sincronizar", False)]
    amb.fake.erro = None
    assert executar_sincronizacao(amb.host, amb.fake)["ok"] is True
    assert amb.auditoria[-1] == ("agenda.sincronizar", True)


def test_falha_no_ledger_desfaz_tudo_inclusive_o_estado(amb):
    amb.fake.semear(ag("a1"), ag("a2", cust="c2", nome="???"))
    res = executar_sincronizacao(amb.host, amb.fake)
    assert res["ok"] is False and "sem nome" in res["erro"]
    assert amb.sql("SELECT * FROM sessao") == []
    assert amb.sql_estado("SELECT * FROM agendamento_visto") == []
    assert ler_estado(amb.host, CHAVE_CURSOR) is None


def test_cliente_desconhecido_em_lancamento_vira_pendencia_e_nao_cria_paciente(amb):
    amb.fake.semear(ag())
    amb.sync()
    ctx = amb.ctx(**GESTOR_CTX)
    r = h.registrar_recebimento(ctx, recebimento(paciente="Fulana Inexistente"))
    assert r["registrado"] is False and r["pendencia_id"]
    tipos = {p["tipo"] for p in amb.sql("SELECT tipo FROM pendencia_operacional")}
    assert "paciente_desconhecido" in tipos
    assert [p["nome"] for p in amb.sql("SELECT nome FROM paciente_ref")] == ["Maria Silva"]


def test_sessao_sem_desfecho_aparece_e_some_quando_concluida(amb):
    amb.fake.semear(
        ag("passada", inicio=datetime(2026, 10, 9, 10, 0, tzinfo=UTC)),
        ag("futura", cust="c2", nome="Joao Lima", inicio=datetime(2026, 10, 9, 20, 0, tzinfo=UTC)),
    )
    r = amb.sync()
    assert r["sem_desfecho_novas"] == 1
    abertas = amb.sql("SELECT * FROM pendencia_operacional WHERE status = 'aberta'")
    assert [p["tipo"] for p in abertas] == ["sessao_sem_desfecho"]
    assert "Agenda" in abertas[0]["motivo"]
    assert amb.sync()["sem_desfecho_novas"] == 0

    amb.fake.semear(
        ag(
            "passada",
            status="COMPLETED",
            inicio=datetime(2026, 10, 9, 10, 0, tzinfo=UTC),
            atualizado=datetime(2026, 10, 9, 14, 0, tzinfo=UTC),
        )
    )
    r = amb.sync()
    assert r["sem_desfecho_encerradas"] == 1
    assert amb.sql("SELECT status FROM pendencia_operacional") == [{"status": "resolvida"}]


def test_sessao_sem_desfecho_some_quando_a_falta_e_registrada(amb):
    amb.fake.semear(ag(inicio=datetime(2026, 10, 9, 10, 0, tzinfo=UTC)))
    amb.sync()
    con = abrir(amb.ledger)
    with transacao(con):
        sid = con.execute("SELECT id FROM sessao").fetchone()[0]
        sessoes.registrar_falta(con, sid, "falta", "não veio", Quem("sec", "viewer"))
    con.close()
    amb.fake.semear(ag(inicio=datetime(2026, 10, 9, 10, 0, tzinfo=UTC), atualizado=AGORA))
    assert amb.sync()["sem_desfecho_encerradas"] == 1


def test_conflito_agenda_concluida_x_falta_no_ledger(amb):
    inicio = datetime(2026, 10, 8, 13, 0, tzinfo=UTC)
    amb.fake.semear(ag(inicio=inicio))
    amb.sync()
    con = abrir(amb.ledger)
    with transacao(con):
        sid = con.execute("SELECT id FROM sessao").fetchone()[0]
        sessoes.registrar_falta(con, sid, "falta", "não veio", Quem("sec", "viewer"))
    con.close()
    amb.fake.semear(ag(inicio=inicio, status="COMPLETED", atualizado=AGORA))
    amb.sync()
    conflitos = amb.sql("SELECT status FROM pendencia_operacional WHERE tipo = 'conflito_desfecho'")
    assert conflitos == [{"status": "aberta"}]


def test_possivel_duplicidade_de_cliente_vira_pendencia_sem_fusao(amb):
    amb.fake.semear(
        ag("a1", cust="c1", nome="Maria  da Silva", fone="(11) 99999-0001"),
        ag("a2", cust="c2", nome="MARIA DA SILVA", fone="11 98888-7777"),
        ag("a3", cust="c3", nome="Joao Lima", fone="11977776666"),
    )
    r = amb.sync()
    assert r["duplicidades"] == 1
    pend = amb.sql(
        "SELECT tipo, referencia, detalhe FROM pendencia_operacional"
        " WHERE tipo = 'possivel_duplicidade_cliente'"
    )
    assert [p["tipo"] for p in pend] == ["possivel_duplicidade_cliente"]
    assert pend[0]["referencia"] == "clientes:c1:c2"
    assert "99999" not in pend[0]["detalhe"]
    assert len(amb.sql("SELECT * FROM paciente_ref")) == 3
    amb.sync()
    assert len(amb.sql("SELECT * FROM pendencia_operacional WHERE tipo LIKE 'possivel%'")) == 1


def test_mesmo_cliente_com_telefone_alterado_nao_e_duplicidade(amb):
    amb.fake.semear(ag("a1", fone="11999990001"))
    amb.sync()
    amb.fake.semear(ag("a1", fone="11888880002", atualizado=AGORA))
    assert amb.sync()["duplicidades"] == 0
    assert amb.sql("SELECT * FROM pendencia_operacional WHERE tipo LIKE 'possivel%'") == []


def test_precedencia_do_valor_previsto_paciente_antes_do_servico(amb):
    con = abrir(amb.ledger)
    with transacao(con):
        params.garantir_paciente(
            con, "Maria Silva", valor_sessao_centavos=20000, status="a_confirmar"
        )
    con.close()
    amb.fake.semear(ag(preco=15000))
    amb.sync()
    s = amb.sql("SELECT valor_previsto_centavos v, valor_previsto_origem o FROM sessao")
    assert s == [{"v": 20000, "o": "paciente"}]
    p = amb.sql("SELECT external_ref, status FROM paciente_ref")
    assert p == [{"external_ref": "c1", "status": "ativo"}]


def test_cobranca_por_sessao_e_gerada_quando_a_sessao_do_pacote_e_concluida(amb):
    amb.fake.semear(ag(inicio=datetime(2026, 10, 8, 13, 0, tzinfo=UTC)))
    amb.sync()
    path = amb.ledger
    params.materializar_modelo_pacote(
        path,
        {
            "nome": "Pacote 4",
            "modo_cobranca": "por_sessao",
            "n_sessoes": 4,
            "preco_total": "400",
            "vigente_de": "2026-01-01",
        },
        criado_por="t",
    )
    con = abrir(path)
    with transacao(con):
        pid = con.execute("SELECT id FROM paciente_ref").fetchone()[0]
        pacote = sessoes.registrar_pacote(
            con,
            paciente_id=pid,
            modelo_nome="Pacote 4",
            inicio=date(2026, 10, 1),
            quem=Quem("g", "admin"),
        )["pacote_id"]
        con.execute("UPDATE sessao SET pacote_id = ?", (pacote,))
    con.close()
    amb.fake.semear(
        ag(inicio=datetime(2026, 10, 8, 13, 0, tzinfo=UTC), status="COMPLETED", atualizado=AGORA)
    )
    assert amb.sync()["cobrancas_geradas"] == 1
    assert amb.sync()["cobrancas_geradas"] == 0
    s = amb.sql("SELECT valor_previsto_centavos v, valor_previsto_origem o FROM sessao")
    assert s == [{"v": 10000, "o": "pacote"}]


def test_orcamento_um_dia_de_agenda_em_menos_de_2s(amb):
    dia = datetime(2026, 10, 9, 11, 0, tzinfo=UTC)
    amb.fake.semear(
        *(
            ag(
                f"a{i}",
                cust=f"c{i % 40}",
                nome=f"Paciente Numero {i % 40}",
                fone=f"119{i % 40:08d}",
                inicio=dia + timedelta(minutes=15 * i),
                status="COMPLETED" if i % 2 else "CONFIRMED",
                atualizado=AGORA - timedelta(minutes=i),
            )
            for i in range(80)
        )
    )
    t0 = time.perf_counter()
    r = amb.sync()
    assert time.perf_counter() - t0 < 2.0
    assert r["processados"] == 80


def test_sincronizacao_nao_abre_o_banco_do_financeiro_nem_importa_seus_internos():
    fonte = "\n".join(p.read_text() for p in (RAIZ / "mcp_engine/modules/agenda").glob("*.py"))
    assert "caminho_ledger" not in fonte and "financeiro.sqlite" not in fonte
    assert not re.search(r"from mcp_engine\.modules\.financeiro[.\s]", fonte)
    assert "import sqlite3" in fonte and "sqlite3.connect" not in fonte


def test_modulo_exige_financeiro_e_api_de_modulo_nao_habilitado_falha(tmp_path):
    a = Amb(tmp_path)
    sem = a.novo_host(HandlerRegistry())
    sem.modulos = {"agenda": MODULE}
    with pytest.raises(ModuleError, match="não está habilitado"):
        sem.module_api("financeiro")
    with pytest.raises(ModuleError, match="exige o módulo 'financeiro'"):
        MODULE.install(sem)


def test_leitura_sessoes_gestor_ve_nomes_e_viewer_so_agregados(amb):
    amb.fake.semear(
        ag("a1", inicio=datetime(2026, 10, 9, 13, 0, tzinfo=UTC), status="COMPLETED"),
        ag("a2", cust="c2", nome="Joao Lima", inicio=datetime(2026, 10, 9, 14, 0, tzinfo=UTC)),
        ag("a3", cust="c3", nome="Zeca Tatu", inicio=datetime(2026, 10, 10, 14, 0, tzinfo=UTC)),
    )
    g = amb.chamar("sessoes", {"de": "2026-10-09"})
    assert g["total"] == 2 and {s["cliente"] for s in g["sessoes"]} == {"Maria Silva", "Joao Lima"}
    assert g["sessoes"][0]["hora"] == "10:00" and g["sessoes"][0]["valor"] == "R$ 150,00"
    v = amb.chamar("sessoes", {"de": "2026-10-09"}, **SEC_CTX)
    texto = json.dumps(v, ensure_ascii=False)
    assert v["agregado"] is True and v["total"] == 2
    assert "Maria" not in texto and "Joao" not in texto
    assert v["por_status"] == {"COMPLETED": 1, "CONFIRMED": 1}


def test_leitura_sessoes_respeita_virada_do_dia_no_fuso_do_estabelecimento(amb):
    amb.fake.semear(
        ag("tarde", inicio=datetime(2026, 10, 10, 2, 30, tzinfo=UTC)),
        ag("meia", cust="c2", nome="Joao Lima", inicio=datetime(2026, 10, 10, 3, 0, tzinfo=UTC)),
    )
    dia9 = amb.chamar("sessoes", {"de": "2026-10-09", "ate": "2026-10-09"})
    assert [s["agenda_id"] for s in dia9["sessoes"]] == ["tarde"]
    assert dia9["sessoes"][0]["hora"] == "23:30"
    dia10 = amb.chamar("sessoes", {"de": "2026-10-10", "ate": "2026-10-10"})
    assert [s["agenda_id"] for s in dia10["sessoes"]] == ["meia"]
    assert dia10["sessoes"][0]["hora"] == "00:00"


@pytest.mark.parametrize(
    "args",
    [
        {"de": "ontem"},
        {"de": "2026-10-09", "ate": "2026-10-01"},
        {"de": "2026-01-01", "ate": "2026-12-31"},
        {"status": "HACK"},
        {"profissional": "x'; drop"},
    ],
)
def test_leitura_sessoes_rejeita_parametros_ruins(amb, args):
    with pytest.raises(ExecucaoFalhou):
        amb.chamar("sessoes", args)


def test_leitura_clientes_e_servicos(amb):
    amb.fake.clientes = [Cliente("c1", "Maria Silva", "11999990001")]
    amb.fake.servicos = [Servico("sv1", "Sessão", 15000, 50, True)]
    g = amb.chamar("clientes", {"busca": "mar"})
    assert g["clientes"] == [{"id": "c1", "nome": "Maria Silva", "telefone": "****0001"}]
    v = amb.chamar("clientes", {"busca": "mar"}, **SEC_CTX)
    assert v == {"total": 1, "agregado": True}
    assert amb.chamar("servicos", **SEC_CTX)["servicos"][0]["valor"] == "R$ 150,00"
    with pytest.raises(ExecucaoFalhou):
        amb.chamar("clientes", {"busca": "x" * 81})


def test_agenda_fora_do_ar_da_erro_claro_e_o_financeiro_segue(amb):
    amb.fake.semear(ag(status="COMPLETED"))
    amb.sync()
    amb.fake.erro = AgendaIndisponivel("A Agenda está indisponível no momento.")
    for nome in ("sessoes", "clientes", "servicos"):
        with pytest.raises(AgendaIndisponivel, match="indisponível"):
            amb.chamar(nome)
    res = amb.chamar("sessoes_sem_pagamento", {"de": "2026-10-01"})
    assert res["total"] == 1
    con = abrir(amb.ledger)
    assert con.execute("SELECT COUNT(*) FROM sessao").fetchone()[0] == 1
    con.close()


def test_sessoes_sem_pagamento_some_quando_ha_recebimento(amb):
    semear_meio(amb)
    amb.fake.semear(
        ag("a1", status="COMPLETED", inicio=datetime(2026, 10, 7, 13, 0, tzinfo=UTC)),
        ag(
            "a2",
            cust="c2",
            nome="Joao Lima",
            status="COMPLETED",
            inicio=datetime(2026, 10, 7, 14, 0, tzinfo=UTC),
        ),
        ag(
            "a3",
            cust="c3",
            nome="Ana Reis",
            status="CONFIRMED",
            inicio=datetime(2026, 10, 7, 15, 0, tzinfo=UTC),
        ),
    )
    amb.sync()
    g = amb.chamar("sessoes_sem_pagamento", {"de": "2026-10-01", "ate": "2026-10-09"})
    assert g["total"] == 2 and g["valor_previsto"] == "R$ 300,00"
    assert g["ultima_sincronizacao"] == "2026-10-09T15:00:00Z"
    r = h.registrar_recebimento(
        amb.ctx(**GESTOR_CTX),
        recebimento(
            data_pagamento="2026-10-08",
            paciente="Maria Silva",
            datas_sessoes=["2026-10-07"],
        ),
    )
    assert r["registrado"] is True, r
    g = amb.chamar("sessoes_sem_pagamento", {"de": "2026-10-01", "ate": "2026-10-09"})
    assert [s["paciente"] for s in g["sessoes"]] == ["Joao Lima"]
    v = amb.chamar("sessoes_sem_pagamento", {"de": "2026-10-01"}, **SEC_CTX)
    assert v["agregado"] is True and "Joao" not in json.dumps(v)


def semear_meio(amb):
    params.materializar_meio(
        amb.ledger, {"nome": "Sicoob", "tipo": "banco", "vigente_de": "2025-01-01"}, criado_por="t"
    )


def test_skill_sincronizar_agenda_falha_com_mensagem_limpa(amb):
    amb.fake.erro = AgendaIndisponivel("A Agenda está indisponível no momento.")
    with pytest.raises(AgendaErro, match="indisponível"):
        amb.chamar("sincronizar_agenda")
    amb.fake.erro = None
    amb.fake.semear(ag())
    assert amb.chamar("sincronizar_agenda")["processados"] == 1


def test_sem_segredos_o_erro_e_claro_e_sem_vazar(tmp_path, monkeypatch):
    monkeypatch.delenv("BRM_SECRET_AGENDA_API_KEY", raising=False)
    a = Amb(tmp_path)
    monkeypatch.setattr(MODULE, "provider_factory", None)
    with pytest.raises(ExecucaoFalhou, match="não está configurada"):
        a.chamar("servicos")
    assert executar_sincronizacao(a.host)["ok"] is False


def test_chave_e_url_vindas_dos_segredos_nao_aparecem_em_auditoria(tmp_path, monkeypatch):
    monkeypatch.setenv(
        f"BRM_SECRET_{CLIENTE.upper().replace('-', '_')}_AGENDA_API_KEY", "SEGREDO-ZZ"
    )
    monkeypatch.setenv("BRM_SECRET_AGENDA_BASE_URL", "http://inseguro.example")
    a = Amb(tmp_path)
    a.host.secrets = SecretStore(tmp_path / "s").for_client(CLIENTE)
    monkeypatch.setattr(MODULE, "provider_factory", None)
    res = executar_sincronizacao(a.host)
    assert res["ok"] is False
    blob = json.dumps(res) + json.dumps(a.auditoria)
    assert "SEGREDO-ZZ" not in blob and "inseguro" not in blob


def test_manifesto_valida_dominio_na_subida_e_cliente_gerado_carrega(tmp_path):
    with pytest.raises(ModuleError, match="fora da allowlist"):
        load_for_client(CLIENTE, tmp_path, ["financeiro", "agenda"], [], ["example.invalid"])
    destino = criar_cliente("cli-agenda", ["financeiro", "agenda"], tmp_path / "clients")
    habilitadas = (destino / "config" / "skills.yaml").read_text()
    assert "agenda_sessoes_sem_pagamento" in habilitadas
    assert "agenda.brmsolutions.com.br" in (destino / "config" / "client_config.yaml").read_text()


def test_clinica_piloto_habilita_agenda_e_adota_as_skills():
    validar("clinica-piloto", RAIZ / "clients")


def test_nota_modelo_de_gatilho_existe_e_aponta_para_skill_do_modulo():
    nota = (RAIZ / "mcp_engine/modules/agenda/defaults/gatilho-sincronizar-agenda.md").read_text()
    assert "tipo: gatilho" in nota and "skill: sincronizar_agenda" in nota
    assert "sincronizar_agenda" in MODULE.skills_defs


def test_motor_expoe_leitura_agregada_por_perfil_e_audita(tmp_path, monkeypatch):
    from mcp_engine.core.mcp_server import BRMEngine

    monkeypatch.setattr(h, "hoje", lambda: date(2026, 10, 9))
    cfg = tmp_path / "clients"
    criar_cliente("cli-motor", ["financeiro", "agenda"], cfg)
    fake = FakeAgendaProvider([ag(inicio=datetime(2026, 10, 9, 13, 0, tzinfo=UTC))])
    monkeypatch.setattr(MODULE, "provider_factory", lambda host: fake)
    engine = BRMEngine(client_id="cli-motor", config_root=cfg, data_root=tmp_path / "data")
    assert {"agenda_sessoes", "sincronizar_agenda"} <= {s.name for s in engine.skills}
    set_current_user(UserProfile(identity="g@x.test", role=Role.ADMIN))
    r = engine.call_skill("agenda_sessoes", {"de": "2026-10-09"})
    assert r["allowed"] is True and "Maria Silva" in r["result"]
    set_current_user(UserProfile(identity="s@x.test", role=Role.VIEWER))
    r = engine.call_skill("agenda_sessoes", {"de": "2026-10-09"})
    assert r["allowed"] is True and "Maria" not in r["result"]
    set_current_user(UserProfile(identity="g@x.test", role=Role.ADMIN))
    r = engine.call_skill("sincronizar_agenda", {})
    assert r["allowed"] is True, r
    con = abrir(caminho_ledger(tmp_path / "data", "cli-motor"))
    assert con.execute("SELECT COUNT(*) FROM sessao").fetchone()[0] == 1
    con.close()


def test_comando_manual_sincroniza_com_provider_falso_e_falha_limpo_sem_segredos(
    tmp_path, monkeypatch, capsys
):
    from scripts import sincronizar_agenda as cmd

    for var in ("BRM_SECRET_AGENDA_API_KEY", "BRM_SECRET_AGENDA_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("BRM_SECRETS_DIR", str(tmp_path / "vazio"))
    cfg = tmp_path / "clients"
    criar_cliente("cli-cmd", ["financeiro", "agenda"], cfg)
    argv = ["--cliente", "cli-cmd", "--config-root", str(cfg), "--data-root", str(tmp_path / "d")]
    assert cmd.main(argv) == 1
    assert "não está configurada" in capsys.readouterr().out

    fake = FakeAgendaProvider([ag()])
    monkeypatch.setattr(MODULE, "provider_factory", lambda host: fake)
    assert cmd.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["processados"] == 1
    assert cmd.main(["--cliente", "cli-inexistente", "--config-root", str(cfg)]) == 1
