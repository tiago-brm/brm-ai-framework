"""Contrato de sessão no módulo agenda (spec 035, fases 3 e 4), com a Agenda falsa."""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from mcp_engine.core.acoes import AcaoErro
from mcp_engine.core.auth import Role, UserProfile, current_user_var, set_current_user
from mcp_engine.core.executor import ExecucaoFalhou
from mcp_engine.core.identidade import Identidade
from mcp_engine.core.mcp_server import BRMEngine
from mcp_engine.modules.agenda import MODULE
from mcp_engine.modules.agenda.contrato import MODELO_CONTRATO, montar_fontes, referencia_cliente
from mcp_engine.modules.agenda.fake import FakeAgendaProvider
from mcp_engine.modules.agenda.provider import (
    AgendaRecursoIndisponivel,
    Cliente,
    Endereco,
    FichaCliente,
    HttpAgendaCadastroProvider,
    HttpAgendaProvider,
    Profissional,
    Responsavel,
    StatusSessao,
)
from tests.test_agenda import ag

RAIZ = Path(__file__).resolve().parent.parent
PIN = "2580"
SEC = Identidade(
    id="sec@cli.test",
    perfil=UserProfile(identity="sec@cli.test", role=Role.VIEWER),
    provedor="local",
    metodo="token",
)
GESTOR_PERFIL = UserProfile(identity="gestor@cli.test", role=Role.ADMIN)
ENDERECO = Endereco("01001000", "Rua A", "10", None, "Centro", "São Paulo", "SP")


def ficha(cpf="52998224725", nascimento=date(1990, 5, 1), endereco=ENDERECO, resp=()):
    return FichaCliente(
        "c1", "Maria Silva", "11999990001", "m@x.test", nascimento, cpf, endereco, tuple(resp)
    )


@pytest.fixture(autouse=True)
def _usuario():
    token = current_user_var.set(None)
    yield
    current_user_var.reset(token)


@pytest.fixture
def fake(monkeypatch) -> FakeAgendaProvider:
    f = FakeAgendaProvider([ag("a1", status="COMPLETED")])
    f.fichas["c1"] = ficha()
    monkeypatch.setattr(MODULE, "provider_factory", lambda host: f)
    return f


@pytest.fixture
def engine(tmp_path: Path, fake) -> BRMEngine:
    origem = RAIZ / "clients" / "clinica-piloto"
    destino = tmp_path / "clients" / "clinica-piloto"
    shutil.copytree(origem, destino, ignore=shutil.ignore_patterns("*.db"))
    cfg = destino / "config" / "client_config.yaml"
    cfg.write_text(
        cfg.read_text().replace(
            "vault_root: clients/clinica-piloto/vault", f"vault_root: {destino / 'vault'}"
        )
    )
    eng = BRMEngine("clinica-piloto", tmp_path / "clients", tmp_path / "data")
    eng.acoes.atributos.definir_pin("local", SEC.id, PIN, por=SEC.id)
    return eng


def como(perfil: UserProfile):
    return set_current_user(perfil)


def aprovar(engine: BRMEngine) -> None:
    como(GESTOR_PERFIL)
    assert engine.documento_aprovar_modelo(MODELO_CONTRATO)["allowed"]
    como(SEC.perfil)


def pedir(engine: BRMEngine, **args) -> dict:
    como(SEC.perfil)
    return engine.criar_acao("agenda.emitir_contrato_sessao", {"agendamento_id": "a1", **args})


class TestModeloDeExemplo:
    def test_modelo_do_piloto_valido_e_marcado_como_exemplo(self, engine) -> None:
        modelos, erros = engine.documentos.modelos()
        assert erros == {} and modelos[MODELO_CONTRATO].exemplo is True
        assert "EXEMPLO" in modelos[MODELO_CONTRATO].titulo

    def test_tipo_registrado_com_prefixo_do_modulo(self, engine) -> None:
        nomes = [t["tipo"] for t in engine.tipos_de_acao()["tipos"]]
        assert "agenda.emitir_contrato_sessao" in nomes

    def test_so_gestor_aprova_modelo(self, engine) -> None:
        como(SEC.perfil)
        assert engine.documento_aprovar_modelo(MODELO_CONTRATO)["allowed"] is False
        assert engine.documento_modelos()["allowed"] is False


