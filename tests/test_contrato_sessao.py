"""Contrato de sessão no módulo agenda (spec 035, fases 3 e 4), com a Agenda falsa."""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from mcp_engine.core.acoes import AcaoErro
from mcp_engine.core.auth import Role, UserProfile, current_user_var, set_current_user
from mcp_engine.core.identidade import Identidade
from mcp_engine.core.mcp_server import BRMEngine
from mcp_engine.modules.agenda import MODULE
from mcp_engine.modules.agenda.contrato import MODELO_CONTRATO, montar_fontes, referencia_cliente
from mcp_engine.modules.agenda.fake import FakeAgendaProvider
from mcp_engine.modules.agenda.provider import (
    AgendaRecursoIndisponivel,
    Endereco,
    FichaCliente,
    HttpAgendaCadastroProvider,
    HttpAgendaProvider,
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
