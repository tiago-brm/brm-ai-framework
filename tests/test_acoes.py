"""Central de Ações (spec 035, fase 2): links de uso único, PIN, liberação e trilha."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from mcp_engine.core.acoes import (
    AcaoErro,
    CampoFaltante,
    Previa,
    TipoAcao,
)
from mcp_engine.core.atributos_usuario import PinInvalido, validar_formato_pin
from mcp_engine.core.auth import Role, UserProfile, current_user_var, set_current_user
from mcp_engine.core.executor import ExecucaoFalhou
from mcp_engine.core.http_app import create_app
from mcp_engine.core.identidade import Identidade
from mcp_engine.core.mcp_server import BRMEngine

PIN_SEC = "2580"
PIN_GESTOR = "7391"
PIN_GESTOR2 = "4826"

SKILLS = """schema_version: 1
client_id: cli
skills:
  - name: enviar_aviso
    description: Envia um aviso
    method: POST
    url_template: https://api.example.com/aviso
    allowed_domains: [api.example.com]
    parameters: {texto: {type: string}}
    autonomia: A2
  - name: estornar
    description: Estorna um lançamento
    method: POST
    url_template: https://api.example.com/estorno
    allowed_domains: [api.example.com]
    parameters: {lancamento: {type: string}}
    autonomia: A3
    aprovador: admin
  - name: proibida
    description: Nunca pode
    method: POST
    url_template: https://api.example.com/x
    allowed_domains: [api.example.com]
    autonomia: A2
  - name: valor_alto
    description: Pagamento
    method: POST
    url_template: https://api.example.com/pg
    allowed_domains: [api.example.com]
    parameters: {valor: {type: number}}
    autonomia: A2
"""

RULES = """schema_version: 1
client_id: cli
rules:
  - id: proibida-nega
    description: nega
    condition: {"==": [{"var": "skill_name"}, "proibida"]}
    effect: deny
    message: Proibido pelas regras.
  - id: valor-alto-gestor
    description: valor alto exige gestor
    condition:
      and:
        - {"==": [{"var": "skill_name"}, "valor_alto"]}
        - {">": [{"var": "valor"}, 1000]}
    effect: require_approval
    aprovador: admin
    message: Valor alto.