class TestFluxo:
    def test_emite_contrato_com_dados_ao_vivo_e_download(self, engine, fake) -> None:
        aprovar(engine)
        r = pedir(engine)
        assert r["allowed"], r
        pagina = engine.acoes.abrir(SEC, r["acao_id"])
        item = pagina["itens"][0]
        assert item["campos_faltantes"] == []
        assert item["dados"]["menor_de_idade"] is False
        valores = {c["campo"]: c["valor"] for c in item["dados"]["campos"]}
        assert valores["paciente_nome"] == "Maria Silva"
        assert valores["sessao_preco"] == "R$ 150,00"
        assert valores["emissor_razao_social"] == "Clínica Piloto (exemplo)"

        saida = engine.acoes.confirmar(SEC, r["acao_id"], PIN)
        assert saida["status"] == "confirmada"
        documento_id = saida["resultado"][0]["documento_id"]
        doc = engine.documentos.store.obter(documento_id)
        assert doc.emitido_por == SEC.id and doc.acao_id == r["acao_id"]
        assert doc.referencia == referencia_cliente("c1")
        pdf = engine.documentos.store.ler_pdf(documento_id)
        assert pdf.startswith(b"%PDF")

        chat = engine.status_da_acao(r["acao_id"])
        assert "Maria" not in str(chat) and "529" not in str(chat)
        assert chat["resultado"][0]["documento_id"] == documento_id and "sha256" not in str(chat)

    def test_modelo_nao_aprovado_falha_e_nao_emite(self, engine) -> None:
        r = pedir(engine)
        saida = engine.acoes.confirmar(SEC, r["acao_id"], PIN)
        assert saida["status"] == "falhou" and "não está aprovado" in saida["motivo"]
        assert engine.documentos.store.listar() == []

    def test_campo_faltante_e_gravado_na_agenda_com_ator_e_trace(self, engine, fake) -> None:
        aprovar(engine)
        fake.fichas["c1"] = ficha(cpf=None)
        r = pedir(engine)
        pagina = engine.acoes.abrir(SEC, r["acao_id"])
        faltantes = pagina["itens"][0]["campos_faltantes"]
        assert [f["nome"] for f in faltantes] == ["paciente_cpf"] and faltantes[0]["editavel"]
        with pytest.raises(AcaoErro) as exc:
            engine.acoes.confirmar(SEC, r["acao_id"], PIN)
        assert exc.value.status == 422
        with pytest.raises(AcaoErro, match="CPF inválido"):
            engine.acoes.confirmar(
                SEC, r["acao_id"], PIN, [{"item": 0, "nome": "paciente_cpf", "valor": "111"}]
            )
        saida = engine.acoes.confirmar(
            SEC, r["acao_id"], PIN, [{"item": 0, "nome": "paciente_cpf", "valor": "529.982.247-25"}]
        )
        assert saida["status"] == "confirmada"
        escrita = fake.escritas[-1]
        assert escrita["campos"] == {"cpf": "52998224725"} and escrita["ator"] == SEC.id
        assert escrita["trace_id"]
        assert fake.fichas["c1"].cpf == "52998224725"

    def test_menor_exige_dados_do_responsavel(self, engine, fake) -> None:
        aprovar(engine)
        fake.fichas["c1"] = ficha(nascimento=date(2017, 3, 2))
        r = pedir(engine)
        faltantes = engine.acoes.abrir(SEC, r["acao_id"])["itens"][0]["campos_faltantes"]
        assert {f["nome"] for f in faltantes} == {
            "responsavel_nome",
            "responsavel_cpf",
            "responsavel_parentesco",
        }
        fake.fichas["c1"] = ficha(
            nascimento=date(2017, 3, 2),
            resp=[Responsavel("Paulo Silva", "11144477735", "pai", None, None, True)],
        )
        saida = engine.acoes.confirmar(SEC, r["acao_id"], PIN)
        assert saida["status"] == "confirmada"
        dados = engine.documentos.store.dados(saida["resultado"][0]["documento_id"])
        assert dados["responsavel_nome"] == "Paulo Silva"

    def test_adulto_nao_exige_responsavel(self, engine) -> None:
        r = pedir(engine)
        itens = engine.acoes.abrir(SEC, r["acao_id"])["itens"]
        assert all(not f["nome"].startswith("responsavel") for f in itens[0]["campos_faltantes"])

    def test_correcao_aponta_para_o_original(self, engine) -> None:
        aprovar(engine)
        original = engine.acoes.confirmar(SEC, pedir(engine)["acao_id"], PIN)
        doc_id = original["resultado"][0]["documento_id"]
        corrigido = engine.acoes.confirmar(SEC, pedir(engine, substitui=doc_id)["acao_id"], PIN)
        novo = engine.documentos.store.obter(corrigido["resultado"][0]["documento_id"])
        assert novo.substitui == doc_id and novo.id != doc_id

    def test_agendamento_inexistente(self, engine) -> None:
        r = pedir(engine, agendamento_id="nao-existe")
        with pytest.raises(AcaoErro) as exc:
            engine.acoes.abrir(SEC, r["acao_id"])
        assert exc.value.status == 503


class TestPacientePeloNome:
    def pedir_nome(self, engine, nome="maria silva", **args) -> dict:
        como(SEC.perfil)
        return engine.criar_acao("agenda.emitir_contrato_sessao", {"paciente": nome, **args})

    def sessao(self, fake, id_, nome="Maria Silva", cust="c1", dias=-1, status="COMPLETED"):
        agora = datetime.now(UTC).replace(microsecond=0)
        fake.agendamentos[id_] = ag(
            id_, cust, nome, inicio=agora + timedelta(days=dias), status=status
        )

    def test_resolve_a_sessao_mais_recente_concluida_sem_expor_ids(self, engine, fake) -> None:
        aprovar(engine)
        fake.agendamentos.clear()
        self.sessao(fake, "velha", dias=-20)
        self.sessao(fake, "nova", dias=-2)
        self.sessao(fake, "futura", dias=3, status="CONFIRMED")
        r = self.pedir_nome(engine)
        assert "nova" not in str(r) and "customer" not in str(r)
        saida = engine.acoes.confirmar(SEC, r["acao_id"], PIN)
        doc = engine.documentos.store.obter(saida["resultado"][0]["documento_id"])
        assert doc.metadados["agendamento_id"] == "nova"

    def test_sem_concluida_usa_a_mais_recente_nao_cancelada(self, engine, fake) -> None:
        aprovar(engine)
        fake.agendamentos.clear()
        self.sessao(fake, "cancelada", dias=1, status="CANCELLED")
        self.sessao(fake, "hoje", dias=0, status="CONFIRMED")
        saida = engine.acoes.confirmar(SEC, self.pedir_nome(engine)["acao_id"], PIN)
        doc = engine.documentos.store.obter(saida["resultado"][0]["documento_id"])
        assert doc.metadados["agendamento_id"] == "hoje"

    def test_nome_ambiguo_pede_nome_completo(self, engine, fake) -> None:
        fake.agendamentos.clear()
        self.sessao(fake, "s1", nome="Tiago Silva", cust="c1")
        self.sessao(fake, "s2", nome="Tiago Souza", cust="c2")
        r = self.pedir_nome(engine, "tiago")
        with pytest.raises(AcaoErro, match="nome completo"):
            engine.acoes.abrir(SEC, r["acao_id"])
        self.sessao(fake, "s3", nome="Tiago Silva", cust="c1", dias=-3)
        assert engine.acoes.abrir(SEC, self.pedir_nome(engine, "Tiago Silva")["acao_id"])

    def test_sem_sessao_na_janela(self, engine, fake) -> None:
        fake.agendamentos.clear()
        self.sessao(fake, "antiga", dias=-400)
        r = self.pedir_nome(engine)
        with pytest.raises(AcaoErro, match="Nenhuma sessão"):
            engine.acoes.abrir(SEC, r["acao_id"])

    def test_exige_exatamente_uma_referencia(self, engine) -> None:
        como(SEC.perfil)
        r = engine.criar_acao(
            "agenda.emitir_contrato_sessao", {"paciente": "Maria", "agendamento_id": "a1"}
        )
        assert r.get("allowed") is False or "acao_id" not in r
        r = engine.criar_acao("agenda.emitir_contrato_sessao", {})
        assert r.get("allowed") is False or "acao_id" not in r


class TestStatusDaSessao:
    TIPO = "agenda.atualizar_status_sessao"

    def pedir_status(self, engine, **args) -> dict:
        como(SEC.perfil)
        return engine.criar_acao(self.TIPO, args)

    def sessao(self, fake, id_, dias=0, status="CONFIRMED", nome="Maria Silva", cust="c1"):
        agora = datetime.now(UTC).replace(microsecond=0)
        fake.agendamentos[id_] = ag(
            id_, cust, nome, inicio=agora + timedelta(days=dias), status=status
        )

    def test_conclui_a_sessao_mais_proxima_e_atualiza_a_agenda(self, engine, fake) -> None:
        fake.agendamentos.clear()
        self.sessao(fake, "antiga", dias=-30, status="COMPLETED")
        self.sessao(fake, "hoje", dias=0)
        self.sessao(fake, "futura", dias=7)
        r = self.pedir_status(engine, paciente="maria silva", status="COMPLETED")
        assert "acao_id" in r
        pagina = engine.acoes.abrir(SEC, r["acao_id"])["itens"][0]
        assert "Maria Silva" in pagina["resumo"] and "concluída" in pagina["resumo"]
        assert pagina["dados"]["status_atual"] == "CONFIRMED"
        saida = engine.acoes.confirmar(SEC, r["acao_id"], PIN)
        assert saida["status"] == "confirmada"
        assert fake.agendamentos["hoje"].status == "COMPLETED"
        assert fake.agendamentos["futura"].status == "CONFIRMED"
        escrita = fake.status_escritos[-1]
        assert escrita["agendamento_id"] == "hoje" and escrita["ator"] == SEC.id

    def test_pelo_id_do_agendamento(self, engine, fake) -> None:
        r = self.pedir_status(engine, agendamento_id="a1", status="NO_SHOW")
        saida = engine.acoes.confirmar(SEC, r["acao_id"], PIN)
        assert saida["status"] == "confirmada" and fake.agendamentos["a1"].status == "NO_SHOW"

    def test_mesmo_status_e_recusado(self, engine, fake) -> None:
        r = self.pedir_status(engine, agendamento_id="a1", status="COMPLETED")
        with pytest.raises(AcaoErro, match="já está concluída"):
            engine.acoes.abrir(SEC, r["acao_id"])

    def test_cancelar_exige_motivo_e_liberacao_do_gestor(self, engine, fake) -> None:
        sem_motivo = self.pedir_status(engine, agendamento_id="a1", status="CANCELLED")
        assert "acao_id" not in sem_motivo
        r = self.pedir_status(engine, agendamento_id="a1", status="CANCELLED", motivo="Pediu")
        assert engine.acoes.abrir(SEC, r["acao_id"])["requer"] == "liberacao_gestor"
        with pytest.raises(AcaoErro):
            engine.acoes.confirmar(SEC, r["acao_id"], PIN)
        assert fake.agendamentos["a1"].status == "COMPLETED"

    def test_motivo_so_vale_para_cancelamento_e_status_novo_e_recusado(self, engine) -> None:
        for args in (
            {"agendamento_id": "a1", "status": "COMPLETED", "motivo": "x"},
            {"agendamento_id": "a1", "status": "DONE"},
            {"agendamento_id": "a1", "paciente": "Maria", "status": "COMPLETED"},
        ):
            assert "acao_id" not in self.pedir_status(engine, **args)

    def test_http_patch_no_endpoint_de_status_com_ator_e_trace(self) -> None:
        import httpx

        aid = "22222222-2222-4222-8222-222222222222"
        vistos = []

        def handler(req: httpx.Request) -> httpx.Response:
            vistos.append(req)
            return httpx.Response(200, json={"success": True, "data": {"id": aid}})

        TestContratoDoProvider()._http(handler).atualizar_status(
            aid, "CANCELLED", motivo="Pediu", ator="ana@c.com", trace_id="tr1"
        )
        req = vistos[0]
        assert req.method == "PATCH"
        assert req.url.path.endswith(f"/api/v1/clinic/appointments/{aid}/status")
        assert req.headers["x-brm-actor"] == "ana@c.com" and req.headers["x-brm-trace-id"] == "tr1"
        import json

        assert json.loads(req.content) == {"status": "CANCELLED", "cancel_reason": "Pediu"}

    def test_http_traduz_recusas_da_agenda(self) -> None:
        import httpx

        aid = "22222222-2222-4222-8222-222222222222"
        provider = TestContratoDoProvider()._http(
            lambda req: httpx.Response(422, json={"code": "PRICE_UNRESOLVED", "error": "x"})
        )
        with pytest.raises(ExecucaoFalhou, match="definir o preço"):
            provider.atualizar_status(aid, "COMPLETED", motivo=None, ator="a", trace_id=None)