"""


class ArgsTeste(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registro: str
    falhar: bool = False


class Produto:
    """Produto falso: dono do dado, com um campo obrigatório que pode faltar."""

    def __init__(self) -> None:
        self.campos: dict[str, dict[str, str]] = {}
        self.execucoes: list[dict[str, Any]] = []
        self.escritas: list[dict[str, Any]] = []

    def tipo(self, nome: str = "teste.registrar", **extra: Any) -> TipoAcao:
        def carregar(ctx, args) -> Previa:
            cpf = self.campos.get(args["registro"], {}).get("documento")
            faltantes = () if cpf else (CampoFaltante("documento", "Documento", "cpf", True),)
            return Previa(f"Registrar {args['registro']}", {"documento": cpf}, faltantes)

        def gravar(ctx, args, valores) -> None:
            if valores.get("documento") == "invalido":
                raise ExecucaoFalhou("Documento inválido.")
            self.escritas.append({"ator": ctx.ator, "trace_id": ctx.trace_id, **valores})
            self.campos.setdefault(args["registro"], {}).update(valores)

        def executar(ctx, args) -> dict[str, Any]:
            if args.get("falhar"):
                raise ExecucaoFalhou("produto recusou")
            self.execucoes.append({"ator": ctx.ator, "papel": ctx.papel, "acao": ctx.acao_id})
            return {"resumo": "Registrado.", "registro_id": f"r-{len(self.execucoes)}"}

        return TipoAcao(
            nome=nome,
            titulo="Registrar",
            descricao="Teste",
            esquema=ArgsTeste,
            resumir=lambda a: f"Registrar {a['registro']}",
            carregar=carregar,
            executar=executar,
            gravar_campos=gravar,
            **extra,
        )


def ident(email: str, role: Role, provedor: str = "local", **kw: Any) -> Identidade:
    return Identidade(
        id=email,
        perfil=UserProfile(identity=email, role=role),
        provedor=provedor,
        metodo="token",
        email=email,
        **kw,
    )


SEC = ident("sec@cli.test", Role.VIEWER)
SEC2 = ident("sec2@cli.test", Role.VIEWER)
GESTOR = ident("gestor@cli.test", Role.ADMIN)
GESTOR2 = ident("gestor2@cli.test", Role.ADMIN)


def escrever_cliente(raiz: Path, extra_config: str = "") -> None:
    cfg = raiz / "cfg" / "cli" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "rules.yaml").write_text(RULES)
    (cfg / "skills.yaml").write_text(SKILLS)
    (cfg / "client_config.yaml").write_text(
        "client_id: cli\ndomain_allowlist: [api.example.com]\n"
        "dlp_patterns:\n"
        "  - {pattern: '\\d{3}\\.\\d{3}\\.\\d{3}-\\d{2}', label: CPF, action: deny}\n"
        "central_acoes:\n  url_base: https://cli.brm.test\n  max_por_hora: 50\n" + extra_config
    )


@pytest.fixture(autouse=True)
def _limpa_usuario():
    token = current_user_var.set(None)
    yield
    current_user_var.reset(token)


@pytest.fixture
def amb(tmp_path: Path):
    escrever_cliente(tmp_path)
    engine = BRMEngine("cli", tmp_path / "cfg", tmp_path / "data")
    produto = Produto()
    engine.extensoes.acoes.registrar(produto.tipo())
    engine.extensoes.acoes.registrar(produto.tipo("teste.sensivel", sensivel=True))
    attrs = engine.acoes.atributos
    for pessoa, pin in ((SEC, PIN_SEC), (GESTOR, PIN_GESTOR), (GESTOR2, PIN_GESTOR2)):
        attrs.definir_pin(pessoa.provedor, pessoa.id, pin, por=pessoa.id)
    engine.produto = produto
    return engine


def criar(engine: BRMEngine, quem: Identidade = SEC, tipo="teste.registrar", **args) -> dict:
    args = args or {"registro": "R1"}
    token = set_current_user(quem.perfil)
    try:
        return engine.acoes.criar(quem, [{"tipo": tipo, "argumentos": args}])
    finally:
        current_user_var.reset(token)


def eventos(engine: BRMEngine, prefixo: str = "acao:") -> list:
    return [e for e in engine._audit_sink.iter_events("cli") if e.action.startswith(prefixo)]


class TestCriacao:
    def test_link_sem_parametros_com_id_de_128_bits(self, amb) -> None:
        r = criar(amb)
        assert r["allowed"] and re.fullmatch(r"[0-9a-f]{32}", r["acao_id"])
        assert r["link"] == f"https://cli.brm.test/acao/{r['acao_id']}"
        assert "?" not in r["link"] and r["status"] == "pendente"
        expira = datetime.fromisoformat(r["expira_em"])
        assert timedelta(minutes=29) < expira - datetime.now(UTC) <= timedelta(minutes=30)

    def test_validade_por_tipo(self, amb) -> None:
        amb.extensoes.acoes.registrar(amb.produto.tipo("teste.curta", validade_min=5))
        r = criar(amb, tipo="teste.curta")
        assert datetime.fromisoformat(r["expira_em"]) - datetime.now(UTC) <= timedelta(minutes=5)

    def test_sem_url_base_nao_cria(self, tmp_path) -> None:
        escrever_cliente(tmp_path)
        cfg = tmp_path / "cfg" / "cli" / "config" / "client_config.yaml"
        cfg.write_text(cfg.read_text().replace("  url_base: https://cli.brm.test\n", ""))
        engine = BRMEngine("cli", tmp_path / "cfg", tmp_path / "data")
        assert criar(engine, tipo="nucleo.executar_skill", skill="enviar_aviso")["allowed"] is False

    def test_tipo_desconhecido_e_argumento_invalido(self, amb) -> None:
        assert not criar(amb, tipo="teste.nao_existe")["allowed"]
        r = criar(amb, registro="R1", extra="x")
        assert not r["allowed"] and r["campos"] == ["extra"]

    def test_dado_pessoal_nos_argumentos_e_recusado(self, amb, tmp_path) -> None:
        r = criar(amb, registro="529.982.247-25")
        assert not r["allowed"] and "dado pessoal" in r["reason"]
        assert "529.982.247-25" not in (tmp_path / "data" / "cli" / "state.db").read_bytes().decode(
            "latin-1"
        )

    def test_limite_por_hora(self, tmp_path) -> None:
        escrever_cliente(tmp_path)
        cfg = tmp_path / "cfg" / "cli" / "config" / "client_config.yaml"
        cfg.write_text(cfg.read_text().replace("max_por_hora: 50", "max_por_hora: 2"))
        engine = BRMEngine("cli", tmp_path / "cfg", tmp_path / "data")
        engine.extensoes.acoes.registrar(Produto().tipo())
        assert criar(engine)["allowed"] and criar(engine)["allowed"]
        assert "Limite" in criar(engine)["reason"]

    def test_nome_de_tipo_e_esquema_estrito(self, amb) -> None:
        class Frouxo(BaseModel):
            x: int = 0

        with pytest.raises(ValueError):
            amb.extensoes.acoes.registrar(amb.produto.tipo("sem_modulo"))
        with pytest.raises(ValueError, match="extra"):
            tipo = amb.produto.tipo("teste.frouxo")
            amb.extensoes.acoes.registrar(TipoAcao(**{**tipo.__dict__, "esquema": Frouxo}))


class TestConfirmacao:
    def test_fluxo_completo_executa_com_quem_confirmou(self, amb) -> None:
        r = criar(amb, quem=SEC2)
        pagina = amb.acoes.abrir(SEC, r["acao_id"])
        assert pagina["voce"]["identidade"] == SEC.id
        assert pagina["pedido_por"] == SEC2.id and pagina["requer"] == "pin"
        assert pagina["itens"][0]["campos_faltantes"][0]["nome"] == "documento"

        with pytest.raises(AcaoErro) as faltando:
            amb.acoes.confirmar(SEC, r["acao_id"], PIN_SEC)
        assert faltando.value.status == 422

        saida = amb.acoes.confirmar(
            SEC, r["acao_id"], PIN_SEC, [{"item": 0, "nome": "documento", "valor": "123"}]
        )
        assert saida["status"] == "confirmada"
        assert amb.produto.execucoes == [{"ator": SEC.id, "papel": "viewer", "acao": r["acao_id"]}]
        assert amb.produto.escritas[0]["ator"] == SEC.id and amb.produto.escritas[0]["trace_id"]

    def test_uso_unico(self, amb) -> None:
        amb.produto.campos["R1"] = {"documento": "1"}
        r = criar(amb)
        amb.acoes.confirmar(SEC, r["acao_id"], PIN_SEC)
        with pytest.raises(AcaoErro) as exc:
            amb.acoes.confirmar(GESTOR, r["acao_id"], PIN_GESTOR)
        assert exc.value.status == 409
        assert len(amb.produto.execucoes) == 1

    def test_expiracao(self, amb) -> None:
        amb.produto.campos["R1"] = {"documento": "1"}
        r = criar(amb)
        amb.acoes._agora = lambda: datetime.now(UTC) + timedelta(minutes=31)
        with pytest.raises(AcaoErro) as exc:
            amb.acoes.confirmar(SEC, r["acao_id"], PIN_SEC)
        assert exc.value.status == 410
        assert amb.acoes.store.get(r["acao_id"]).status == "expirada"
        assert amb.produto.execucoes == []
        assert [e.action for e in eventos(amb)].count("acao:expirada") == 1

    def test_pedido_adulterado_nao_executa(self, amb, tmp_path) -> None:
        amb.produto.campos["R1"] = {"documento": "1"}
        amb.produto.campos["R9"] = {"documento": "1"}
        r = criar(amb)
        con = sqlite3.connect(tmp_path / "data" / "cli" / "state.db")
        itens = json.loads(con.execute("SELECT itens FROM acao").fetchone()[0])
        itens[0]["argumentos"]["registro"] = "R9"
        con.execute("UPDATE acao SET itens = ?", (json.dumps(itens),))
        con.commit()
        con.close()
        with pytest.raises(AcaoErro) as exc:
            amb.acoes.confirmar(SEC, r["acao_id"], PIN_SEC)
        assert exc.value.status == 409 and "alterado" in exc.value.motivo
        assert amb.acoes.store.get(r["acao_id"]).status == "falhou"
        assert amb.produto.execucoes == []

    def test_pin_errado_e_bloqueio(self, amb) -> None:
        amb.produto.campos["R1"] = {"documento": "1"}
        r = criar(amb)
        for restantes in (4, 3, 2, 1):
            with pytest.raises(AcaoErro) as exc:
                amb.acoes.confirmar(SEC, r["acao_id"], "0000")
            assert exc.value.status == 401
            assert exc.value.extra["tentativas_restantes"] == restantes
        with pytest.raises(AcaoErro) as exc:
            amb.acoes.confirmar(SEC, r["acao_id"], "0000")
        assert exc.value.status == 423
        with pytest.raises(AcaoErro) as exc:
            amb.acoes.confirmar(SEC, r["acao_id"], PIN_SEC)
        assert exc.value.status == 423
        assert amb.produto.execucoes == []
        assert any(e.action == "acao:pin_bloqueado" for e in eventos(amb))

    def test_sem_pin_definido(self, amb) -> None:
        r = criar(amb)
        with pytest.raises(AcaoErro) as exc:
            amb.acoes.confirmar(SEC2, r["acao_id"], "1357")
        assert exc.value.status == 428

    def test_pin_guardado_so_como_hash(self, amb, tmp_path) -> None:
        con = sqlite3.connect(tmp_path / "data" / "cli" / "state.db")
        hashes = [h for (h,) in con.execute("SELECT pin_hash FROM atributos_usuario")]
        con.close()
        assert all(h.startswith("scrypt$") for h in hashes)
        assert not any(PIN_SEC in h for h in hashes)

    @pytest.mark.parametrize("pin", ["12", "1111", "1234", "9876", "12a4", "123456789"])
    def test_pin_fraco(self, pin) -> None:
        with pytest.raises(PinInvalido):
            validar_formato_pin(pin)

    def test_lote_com_falha_para_no_item_que_falhou(self, amb) -> None:
        for reg in ("A", "B", "C"):
            amb.produto.campos[reg] = {"documento": "1"}
        token = set_current_user(SEC.perfil)
        try:
            r = amb.acoes.criar(
                SEC,
                [
                    {"tipo": "teste.registrar", "argumentos": {"registro": "A"}},
                    {"tipo": "teste.registrar", "argumentos": {"registro": "B", "falhar": True}},
                    {"tipo": "teste.registrar", "argumentos": {"registro": "C"}},
                ],
            )
        finally:
            current_user_var.reset(token)
        assert r["resumo"].startswith("3 itens")
        saida = amb.acoes.confirmar(SEC, r["acao_id"], PIN_SEC)
        assert saida["status"] == "falhou"
        assert [i["ok"] for i in saida["resultado"]] == [True, False, False]
        assert saida["resultado"][2]["resumo"] == "não executado"
        assert len(amb.produto.execucoes) == 1

    def test_lote_inteiro(self, amb) -> None:
        for reg in ("A", "B"):
            amb.produto.campos[reg] = {"documento": "1"}
        token = set_current_user(SEC.perfil)
        try:
            r = amb.acoes.criar(
                SEC, [{"tipo": "teste.registrar", "argumentos": {"registro": x}} for x in "AB"]
            )
        finally:
            current_user_var.reset(token)
        assert amb.acoes.confirmar(SEC, r["acao_id"], PIN_SEC)["status"] == "confirmada"
        assert len(amb.produto.execucoes) == 2

    def test_campo_nao_editavel_ou_invalido(self, amb) -> None:
        r = criar(amb)
        with pytest.raises(AcaoErro) as exc:
            amb.acoes.gravar_campos(SEC, r["acao_id"], PIN_SEC, [{"nome": "x", "valor": "1"}])
        assert exc.value.status == 422
        with pytest.raises(AcaoErro) as exc:
            amb.acoes.gravar_campos(
                SEC, r["acao_id"], PIN_SEC, [{"nome": "documento", "valor": "invalido"}]
            )
        assert exc.value.motivo == "Documento inválido."


class TestSensivel:
    def test_confirmar_simples_nao_vale_para_sensivel(self, amb) -> None:
        r = criar(amb, tipo="teste.sensivel")
        assert r["sensivel"]
        with pytest.raises(AcaoErro) as exc:
            amb.acoes.confirmar(SEC, r["acao_id"], PIN_SEC)
        assert exc.value.status == 403

    def test_dados_so_para_o_gestor(self, amb) -> None:
        r = criar(amb, tipo="teste.sensivel")
        assert amb.acoes.abrir(SEC, r["acao_id"])["itens"] == []
        assert amb.acoes.abrir(GESTOR, r["acao_id"])["itens"][0]["resumo"] == "Registrar R1"

    def test_segregacao_quem_pede_nao_libera(self, amb) -> None:
        amb.produto.campos["R1"] = {"documento": "1"}
        r = criar(amb, quem=GESTOR, tipo="teste.sensivel")
        with pytest.raises(AcaoErro) as exc:
            amb.acoes.liberar(GESTOR, r["acao_id"], PIN_GESTOR)
        assert exc.value.status == 403 and "Quem pediu" in exc.value.motivo
        with pytest.raises(AcaoErro) as exc:
            amb.acoes.liberar(SEC, r["acao_id"], PIN_SEC)
        assert exc.value.status == 403
        saida = amb.acoes.liberar(GESTOR2, r["acao_id"], PIN_GESTOR2)
        assert saida["status"] == "confirmada"
        acao = amb.acoes.store.get(r["acao_id"])
        assert acao.liberado_por == GESTOR2.id and acao.metodo == "pin+liberacao"
        assert amb.produto.execucoes[0]["ator"] == GESTOR2.id

    def test_aal2_recente_quando_exigido(self, tmp_path) -> None:
        escrever_cliente(tmp_path, "  exigir_aal2_liberacao: true\n")
        engine = BRMEngine("cli", tmp_path / "cfg", tmp_path / "data")
        produto = Produto()
        produto.campos["R1"] = {"documento": "1"}
        engine.extensoes.acoes.registrar(produto.tipo("teste.sensivel", sensivel=True))
        agora = datetime.now(UTC)
        sem_mfa = ident("g@x", Role.ADMIN, "agenda", aal="aal1", autenticado_em=agora)
        antigo = ident(
            "g@x", Role.ADMIN, "agenda", aal="aal2", autenticado_em=agora - timedelta(hours=1)
        )
        recente = ident("g@x", Role.ADMIN, "agenda", aal="aal2", autenticado_em=agora)
        engine.acoes.atributos.definir_pin("agenda", "g@x", PIN_GESTOR, por="g@x")
        r = criar(engine, tipo="teste.sensivel")
        for quem in (sem_mfa, antigo):
            with pytest.raises(AcaoErro) as exc:
                engine.acoes.liberar(quem, r["acao_id"], PIN_GESTOR)
            assert exc.value.status == 401
        assert engine.acoes.liberar(recente, r["acao_id"], PIN_GESTOR)["status"] == "confirmada"


class TestSkillPelaCentral:
    def test_regra_que_nega_nao_gera_link(self, amb) -> None:
        r = criar(amb, tipo="nucleo.executar_skill", skill="proibida")
        assert not r["allowed"] and r["reason"] == "Proibido pelas regras."

    def test_a2_confirmada_executa_como_quem_confirmou(self, amb) -> None:
        r = criar(
            amb,
            quem=SEC2,
            tipo="nucleo.executar_skill",
            skill="enviar_aviso",
            argumentos={"texto": "oi"},
        )
        assert not r["sensivel"]
        saida = amb.acoes.confirmar(SEC, r["acao_id"], PIN_SEC)
        assert saida["status"] == "confirmada"
        executou = [e for e in amb._audit_sink.iter_events("cli") if e.action == "enviar_aviso"]
        assert executou and executou[-1].actor == SEC.id

    def test_a3_e_require_approval_viram_sensivel(self, amb) -> None:
        a3 = criar(amb, tipo="nucleo.executar_skill", skill="estornar")
        alto = criar(
            amb, tipo="nucleo.executar_skill", skill="valor_alto", argumentos={"valor": 5000}
        )
        baixo = criar(
            amb, tipo="nucleo.executar_skill", skill="valor_alto", argumentos={"valor": 10}
        )
        assert a3["sensivel"] and alto["sensivel"] and not baixo["sensivel"]
        saida = amb.acoes.liberar(GESTOR, alto["acao_id"], PIN_GESTOR)
        assert saida["status"] == "confirmada", saida

    def test_status_da_acao_para_o_chat(self, amb) -> None:
        r = criar(amb, tipo="nucleo.executar_skill", skill="enviar_aviso")
        token = set_current_user(SEC.perfil)
        try:
            assert amb.status_da_acao(r["acao_id"])["status"] == "pendente"
        finally:
            current_user_var.reset(token)
        amb.acoes.confirmar(SEC, r["acao_id"], PIN_SEC)
        status = amb.acoes.status(SEC, r["acao_id"])
        assert status["status"] == "confirmada" and status["confirmado_por"] == SEC.id
        assert status["resultado"] == [{"item": 0, "ok": True, "resumo": "Executada."}]
        assert amb.acoes.status(SEC2, r["acao_id"])["allowed"] is False


class TestTrilha:
    def test_trilha_costurada_pelo_id(self, amb) -> None:
        amb.produto.campos["R1"] = {"documento": "1"}
        r = criar(amb, quem=SEC2)
        amb.acoes.abrir(SEC, r["acao_id"])
        amb.acoes.confirmar(SEC, r["acao_id"], PIN_SEC)
        da_acao = [e for e in eventos(amb) if e.details.get("acao_id") == r["acao_id"]]
        assert [e.action for e in da_acao] == [
            "acao:criada",
            "acao:aberta",
            "acao:execucao_iniciada",
            "acao:confirmada",
        ]
        assert da_acao[0].actor == SEC2.id
        assert {e.actor for e in da_acao[1:]} == {SEC.id}
        inicio = da_acao[2].details
        assert inicio["metodo"] == "pin" and inicio["provedor"] == "local"
        assert da_acao[3].details["resultado"][0]["registro_id"] == "r-1"

    def test_escrita_falha_fechado_sem_auditoria(self, amb) -> None:
        amb.produto.campos["R1"] = {"documento": "1"}
        r = criar(amb)

        def quebra(*a, **k):
            raise OSError("disco cheio")

        amb.acoes._auditar = quebra
        with pytest.raises(OSError):
            amb.acoes.confirmar(SEC, r["acao_id"], PIN_SEC)
        assert amb.produto.execucoes == []
        assert amb.acoes.store.get(r["acao_id"]).status == "pendente"


class TestAPI:
    @pytest.fixture
    def client(self, amb) -> TestClient:
        amb.user_store.create_user(SEC.id, Role.VIEWER, api_token="tok-sec")
        amb.user_store.create_user(GESTOR.id, Role.ADMIN, api_token="tok-gestor")
        return TestClient(create_app(engine=amb))

    def test_rotas(self, amb, client) -> None:
        h = {"Authorization": "Bearer tok-sec"}
        r = criar(amb)
        acao = r["acao_id"]
        assert client.get(f"/api/central/acoes/{acao}").status_code == 401
        pagina = client.get(f"/api/central/acoes/{acao}", headers=h).json()
        assert pagina["itens"][0]["campos_faltantes"][0]["editavel"] is True
        ruim = client.post(f"/api/central/acoes/{acao}/confirmar", headers=h, json={"pin": "0000"})
        assert ruim.status_code == 401
        assert ruim.json()["detail"]["tentativas_restantes"] == 4
        ok = client.post(
            f"/api/central/acoes/{acao}/campos",
            headers=h,
            json={"pin": PIN_SEC, "campos": [{"nome": "documento", "valor": "9"}]},
        )
        assert ok.status_code == 200 and ok.json()["gravados"] == ["0.documento"]
        feito = client.post(
            f"/api/central/acoes/{acao}/confirmar", headers=h, json={"pin": PIN_SEC}
        )
        assert feito.status_code == 200 and feito.json()["status"] == "confirmada"
        de_novo = client.post(
            f"/api/central/acoes/{acao}/confirmar", headers=h, json={"pin": PIN_SEC}
        )
        assert de_novo.status_code == 409
        assert client.get("/api/central/acoes/" + "0" * 32, headers=h).status_code == 404

    def test_pin_pela_api(self, amb, client) -> None:
        h = {"Authorization": "Bearer tok-sec"}
        assert client.get("/api/central/eu", headers=h).json()["pin_definido"] is True
        sem_atual = client.put("/api/central/eu/pin", headers=h, json={"pin_novo": "8642"})
        assert sem_atual.status_code == 401
        fraco = client.put(
            "/api/central/eu/pin", headers=h, json={"pin_novo": "1234", "pin_atual": PIN_SEC}
        )
        assert fraco.status_code == 422
        troca = client.put(
            "/api/central/eu/pin", headers=h, json={"pin_novo": "8642", "pin_atual": PIN_SEC}
        )
        assert troca.status_code == 200
        g = {"Authorization": "Bearer tok-gestor"}
        assert (
            client.delete(f"/api/central/usuarios/local/{SEC.id}/pin", headers=h).status_code == 403
        )
        assert (
            client.delete(f"/api/central/usuarios/local/{SEC.id}/pin", headers=g).status_code == 200
        )
        assert (
            client.delete(f"/api/central/usuarios/local/{GESTOR.id}/pin", headers=g).status_code
            == 403
        )
        unidades = client.put(
            "/api/central/usuarios/agenda/u-1/unidades",
            headers=g,
            json={"unidades": ["Centro", "Infantil"]},
        )
        assert unidades.json() == {"unidades": ["Centro", "Infantil"]}

    def test_nome_de_exibicao_e_lista_de_pessoas(self, amb, client) -> None:
        h = {"Authorization": "Bearer tok-sec"}
        g = {"Authorization": "Bearer tok-gestor"}
        amb.acoes.atributos.definir_nome("local", SEC.id, "  Ana   Souza ", por="teste")
        assert client.get("/api/central/eu", headers=h).json()["nome"] == "Ana Souza"
        acao = criar(amb)["acao_id"]
        assert client.get(f"/api/central/acoes/{acao}", headers=h).json()["voce"]["nome"] == (
            "Ana Souza"
        )
        client.post(f"/api/central/acoes/{acao}/confirmar", headers=h, json={"pin": PIN_SEC})
        assert any(e.details.get("nome") == "Ana Souza" for e in eventos(amb))
        assert client.get("/api/central/pessoas", headers=h).status_code == 403
        pessoas = client.get("/api/central/pessoas", headers=g).json()["pessoas"]
        ana = next(p for p in pessoas if p["usuario_id"] == SEC.id)
        assert ana["nome"] == "Ana Souza" and ana["papel"] == "viewer" and ana["pin_definido"]
        with pytest.raises(ValueError):
            amb.acoes.atributos.definir_nome("local", SEC.id, "x", por="teste")

    def test_cancelar(self, amb, client) -> None:
        r = criar(amb)
        h = {"Authorization": "Bearer tok-sec"}
        assert (
            client.post(f"/api/central/acoes/{r['acao_id']}/cancelar", headers=h).json()["status"]
            == "cancelada"
        )


class TestToolsMCP:
    def test_tools_do_chat_criam_link_e_consultam_status(self, amb) -> None:
        import asyncio

        from mcp_engine.core.mcp_server import build_server

        amb.user_store.create_user(SEC.id, Role.VIEWER, api_token="tok-sec")
        server = build_server(amb)
        nomes = {t.name for t in asyncio.run(server.list_tools())}
        assert {"criar_acao", "criar_lote_acoes", "status_da_acao", "tipos_de_acao"} <= nomes

        set_current_user(SEC.perfil)

        async def chat() -> tuple[Any, Any]:
            criada = await server.call_tool(
                "criar_acao",
                {
                    "tipo": "nucleo.executar_skill",
                    "argumentos": {"skill": "enviar_aviso", "argumentos": {"texto": "oi"}},
                },
            )
            return criada, None

        criada, _ = asyncio.run(chat())
        dados = criada[1] if isinstance(criada, tuple) else criada
        dados = dados.get("result", dados) if isinstance(dados, dict) else dados
        assert dados["link"].startswith("https://cli.brm.test/acao/") and "?" not in dados["link"]
        status = asyncio.run(server.call_tool("status_da_acao", {"acao_id": dados["acao_id"]}))
        status = status[1] if isinstance(status, tuple) else status
        assert status["status"] == "pendente"
        assert [e for e in eventos(amb) if e.action == "acao:criada" and e.actor == SEC.id]

    def test_tool_nao_executa_nada(self, amb) -> None:
        r = criar(amb, tipo="nucleo.executar_skill", skill="enviar_aviso")
        assert r["status"] == "pendente"
        feitos = [e for e in amb._audit_sink.iter_events("cli") if e.action == "enviar_aviso"]
        assert feitos == []