class TestPagamentoNaAgenda:
    TIPO = "agenda.registrar_pagamento"

    @pytest.fixture(autouse=True)
    def _clientes(self, fake) -> None:
        fake.clientes = [
            Cliente("c1", "Maria Silva", "11999990001"),
            Cliente("c2", "Maria Souza", "11999990002"),
            Cliente("c3", "Tiago Pereira Ramos", None),
        ]
        fake.saldos["c3"] = {"outstanding": 0, "accumulating": 200, "credit": 0}

    def pedir_pagamento(self, engine, **args) -> dict:
        como(SEC.perfil)
        base = {"paciente": "tiago pereira ramos", "valor": "200", "forma": "dinheiro"}
        return engine.criar_acao(self.TIPO, {**base, **args})

    def test_grava_na_agenda_com_ator_e_mostra_o_saldo(self, engine, fake) -> None:
        r = self.pedir_pagamento(engine)
        item = engine.acoes.abrir(SEC, r["acao_id"])["itens"][0]
        assert "R$ 200,00" in item["resumo"] and "Tiago Pereira Ramos" in item["resumo"]
        assert item["dados"]["forma"] == "dinheiro" and item["dados"]["a_vencer_no_mes"]
        saida = engine.acoes.confirmar(SEC, r["acao_id"], PIN)
        assert saida["status"] == "confirmada"
        pagamento = fake.pagamentos[0]
        assert pagamento["customer_id"] == "c3" and pagamento["centavos"] == 20000
        assert pagamento["metodo"] == "CASH" and pagamento["ator"] == SEC.id
        assert pagamento["referencia"].startswith(f"brm-{r['acao_id']}-0-")
        assert "registrado na Agenda" in saida["resultado"][0]["resumo"]

    def test_repetir_a_confirmacao_nao_duplica(self, engine, fake) -> None:
        r = self.pedir_pagamento(engine)
        engine.acoes.confirmar(SEC, r["acao_id"], PIN)
        with pytest.raises(AcaoErro):
            engine.acoes.confirmar(SEC, r["acao_id"], PIN)
        assert len(fake.pagamentos) == 1

    def test_nome_ambiguo_e_inexistente(self, engine) -> None:
        for nome, trecho in (("maria", "nome completo"), ("fulano", "não encontrado")):
            r = self.pedir_pagamento(engine, paciente=nome)
            with pytest.raises(AcaoErro, match=trecho):
                engine.acoes.abrir(SEC, r["acao_id"])

    @pytest.mark.parametrize(
        "args",
        [
            {"valor": "0"},
            {"valor": "abc"},
            {"valor": "1,234"},
            {"forma": "cheque"},
            {"data": "2999-01-01"},
            {"data": "03/11/2026"},
        ],
    )
    def test_entradas_invalidas_nao_chegam_a_agenda(self, engine, fake, args) -> None:
        r = self.pedir_pagamento(engine, **args)
        if "acao_id" in r:
            with pytest.raises(AcaoErro):
                engine.acoes.abrir(SEC, r["acao_id"])
        assert fake.pagamentos == []

    def test_exige_paciente_ou_cliente_id(self, engine) -> None:
        como(SEC.perfil)
        assert "acao_id" not in engine.criar_acao(self.TIPO, {"valor": "10", "forma": "pix"})

    def test_http_post_no_endpoint_de_pagamento(self) -> None:
        import json
        from datetime import timedelta, timezone

        import httpx

        cid = "33333333-3333-4333-8333-333333333333"
        vistos = []

        def handler(req: httpx.Request) -> httpx.Response:
            vistos.append(req)
            return httpx.Response(
                201, json={"success": True, "payment_id": "p", "status": "CONFIRMED"}
            )

        provider = TestContratoDoProvider()._http(handler)
        resposta = provider.registrar_pagamento(
            cid,
            20050,
            "CASH",
            recebido_em=datetime(2026, 11, 3, 12, 0, tzinfo=timezone(timedelta(hours=-3))),
            referencia="brm-x",
            nota=None,
            ator="ana@c.com",
            trace_id="tr1",
        )
        assert resposta["status"] == "CONFIRMED"
        req = vistos[0]
        assert req.method == "POST" and req.url.path.endswith("/api/v1/clinic/payments")
        assert req.headers["x-brm-actor"] == "ana@c.com"
        assert json.loads(req.content) == {
            "customer_id": cid,
            "amount": 200.5,
            "method": "CASH",
            "external_ref": "brm-x",
            "received_at": "2026-11-03T12:00:00-03:00",
        }

    def test_http_traduz_conflito_de_referencia(self) -> None:
        import httpx

        provider = TestContratoDoProvider()._http(
            lambda req: httpx.Response(409, json={"code": "PAYMENT_REF_CONFLICT"})
        )
        with pytest.raises(ExecucaoFalhou, match="já foi usada"):
            provider.registrar_pagamento(
                "33333333-3333-4333-8333-333333333333",
                100,
                "PIX",
                recebido_em=datetime.now(UTC),
                referencia="x",
                nota=None,
                ator="a",
                trace_id=None,
            )


class TestContratanteECrp:
    MODELO = (
        "---\ndocumento: contrato-sessao\nversao: 3\n---\n"
        "Psicólogo {{ sessao.profissional }}, CRP {{ profissional.crp }}.\n\n"
        "Contratante: {% if contratante.e_responsavel %}{{ responsavel.nome }}, CPF"
        " {{ responsavel.cpf }}, CEP {{ responsavel.endereco.cep }}{% endif %}"
        "{% if contratante.e_paciente %}{{ paciente.nome }}, CPF {{ paciente.cpf }}{% endif %}.\n\n"
        "Emitido em {{ emissao.data }}.\n"
    )

    def _fontes(self, **kw):
        return montar_fontes(ag("a1"), ficha(**kw), None, "14/03059-0")

    def test_adulto_e_o_proprio_contratante(self) -> None:
        f = self._fontes()
        assert f["contratante"] == {"e_responsavel": False, "e_paciente": True}
        assert f["profissional"]["crp"] == "14/03059-0" and f["emissao"]["data"]

    def test_menor_tem_o_responsavel_como_contratante(self) -> None:
        resp = Responsavel("Paulo", "11144477735", "pai", None, None, True, ENDERECO)
        f = self._fontes(nascimento=date(2017, 3, 2), resp=[resp])
        assert f["contratante"] == {"e_responsavel": True, "e_paciente": False}
        assert f["responsavel"]["endereco"]["cep"] == "01001000"

    def test_adulto_com_responsavel_financeiro_tambem(self) -> None:
        resp = Responsavel("Lia", "11144477735", "esposa", None, None, True)
        assert self._fontes(resp=[resp])["contratante"]["e_responsavel"] is True
        sem_financeiro = Responsavel("Lia", "11144477735", "esposa", None, None, False)
        assert self._fontes(resp=[sem_financeiro])["contratante"]["e_responsavel"] is False

    def test_crp_pela_configuracao_ignora_acento_e_caixa(self) -> None:
        from mcp_engine.modules.agenda.contrato import crp_do_profissional

        emissor = {"crp_joao_conceicao": "14/00001-2"}
        assert crp_do_profissional(emissor, "João Conceição") == "14/00001-2"
        assert crp_do_profissional(emissor, "Outra Pessoa") is None
        assert crp_do_profissional(emissor, None) is None

    def test_modelo_renderiza_a_parte_certa(self) -> None:
        from mcp_engine.core.documentos import parse_modelo, renderizar, resolver_valores

        m = parse_modelo(self.MODELO)
        adulto = renderizar(m, resolver_valores(m, self._fontes()))
        assert "Contratante: Maria Silva, CPF 529.982.247-25." in adulto
        assert "CEP" not in adulto and "CRP 14/03059-0" in adulto
        resp = Responsavel("Paulo", "11144477735", "pai", None, None, True, ENDERECO)
        menor = renderizar(
            m, resolver_valores(m, self._fontes(nascimento=date(2017, 3, 2), resp=[resp]))
        )
        assert "Contratante: Paulo, CPF 111.444.777-35, CEP 01001000." in menor

    def test_endereco_do_responsavel_faltante_e_gravado_na_agenda(self, engine, fake) -> None:
        vault = engine._vault_root() / "_modelos"
        (vault / f"{MODELO_CONTRATO}.md").write_text(self.MODELO)
        como(GESTOR_PERFIL)
        assert engine.documento_aprovar_modelo(MODELO_CONTRATO)["allowed"]
        fake.fichas["c1"] = ficha(
            nascimento=date(2017, 3, 2),
            resp=[Responsavel("Paulo", "11144477735", "pai", None, None, True)],
        )
        r = pedir(engine)
        sem_crp = engine.acoes.abrir(SEC, r["acao_id"])["itens"][0]["campos_faltantes"]
        assert [(f["nome"], f["editavel"]) for f in sem_crp][0] == ("profissional_crp", False)
        engine.documentos._emissor["crp_ana_souza"] = "14/03059-0"
        faltantes = engine.acoes.abrir(SEC, r["acao_id"])["itens"][0]["campos_faltantes"]
        assert [f["nome"] for f in faltantes] == ["responsavel_endereco_cep"]
        assert faltantes[0]["editavel"] is True
        engine.acoes.confirmar(
            SEC,
            r["acao_id"],
            PIN,
            [{"item": 0, "nome": "responsavel_endereco_cep", "valor": "79102-420"}],
        )
        assert fake.escritas[-1]["campos"] == {"responsavel": {"endereco": {"cep": "79102420"}}}
        assert fake.fichas["c1"].responsaveis[0].endereco.cep == "79102420"


class TestNovidadesDaAgenda:
    AID = "44444444-4444-4444-8444-444444444444"

    def _http(self, handler):
        return TestContratoDoProvider()._http(handler)

    def _item(self, **kw):
        from tests.test_agenda_provider import ITEM

        return {**ITEM, "id": self.AID, **kw}

    def test_agendamento_pela_rota_direta(self) -> None:
        import httpx

        vistos = []

        def handler(req: httpx.Request) -> httpx.Response:
            vistos.append(req.url.path)
            return httpx.Response(200, json={"data": self._item()})

        ag = self._http(handler).obter_agendamento(self.AID)
        assert ag.id == self.AID and vistos == [f"/api/v1/appointments/{self.AID}"]

    def test_agendamento_inexistente_nao_procura_na_lista(self) -> None:
        import httpx

        vistos = []

        def handler(req: httpx.Request) -> httpx.Response:
            vistos.append(req.url.path)
            return httpx.Response(404, json={"code": "APPOINTMENT_NOT_FOUND"})

        assert self._http(handler).obter_agendamento(self.AID) is None
        assert len(vistos) == 1

    def test_rota_ainda_nao_publicada_cai_na_lista(self) -> None:
        import httpx

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path.endswith(self.AID):
                return httpx.Response(404, text="<html>erro</html>")
            return httpx.Response(200, json={"data": [self._item()], "next_cursor": None})

        assert self._http(handler).obter_agendamento(self.AID).id == self.AID

    def test_crp_vem_do_cadastro_do_profissional(self) -> None:
        import httpx

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"data": [{"id": "s1", "name": "Ana", "role": "psi", "crp": "CRP 14/1"}]}
            )

        assert self._http(handler).listar_profissionais()[0].crp == "CRP 14/1"

    def test_numero_do_crp_aceita_prefixo_e_prefere_o_da_agenda(self) -> None:
        from mcp_engine.modules.agenda.contrato import crp_do_profissional

        emissor = {"crp_ana": "14/00001-2"}
        assert crp_do_profissional(emissor, "Ana", "CRP 14/03059-0") == "14/03059-0"
        assert crp_do_profissional(emissor, "Ana", "crp14/03059-0") == "14/03059-0"
        assert crp_do_profissional(emissor, "Ana", None) == "14/00001-2"
        assert crp_do_profissional({}, "Ana", "") is None

    def test_contrato_usa_o_crp_do_cadastro_da_agenda(self, engine, fake) -> None:
        engine.documentos._emissor.pop("crp_ana_souza", None)
        fake.profissionais = [Profissional("st1", "Ana Souza", "psi", True, "CRP 14/03059-0")]
        r = pedir(engine)
        itens = engine.acoes.abrir(SEC, r["acao_id"])["itens"][0]["dados"]["campos"]
        assert not [c for c in itens if c["campo"] == "profissional_crp" and c["faltante"]]


class TestEmAtendimentoECrp:
    def sessao(self, fake, status):
        agora = datetime.now(UTC).replace(microsecond=0)
        fake.agendamentos.clear()
        fake.agendamentos["s1"] = ag("s1", "c1", "Maria Silva", inicio=agora, status=status)

    def status(self, engine, **args) -> dict:
        como(SEC.perfil)
        return engine.criar_acao(
            "agenda.atualizar_status_sessao", {"paciente": "Maria Silva", **args}
        )

    def test_iniciar_atendimento_de_sessao_confirmada(self, engine, fake) -> None:
        self.sessao(fake, "CONFIRMED")
        r = self.status(engine, status="IN_PROGRESS")
        item = engine.acoes.abrir(SEC, r["acao_id"])["itens"][0]
        assert "em atendimento" in item["resumo"] and "não gera cobrança" in item["dados"]["aviso"]
        engine.acoes.confirmar(SEC, r["acao_id"], PIN)
        assert fake.agendamentos["s1"].status == "IN_PROGRESS"
        concluir = self.status(engine, status="COMPLETED")
        engine.acoes.confirmar(SEC, concluir["acao_id"], PIN)
        assert fake.agendamentos["s1"].status == "COMPLETED"

    @pytest.mark.parametrize("origem", ["COMPLETED", "NO_SHOW"])
    def test_nao_inicia_atendimento_de_sessao_encerrada(self, engine, fake, origem) -> None:
        self.sessao(fake, origem)
        r = self.status(engine, status="IN_PROGRESS")
        with pytest.raises(AcaoErro, match="Só dá para iniciar|já está"):
            engine.acoes.abrir(SEC, r["acao_id"])

    def test_http_traduz_transicao_invalida(self) -> None:
        import httpx

        provider = TestContratoDoProvider()._http(
            lambda req: httpx.Response(409, json={"code": "INVALID_STATUS_TRANSITION"})
        )
        with pytest.raises(ExecucaoFalhou, match="não permite essa mudança"):
            provider.atualizar_status(
                "55555555-5555-4555-8555-555555555555",
                "IN_PROGRESS",
                motivo=None,
                ator="a",
                trace_id=None,
            )

    def crp(self, engine, perfil, **args) -> dict:
        como(perfil)
        return engine.criar_acao("agenda.definir_crp_profissional", args)

    def test_so_o_gestor_pede_o_cadastro_do_crp(self, engine, fake) -> None:
        fake.profissionais = [Profissional("st1", "Ana Souza", "psi", True)]
        r = self.crp(engine, SEC.perfil, profissional="ana souza", crp="CRP 14/03059-0")
        with pytest.raises(AcaoErro) as exc:
            engine.acoes.abrir(SEC, r["acao_id"])
        assert exc.value.status == 403
        gestor = Identidade(
            id=GESTOR_PERFIL.identity, perfil=GESTOR_PERFIL, provedor="local", metodo="token"
        )
        assert engine.acoes.abrir(gestor, r["acao_id"])["itens"][0]["dados"]["crp"] == (
            "CRP 14/03059-0"
        )

    def test_cadastra_o_crp_na_agenda_com_ator(self, engine, fake) -> None:
        fake.profissionais = [Profissional("st1", "Ana Souza", "psi", True)]
        r = self.crp(engine, GESTOR_PERFIL, profissional="Ana Souza", crp="14/03059-0")
        gestor = Identidade(
            id=GESTOR_PERFIL.identity, perfil=GESTOR_PERFIL, provedor="local", metodo="token"
        )
        engine.acoes.atributos.definir_pin("local", gestor.id, "7391", por=gestor.id)
        engine.acoes.confirmar(gestor, r["acao_id"], "7391")
        assert fake.crps_escritos[-1] == {
            "profissional_id": "st1",
            "crp": "14/03059-0",
            "ator": gestor.id,
        }
        assert fake.profissionais[0].crp == "14/03059-0"

    @pytest.mark.parametrize("crp", ["123", "CRP", "14-03059", "14/03059-00", "x" * 25])
    def test_crp_em_formato_estranho_e_recusado(self, engine, crp) -> None:
        r = self.crp(engine, GESTOR_PERFIL, profissional="Ana", crp=crp)
        assert "acao_id" not in r

    def test_http_put_do_crp(self) -> None:
        import json

        import httpx

        vistos = []

        def handler(req: httpx.Request) -> httpx.Response:
            vistos.append(req)
            return httpx.Response(200, json={"success": True})

        TestContratoDoProvider()._http(handler).definir_crp(
            "66666666-6666-4666-8666-666666666666", "CRP 14/1", ator="g@c.com", trace_id="t1"
        )
        req = vistos[0]
        assert req.method == "PUT" and req.url.path.endswith(
            "/staff/66666666-6666-4666-8666-666666666666/crp"
        )
        assert json.loads(req.content) == {"crp": "CRP 14/1"}
        assert req.headers["x-brm-actor"] == "g@c.com"


class TestFatosERegra:
    def avaliar(self, engine, perfil=SEC.perfil) -> dict:
        como(perfil)
        return engine.avaliar_skill(
            "registrar_pacote",
            {"paciente": "Maria Silva", "modelo": "mensal", "inicio": "2026-11-01"},
        )

    def sincronizar(self, engine) -> None:
        como(GESTOR_PERFIL)
        assert engine.call_skill("sincronizar_agenda", {})["allowed"]

    def test_sem_sessao_concluida_nao_bloqueia(self, engine, fake) -> None:
        fake.agendamentos["a1"] = ag("a1", status="CONFIRMED")
        self.sincronizar(engine)
        v = self.avaliar(engine)
        assert v["fatos_de_modulo"] == {
            "primeira_sessao_concluida": False,
            "contrato_emitido": False,
        }
        assert v["efeito"] == "allow"

    def test_no_show_nao_conta_como_primeira_sessao(self, engine, fake) -> None:
        fake.agendamentos["a1"] = ag("a1", status="NO_SHOW")
        self.sincronizar(engine)
        v = self.avaliar(engine)
        assert v["fatos_de_modulo"]["primeira_sessao_concluida"] is False
        assert v["efeito"] == "allow"
        fake.semear(ag("a2", status="COMPLETED"))
        assert self.avaliar(engine)["fatos_de_modulo"]["primeira_sessao_concluida"] is True

    def test_concluida_sem_contrato_exige_gestor_e_contrato_libera(self, engine, fake) -> None:
        self.sincronizar(engine)
        v = self.avaliar(engine)
        assert v["efeito"] == "require_approval" and v["rule_id"] == "contrato-antes-do-pacote"
        aprovar(engine)
        engine.acoes.confirmar(SEC, pedir(engine)["acao_id"], PIN)
        v = self.avaliar(engine)
        assert v["fatos_de_modulo"]["contrato_emitido"] is True and v["efeito"] == "allow"

    def test_chamador_nao_forca_o_fato(self, engine, fake) -> None:
        self.sincronizar(engine)
        como(SEC.perfil)
        v = engine.avaliar_skill(
            "registrar_pacote",
            {
                "paciente": "Maria Silva",
                "modelo": "m",
                "inicio": "2026-11-01",
                "contrato_emitido": True,
            },
        )
        assert v["efeito"] == "require_approval"

    def test_historico_indisponivel_da_fato_nulo_sem_quebrar(
        self, engine, fake, monkeypatch
    ) -> None:
        self.sincronizar(engine)

        def sem_historico(customer_id):
            raise AgendaRecursoIndisponivel("G15")

        monkeypatch.setattr(fake, "historico_cliente", sem_historico)
        v = self.avaliar(engine)
        assert v["fatos_de_modulo"]["primeira_sessao_concluida"] is None
        assert v["efeito"] == "allow"

    def test_pendentes_so_com_sessao_concluida_sem_contrato(self, engine, fake) -> None:
        fake.semear(ag("a2", cust="c2", nome="Joao Lima", status="NO_SHOW"))
        como(GESTOR_PERFIL)
        r = engine.call_skill(
            "agenda_contratos_pendentes", {"de": "2026-10-01", "ate": "2026-10-31"}
        )
        assert '"total": 1' in r["result"] and "Maria Silva" in r["result"]
        assert "Joao" not in r["result"]
        como(SEC.perfil)
        r = engine.call_skill(
            "agenda_contratos_pendentes", {"de": "2026-10-01", "ate": "2026-10-31"}
        )
        assert "Maria" not in r["result"] and '"agregado": true' in r["result"]
        aprovar(engine)
        engine.acoes.confirmar(SEC, pedir(engine)["acao_id"], PIN)
        como(GESTOR_PERFIL)
        r = engine.call_skill(
            "agenda_contratos_pendentes", {"de": "2026-10-01", "ate": "2026-10-31"}
        )
        assert '"total": 0' in r["result"]


class TestFronteiraDeDados:
    def test_cadastro_so_existe_dentro_do_documento_emitido(self, engine, fake, tmp_path) -> None:
        import sqlite3

        aprovar(engine)
        fake.fichas["c1"] = ficha(cpf=None)
        r = pedir(engine)
        engine.acoes.abrir(SEC, r["acao_id"])
        engine.acoes.confirmar(
            SEC, r["acao_id"], PIN, [{"item": 0, "nome": "paciente_cpf", "valor": "52998224725"}]
        )
        engine.status_da_acao(r["acao_id"])
        sensiveis = ("52998224725", "529.982.247-25", "11999990001", "Rua A", "Maria Silva")
        for banco in (tmp_path / "data" / "clinica-piloto").glob("*.db"):
            con = sqlite3.connect(banco)
            tabelas = [
                t for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            ]
            for tabela in tabelas:
                if tabela == "documento_emitido":
                    continue
                for linha in con.execute(f'SELECT * FROM "{tabela}"'):
                    texto = str(linha)
                    assert not [x for x in sensiveis if x in texto], (banco.name, tabela)
            con.close()
        audit = (tmp_path / "data" / "clinica-piloto" / "audit.jsonl").read_text()
        assert not [x for x in sensiveis if x in audit]


class TestContratoDoProvider:
    def _http(self, handler):
        import httpx

        return HttpAgendaCadastroProvider(
            "https://agenda.brmsolutions.com.br",
            "k",
            transport=httpx.MockTransport(handler),
            sleep=lambda _s: None,
        )

    def test_http_le_ficha_mascarada_e_historico(self) -> None:
        import httpx

        cid = "11111111-1111-4111-8111-111111111111"
        vistos = []

        def handler(req: httpx.Request) -> httpx.Response:
            vistos.append(req)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "paciente": {
                            "id": cid,
                            "nome": "Ana",
                            "nascimento": "2012-04-03",
                            "cpf": "***.***.***-12",
                            "endereco": {"cidade": "São Paulo", "uf": "SP"},
                        },
                        "responsaveis": [
                            {"id": "r1", "nome": "Maria", "parentesco": "mãe", "financeiro": True}
                        ],
                        "historico": [
                            {
                                "agendamento_id": "a1",
                                "status": "COMPLETED",
                                "inicio_utc": "2026-10-01T13:00:00Z",
                            }
                        ],
                    }
                },
            )

        p = self._http(handler)
        assert p.historico_cliente(cid)[0].status == "COMPLETED"
        assert "unmask" not in vistos[-1].url.params
        f = p.obter_cliente(cid)
        assert f is not None and f.nascimento == date(2012, 4, 3)
        assert f.endereco.uf == "SP" and f.responsaveis[0].financeiro
        p.obter_cliente(cid, ator="ana", trace_id="t1", finalidade="contrato")
        req = vistos[-1]
        assert req.url.params["unmask"] == "1"
        assert req.headers["x-brm-actor"] == "ana" and req.headers["x-brm-trace-id"] == "t1"
        p.fechar()

    def test_http_cliente_inexistente_e_none(self) -> None:
        import httpx

        p = self._http(lambda r: httpx.Response(404, json={"code": "CUSTOMER_NOT_FOUND"}))
        assert p.obter_cliente("11111111-1111-4111-8111-111111111111") is None
        p.fechar()

    def test_http_grava_campo_com_ator_e_preserva_responsaveis(self) -> None:
        import json

        import httpx

        cid = "11111111-1111-4111-8111-111111111111"
        enviados = []

        def handler(req: httpx.Request) -> httpx.Response:
            if req.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "paciente": {"id": cid, "nome": "Ana"},
                            "responsaveis": [
                                {"id": "r1", "nome": "Maria", "financeiro": False},
                                {"id": "r2", "nome": "Paulo", "financeiro": True},
                            ],
                            "historico": [],
                        }
                    },
                )
            enviados.append(req)
            return httpx.Response(200, json={"data": {}})

        p = self._http(handler)
        p.atualizar_cliente(
            cid,
            {"cpf": "52998224725", "responsavel": {"parentesco": "pai"}},
            ator="ana",
            trace_id="t9",
        )
        req = enviados[0]
        assert req.headers["x-brm-actor"] == "ana" and req.headers["x-brm-trace-id"] == "t9"
        corpo = json.loads(req.content)
        assert corpo["cpf"] == "52998224725"
        assert corpo["responsaveis"] == [{"id": "r1"}, {"id": "r2", "parentesco": "pai"}]
        p.fechar()

    def test_http_cpf_repetido_vira_erro_claro(self) -> None:
        import httpx

        p = self._http(lambda r: httpx.Response(409, json={"code": "CPF_ALREADY_REGISTERED"}))
        with pytest.raises(Exception, match="já está cadastrado"):
            p.atualizar_cliente(
                "11111111-1111-4111-8111-111111111111", {"cpf": "1"}, ator="a", trace_id="t"
            )
        p.fechar()

    def test_provider_de_leitura_continua_sem_metodos_de_cadastro(self) -> None:
        for nome in (
            "obter_agendamento",
            "obter_cliente",
            "historico_cliente",
            "atualizar_cliente",
        ):
            assert not hasattr(HttpAgendaProvider, nome)

    def test_fontes_calculam_menoridade_na_data_da_sessao(self) -> None:
        a = ag("a1", inicio=datetime(2026, 10, 9, 13, 0, tzinfo=UTC))
        assert montar_fontes(a, ficha(nascimento=date(2008, 10, 10)))["paciente"]["menor_de_idade"]
        assert not montar_fontes(a, ficha(nascimento=date(2008, 10, 9)))["paciente"][
            "menor_de_idade"
        ]
        assert montar_fontes(a, ficha(nascimento=None))["paciente"]["menor_de_idade"] is None

    def test_historico_do_fake(self, fake) -> None:
        assert fake.historico_cliente("c1") == [
            StatusSessao("a1", "COMPLETED", fake.agendamentos["a1"].inicio_utc)
        ]


def test_nota_modelo_do_gatilho_de_contratos_pendentes_e_valida() -> None:
    import re

    import yaml

    from mcp_engine.core.gatilhos_tipo import GatilhoBloco

    texto = (RAIZ / "mcp_engine/modules/agenda/defaults/gatilho-contratos-pendentes.md").read_text()
    bloco = re.search(r"```brm-config\n(.*?)\n```", texto, re.S).group(1)
    gatilho = GatilhoBloco.model_validate(yaml.safe_load(bloco))
    assert gatilho.acao.skill in MODULE.skills_defs


class TestModeloSimples:
    TEXTO = (
        "---\ndocumento: aditivo\nversao: 1\n---\n"
        "Paciente {{ paciente.nome }}, CPF {{ paciente.cpf }},"
        " nascido em {{ paciente.nascimento }}.\n"
        "{% if paciente.menor_de_idade %}Responsável {{ responsavel.nome }}.\n{% endif %}"
        "Pacote de {{ sessoes_pacote }} sessões por {{ valor_pacote }}.\n"
    )

    def test_campos_vem_do_texto_sem_declaracao(self) -> None:
        from mcp_engine.core.documentos import parse_modelo

        m = parse_modelo(self.TEXTO)
        por_nome = {c.nome: c for c in m.campos}
        assert m.inferido
        assert por_nome["paciente_cpf"].tipo == "cpf" and por_nome["paciente_cpf"].obrigatorio
        assert por_nome["paciente_nascimento"].tipo == "data"
        assert por_nome["responsavel_nome"].obrigatorio_quando == "paciente.menor_de_idade"
        assert not por_nome["responsavel_nome"].obrigatorio
        assert por_nome["sessoes_pacote"].origem == "livre.sessoes_pacote"
        assert por_nome["paciente_menor_de_idade"].tipo == "booleano"

    def test_preenche_e_o_texto_fixo_nao_muda(self) -> None:
        from mcp_engine.core.documentos import parse_modelo, renderizar, resolver_valores

        m = parse_modelo(self.TEXTO)
        fontes = {
            "paciente": {
                "nome": "Ana",
                "cpf": "52998224725",
                "nascimento": "2012-04-03",
                "menor_de_idade": True,
            },
            "responsavel": {"nome": "Maria"},
            "livre": {"sessoes_pacote": "8", "valor_pacote": "R$ 800,00"},
        }
        texto = renderizar(m, resolver_valores(m, fontes))
        assert texto == (
            "Paciente Ana, CPF 529.982.247-25, nascido em 03/04/2012.\n"
            "Responsável Maria.\nPacote de 8 sessões por R$ 800,00."
        )
        fontes["paciente"]["menor_de_idade"] = False
        assert "Responsável" not in renderizar(m, resolver_valores(m, fontes))

    def test_lacuna_livre_ausente_ou_invalida_bloqueia(self) -> None:
        import pytest

        from mcp_engine.core.documentos import (
            CampoObrigatorioAusente,
            ValorInvalido,
            parse_modelo,
            resolver_valores,
        )

        m = parse_modelo(self.TEXTO)
        base = {
            "paciente": {"nome": "Ana", "cpf": "52998224725", "nascimento": "2012-04-03"},
            "livre": {"sessoes_pacote": "8"},
        }
        with pytest.raises(CampoObrigatorioAusente, match="valor_pacote"):
            resolver_valores(m, base)
        base["livre"]["valor_pacote"] = "x\ny"
        with pytest.raises(ValorInvalido):
            resolver_valores(m, base)

    def test_modelo_sem_lacuna_e_recusado_e_fonte_ausente_vira_campo_faltante(self) -> None:
        import pytest

        from mcp_engine.core.documentos import ModeloInvalido, campos_faltantes, parse_modelo

        with pytest.raises(ModeloInvalido, match="nenhuma lacuna"):
            parse_modelo("---\ndocumento: x\nversao: 1\n---\nTexto fixo.")
        m = parse_modelo("---\ndocumento: x\nversao: 1\n---\n{{ outra.coisa }}")
        assert [c.nome for c in campos_faltantes(m, {})] == ["outra_coisa"]
