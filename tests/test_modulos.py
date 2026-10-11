import importlib.util
import logging
import sqlite3
from pathlib import Path

import httpx
import pytest
import yaml

from mcp_engine import modules as modulos
from mcp_engine.core.config_loader import ConfigLoadError
from mcp_engine.core.executor import SecretStore, emitir_chamada, run_http
from mcp_engine.core.mcp_server import BRMEngine, build_server
from mcp_engine.core.models import SkillDefinition
from mcp_engine.modules import BaseModule, ModuleError, ModuleHost

ROOT = Path(__file__).resolve().parent.parent

CONFIG = """\
client_id: {cid}
block_private_ip: true
{extra}domain_allowlist:
  - api.example.com
"""

MOCK_SKILLS = """\
schema_version: 1
client_id: {cid}
skills:
  - name: consultar
    description: consulta mock
    method: GET
    url_template: https://api.example.com/x
    allowed_domains: [api.example.com]
"""


def escrever_cliente(
    root: Path, cid: str, extra: str = "", skills: str | None = None, allowlist: str = ""
) -> None:
    cfg = root / cid / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "rules.yaml").write_text(f"schema_version: 1\nclient_id: {cid}\nrules: []\n")
    (cfg / "skills.yaml").write_text(skills or MOCK_SKILLS.format(cid=cid))
    (cfg / "client_config.yaml").write_text(CONFIG.format(cid=cid, extra=extra) + allowlist)


def skills_com(cid: str, *entradas: str) -> str:
    return f"schema_version: 1\nclient_id: {cid}\nskills:\n" + "".join(entradas)


def ferramentas(engine: BRMEngine) -> set[str]:
    return {t.name for t in build_server(engine)._tool_manager.list_tools()}


def novo_motor(root: Path, cid: str) -> BRMEngine:
    return BRMEngine(client_id=cid, config_root=root / "cfg", data_root=root / "data")


@pytest.fixture
def raiz(tmp_path: Path) -> Path:
    (tmp_path / "cfg").mkdir()
    return tmp_path


def modulo_dir(base: Path, nome: str, extra_manifest: str = "", skills: str = "") -> Path:
    d = base / nome
    d.mkdir(parents=True, exist_ok=True)
    nomes = []
    if skills:
        nomes = [s["name"] for s in yaml.safe_load(skills)["skills"]]
        (d / "skills.yaml").write_text(skills)
    (d / "module.yaml").write_text(
        f"name: {nome}\nversion: '1'\napi_version: 1\ncompartilhavel: true\n"
        f"skills: {nomes}\n{extra_manifest}"
    )
    return d


class ModuloExemplo(BaseModule):
    def install(self, host: ModuleHost) -> None:
        @host.handlers.register("ping")
        def ping(ctx, args):  # noqa: ANN001
            return {"pong": True, "db": str(host.db_path)}

        self.host = host


@pytest.fixture
def exemplo(tmp_path: Path):
    d = modulo_dir(tmp_path / "mods", "exemplo")
    (d / "migrations").mkdir()
    (d / "migrations" / "001.sql").write_text("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    mod = ModuloExemplo(d)
    modulos.register_module(mod)
    yield mod
    modulos.unregister_module("exemplo")


class TestContrato:
    def test_cliente_sem_modulos_inicia_e_governa(self, raiz: Path) -> None:
        escrever_cliente(raiz / "cfg", "a")
        engine = novo_motor(raiz, "a")
        assert engine.module_tools == {}
        assert engine.call_skill("consultar", {})["allowed"] is True

    def test_modulo_registra_handler_com_namespace_e_banco_proprio(
        self, raiz: Path, exemplo: BaseModule
    ) -> None:
        escrever_cliente(raiz / "cfg", "a", extra="modules: [exemplo]\n")
        engine = novo_motor(raiz, "a")
        assert engine.handlers.names() == ["exemplo.ping"]
        db = raiz / "data" / "a" / "exemplo.sqlite"
        assert db.is_file()
        assert sqlite3.connect(db).execute("SELECT count(*) FROM t").fetchone() == (0,)

    def test_migracao_e_idempotente(self, raiz: Path, exemplo: BaseModule) -> None:
        escrever_cliente(raiz / "cfg", "a", extra="modules: [exemplo]\n")
        novo_motor(raiz, "a")
        engine = novo_motor(raiz, "a")
        assert engine.handlers.names() == ["exemplo.ping"]

    def test_modulo_nao_enxerga_state_nem_user_store(self, raiz: Path, exemplo: BaseModule) -> None:
        escrever_cliente(raiz / "cfg", "a", extra="modules: [exemplo]\n")
        novo_motor(raiz, "a")
        host = exemplo.host
        campos = set(vars(host))
        assert not {"user_store", "state", "operacoes", "engine"} & campos
        assert host.db_path.name == "exemplo.sqlite"

    def test_handler_nao_aceita_nome_com_ponto(self, raiz: Path, exemplo: BaseModule) -> None:
        escrever_cliente(raiz / "cfg", "a", extra="modules: [exemplo]\n")
        engine = novo_motor(raiz, "a")
        with pytest.raises(ModuleError):
            exemplo.host.handlers.register("outro.x", lambda c, a: {})
        assert engine.handlers.names() == ["exemplo.ping"]

    def test_api_version_incompativel_e_recusada(self, tmp_path: Path) -> None:
        d = modulo_dir(tmp_path / "mods", "velho")
        (d / "module.yaml").write_text(
            "name: velho\nversion: '1'\napi_version: 99\ncompartilhavel: true\n"
        )
        with pytest.raises(ModuleError, match="api_version"):
            modulos.register_module(BaseModule(d))

    def test_modulo_desconhecido_falha_na_carga(self, raiz: Path) -> None:
        escrever_cliente(raiz / "cfg", "a", extra="modules: [inexistente]\n")
        with pytest.raises(ModuleError, match="desconhecido"):
            novo_motor(raiz, "a")

    def test_catalogo_recusa_modulo_nao_compartilhavel(self, tmp_path: Path) -> None:
        d = modulo_dir(tmp_path / "mods", "fechado")
        (d / "module.yaml").write_text(
            "name: fechado\nversion: '1'\napi_version: 1\ncompartilhavel: false\n"
        )
        modulos.register_module(BaseModule(d))
        try:
            with pytest.raises(ModuleError, match="compartilh"):
                modulos.get_catalog_module("fechado")
        finally:
            modulos.unregister_module("fechado")

    def test_registro_nao_substitui_modulo_do_catalogo(self, tmp_path: Path) -> None:
        d = modulo_dir(tmp_path / "mods", "datajud")
        with pytest.raises(ModuleError, match="reservado"):
            modulos.register_module(BaseModule(d))

    def test_manifesto_diverge_do_skills_yaml(self, tmp_path: Path) -> None:
        d = modulo_dir(tmp_path / "mods", "torto")
        (d / "module.yaml").write_text(
            "name: torto\nversion: '1'\napi_version: 1\ncompartilhavel: true\nskills: [x]\n"
        )
        with pytest.raises(ModuleError, match="divergem"):
            BaseModule(d)


class TestDatajud:
    def test_tool_so_existe_com_modulo_habilitado(self, raiz: Path) -> None:
        escrever_cliente(raiz / "cfg", "com", extra="modules: [datajud]\n")
        escrever_cliente(raiz / "cfg", "sem")
        assert "consultar_processo_datajud" in ferramentas(novo_motor(raiz, "com"))
        assert "consultar_processo_datajud" not in ferramentas(novo_motor(raiz, "sem"))

    def test_cliente_example_habilita_datajud(self, tmp_path: Path) -> None:
        engine = BRMEngine("example", ROOT / "clients", tmp_path / "data")
        assert "consultar_processo_datajud" in ferramentas(engine)


def skill_resend(*linhas: str) -> str:
    return skills_com("a", "  - usar: resend.enviar_email\n", *linhas)


ALLOW_RESEND = "  - api.resend.com\n"


class TestCapacidades:
    def test_a_adota_resend_b_nao_tem(self, raiz: Path) -> None:
        escrever_cliente(
            raiz / "cfg", "a", "modules: [resend]\n", skill_resend(), allowlist=ALLOW_RESEND
        )
        escrever_cliente(raiz / "cfg", "b")
        assert "enviar_email" in ferramentas(novo_motor(raiz, "a"))
        assert "enviar_email" not in ferramentas(novo_motor(raiz, "b"))

    def test_b_nao_consegue_adotar_sem_habilitar(self, raiz: Path) -> None:
        escrever_cliente(
            raiz / "cfg", "b", skills=skill_resend().replace("client_id: a", "client_id: b")
        )
        with pytest.raises(ConfigLoadError, match="não está habilitado"):
            novo_motor(raiz, "b")

    def test_habilitar_sem_adotar_nao_cria_tool(self, raiz: Path) -> None:
        escrever_cliente(raiz / "cfg", "a", "modules: [resend]\n", allowlist=ALLOW_RESEND)
        assert "enviar_email" not in ferramentas(novo_motor(raiz, "a"))

    def test_referencia_inexistente_falha_na_carga(self, raiz: Path) -> None:
        escrever_cliente(
            raiz / "cfg",
            "a",
            "modules: [resend]\n",
            skills_com("a", "  - usar: resend.nao_existe\n"),
            ALLOW_RESEND,
        )
        with pytest.raises(ConfigLoadError, match="inexistente"):
            novo_motor(raiz, "a")

    def test_sobrescrita_valida_aplica(self, raiz: Path) -> None:
        escrever_cliente(
            raiz / "cfg",
            "a",
            "modules: [resend]\n",
            skill_resend(
                "    autonomia: A3\n",
                "    resumo: Aviso para {to}\n",
                "    executor: {timeout_s: 3}\n",
            ),
            ALLOW_RESEND,
        )
        skill = next(s for s in novo_motor(raiz, "a").skills if s.name == "enviar_email")
        assert skill.autonomia == "A3"
        assert skill.resumo == "Aviso para {to}"
        assert skill.executor.timeout_s == 3
        assert skill.executor.type == "http"

    @pytest.mark.parametrize(
        "linha",
        [
            "    allowed_domains: [evil.example.com]\n",
            "    url_template: https://evil.example.com/x\n",
            "    method: GET\n",
            "    name: outro\n",
            "    executor: {headers: {X: y}}\n",
            "    executor: {type: mock}\n",
            "    irreversivel: false\n",
        ],
    )
    def test_sobrescrita_proibida_e_recusada(self, raiz: Path, linha: str) -> None:
        escrever_cliente(
            raiz / "cfg", "a", "modules: [resend]\n", skill_resend(linha), ALLOW_RESEND
        )
        with pytest.raises(ConfigLoadError, match="sobrescrita|sobrescrito"):
            novo_motor(raiz, "a")

    def test_autonomia_baixa_em_skill_irreversivel_e_recusada(self, raiz: Path) -> None:
        escrever_cliente(
            raiz / "cfg",
            "a",
            "modules: [resend]\n",
            skill_resend("    autonomia: A0\n"),
            ALLOW_RESEND,
        )
        with pytest.raises(ConfigLoadError):
            novo_motor(raiz, "a")

    def test_nome_duplicado_apos_resolver_e_recusado(self, raiz: Path) -> None:
        skills = skills_com(
            "a",
            "  - usar: resend.enviar_email\n",
            "  - name: enviar_email\n    description: x\n    method: GET\n"
            "    url_template: https://api.resend.com/x\n    allowed_domains: [api.resend.com]\n",
        )
        escrever_cliente(raiz / "cfg", "a", "modules: [resend]\n", skills, ALLOW_RESEND)
        with pytest.raises(ConfigLoadError, match="duplicado"):
            novo_motor(raiz, "a")

    def test_dominio_do_modulo_fora_da_allowlist_falha_na_subida(self, raiz: Path) -> None:
        escrever_cliente(raiz / "cfg", "a", "modules: [resend]\n")
        with pytest.raises(ModuleError, match="allowlist"):
            novo_motor(raiz, "a")

    def test_segredo_requerido_ausente_gera_aviso(
        self, raiz: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BRM_SECRETS_DIR", str(raiz / "vazio"))
        escrever_cliente(raiz / "cfg", "a", "modules: [resend]\n", allowlist=ALLOW_RESEND)
        with caplog.at_level(logging.WARNING):
            novo_motor(raiz, "a")
        assert "resend:resend_api_key" in caplog.text


class TestPrivados:
    def _privado(self, raiz: Path, cid: str, nome: str = "nfse", python: bool = False) -> None:
        d = modulo_dir(
            raiz / "cfg" / cid / "modules",
            nome,
            skills=(
                "skills:\n- name: emitir_nota\n  description: emite\n"
                "  executor: {type: native, handler: " + nome + ".emitir}\n"
                "  autonomia: A2\n  irreversivel: true\n"
            ),
        )
        (d / "module.yaml").write_text(
            (d / "module.yaml").read_text().replace("compartilhavel: true", "compartilhavel: false")
        )
        if python:
            (d / "module.py").write_text(
                "from pathlib import Path\n"
                "from mcp_engine.modules import BaseModule\n\n"
                "class M(BaseModule):\n"
                "    def install(self, host):\n"
                "        host.handlers.register('emitir', lambda c, a: {'ok': True})\n\n"
                "MODULE = M(Path(__file__).parent)\n"
            )

    def test_privado_de_a_carrega_so_em_a(self, raiz: Path) -> None:
        self._privado(raiz, "a", python=True)
        adotar = skills_com("a", "  - usar: nfse.emitir_nota\n")
        escrever_cliente(raiz / "cfg", "a", "modulos_privados: [nfse]\n", adotar)
        escrever_cliente(raiz / "cfg", "b", "modulos_privados: [nfse]\n")
        engine = novo_motor(raiz, "a")
        assert engine.handlers.names() == ["nfse.emitir"]
        assert "emitir_nota" in ferramentas(engine)
        with pytest.raises(ModuleError, match="não encontrado"):
            novo_motor(raiz, "b")

    def test_privado_nao_listado_nao_carrega(self, raiz: Path) -> None:
        self._privado(raiz, "a", python=True)
        escrever_cliente(raiz / "cfg", "a")
        assert novo_motor(raiz, "a").handlers.names() == []

    def test_privado_declarativo_sem_python(self, raiz: Path) -> None:
        self._privado(raiz, "a")
        escrever_cliente(
            raiz / "cfg",
            "a",
            "modulos_privados: [nfse]\n",
            skills_com("a", "  - usar: nfse.emitir_nota\n"),
        )
        assert "emitir_nota" in ferramentas(novo_motor(raiz, "a"))

    @pytest.mark.parametrize("nome", ["../b", "a/../../x", "..", "Nfse", "n f"])
    def test_nome_com_travessia_e_recusado(self, raiz: Path, nome: str) -> None:
        escrever_cliente(raiz / "cfg", "a", f"modulos_privados: ['{nome}']\n")
        with pytest.raises(ModuleError, match="inválido"):
            novo_motor(raiz, "a")

    def test_privado_nao_pode_colidir_com_catalogo(self, raiz: Path) -> None:
        self._privado(raiz, "a", nome="resend")
        escrever_cliente(raiz / "cfg", "a", "modulos_privados: [resend]\n")
        with pytest.raises(ModuleError, match="colide"):
            novo_motor(raiz, "a")

    def test_symlink_para_fora_e_recusado(self, raiz: Path, tmp_path: Path) -> None:
        self._privado(raiz, "b")
        escrever_cliente(raiz / "cfg", "a", "modulos_privados: [nfse]\n")
        (raiz / "cfg" / "a" / "modules").mkdir()
        (raiz / "cfg" / "a" / "modules" / "nfse").symlink_to(
            raiz / "cfg" / "b" / "modules" / "nfse"
        )
        with pytest.raises(ModuleError, match="não encontrado"):
            novo_motor(raiz, "a")


class TestSegredos:
    def test_segredo_do_cliente_vence_o_global(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRM_SECRET_CHAVE", "global")
        monkeypatch.setenv("BRM_SECRET_CLINICA_PSI_CHAVE", "da-clinica")
        assert SecretStore().for_client("clinica-psi").get("chave") == "da-clinica"
        assert SecretStore().for_client("outro").get("chave") == "global"

    def test_cliente_nao_le_segredo_de_outro(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRM_SECRET_A_CHAVE", "so-do-a")
        from mcp_engine.core.executor import SecretNotFound

        with pytest.raises(SecretNotFound):
            SecretStore().for_client("b").get("chave")

    def test_arquivo_por_cliente_vence_o_global(self, tmp_path: Path) -> None:
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "chave").write_text("do-a\n")
        (tmp_path / "chave").write_text("global\n")
        assert SecretStore(tmp_path).for_client("a").get("chave") == "do-a"
        assert SecretStore(tmp_path).for_client("b").get("chave") == "global"

    @pytest.mark.parametrize("nome", ["../a/chave", "a/chave", "..", ""])
    def test_nome_de_segredo_com_travessia_e_recusado(self, tmp_path: Path, nome: str) -> None:
        from mcp_engine.core.executor import SecretNotFound

        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "chave").write_text("do-a")
        with pytest.raises(SecretNotFound):
            SecretStore(tmp_path).for_client("b").get(nome)

    def test_client_id_invalido_e_recusado(self) -> None:
        with pytest.raises(ValueError):
            SecretStore(client_id="../x")


class TestResendContrato:
    def _skill(self) -> SkillDefinition:
        raw = modulos.get_catalog_module("resend").skills_defs["enviar_email"]
        return SkillDefinition.model_validate(raw)

    def test_corpo_headers_e_remetente(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRM_SECRET_RESEND_API_KEY", "re_teste")
        monkeypatch.setenv("BRM_SECRET_RESEND_REMETENTE", "BRM <no-reply@brm.example>")
        vistos: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            vistos.append(req)
            return httpx.Response(200, json={"id": "abc"})

        skill = self._skill()
        chamada = emitir_chamada(
            skill,
            {"to": "x@y.com", "subject": "Oi", "text": "corpo", "from": "falso@evil.com"},
            "u",
            "admin",
            None,
            idempotency_key="k1",
        )
        out = run_http(
            chamada,
            skill.url_template,
            SecretStore().for_client("a"),
            httpx.Client(transport=httpx.MockTransport(handler)),
        )
        req = vistos[0]
        assert str(req.url) == "https://api.resend.com/emails"
        assert req.headers["Authorization"] == "Bearer re_teste"
        assert req.headers["Idempotency-Key"] == "k1"
        corpo = __import__("json").loads(req.content)
        assert corpo["from"] == "BRM <no-reply@brm.example>"
        assert corpo["to"] == "x@y.com"
        assert out["dados"] == {"id": "abc"}

    def test_skill_e_irreversivel_e_exige_aprovacao(self) -> None:
        skill = self._skill()
        assert skill.irreversivel and skill.autonomia == "A2"


class TestVaultAgnostico:
    def test_defaults_sao_neutros(self) -> None:
        from mcp_engine.core.models import VaultAccess

        acesso = VaultAccess(schema_version=1, client_id="x")
        assert acesso.vinculo_campo == "responsavel"
        assert acesso.identificador_campo == "identificador"
        assert "psic" not in acesso.write_denied_next_step.lower()

    def test_negacao_de_escrita_usa_texto_configurado(self, raiz: Path) -> None:
        from mcp_engine.core.auth import Role, UserProfile, current_user_var, set_current_user

        escrever_cliente(raiz / "cfg", "a")
        (raiz / "cfg" / "a" / "config" / "vault_access.yaml").write_text(
            "schema_version: 1\nclient_id: a\ndefault: deny\n"
            "write_denied_next_step: Fale com a coordenação.\n"
        )
        engine = novo_motor(raiz, "a")
        token = current_user_var.set(None)
        try:
            set_current_user(UserProfile(identity="v@x.com", role=Role.VIEWER))
            out = engine.vault_add_note("t", "c")
        finally:
            current_user_var.reset(token)
        assert out["proximo_passo"] == "Fale com a coordenação."


TERMOS = ["clínica", "clinica", "paciente", "psicólogo", "psicologo", "prontuário", "prontuario"]


@pytest.mark.parametrize("pasta", ["core", "vault"])
def test_nucleo_livre_de_termos_setoriais(pasta: str) -> None:
    achados = []
    for arquivo in (ROOT / "mcp_engine" / pasta).rglob("*.py"):
        texto = arquivo.read_text().lower()
        achados += [f"{arquivo.name}: {t}" for t in TERMOS if t in texto]
    assert achados == []


def test_nucleo_nao_importa_modulos_de_dominio() -> None:
    for arquivo in (ROOT / "mcp_engine" / "core").rglob("*.py"):
        texto = arquivo.read_text()
        assert "modules.datajud" not in texto and "modules.resend" not in texto


def _carregar_script():
    spec = importlib.util.spec_from_file_location(
        "novo_cliente", ROOT / "scripts" / "novo_cliente.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestNovoCliente:
    def test_gera_cliente_que_passa_no_loader_e_inicia(self, tmp_path: Path) -> None:
        script = _carregar_script()
        destino = script.criar_cliente("nova-empresa", [], tmp_path / "clients")
        assert (destino / "config" / "users.db").is_file()
        for pasta in [
            "_modelos",
            "politicas",
            "glossario",
            "procedimentos",
            "cadastros",
            "relatorios",
            "memoria",
        ]:
            assert (destino / "vault" / pasta / "README.md").is_file()
        engine = BRMEngine("nova-empresa", tmp_path / "clients", tmp_path / "data")
        assert engine.skills == ()
        assert engine.rbac_default == "deny"

    def test_com_modulos_ajusta_allowlist_e_valida(self, tmp_path: Path) -> None:
        script = _carregar_script()
        destino = script.criar_cliente("nova-empresa", ["resend", "datajud"], tmp_path / "c")
        cfg = yaml.safe_load((destino / "config" / "client_config.yaml").read_text())
        assert cfg["modules"] == ["resend", "datajud"]
        assert "api.resend.com" in cfg["domain_allowlist"]
        engine = BRMEngine("nova-empresa", tmp_path / "c", tmp_path / "data")
        assert "consultar_processo_datajud" in ferramentas(engine)
        assert "enviar_email" not in ferramentas(engine)

    def test_vault_access_do_template_e_default_deny(self, tmp_path: Path) -> None:
        script = _carregar_script()
        destino = script.criar_cliente("nova-empresa", [], tmp_path / "c")
        acesso = yaml.safe_load((destino / "config" / "vault_access.yaml").read_text())
        assert acesso["default"] == "deny"

    @pytest.mark.parametrize("cid", ["../x", "_template", "A", "a b", "x"])
    def test_id_invalido_e_recusado(self, tmp_path: Path, cid: str) -> None:
        with pytest.raises(ValueError):
            _carregar_script().criar_cliente(cid, [], tmp_path / "c")

    def test_modulo_desconhecido_nao_cria_pasta(self, tmp_path: Path) -> None:
        with pytest.raises(ModuleError):
            _carregar_script().criar_cliente("nova", ["nada"], tmp_path / "c")
        assert not (tmp_path / "c" / "nova").exists()

    def test_nao_sobrescreve_cliente_existente(self, tmp_path: Path) -> None:
        script = _carregar_script()
        script.criar_cliente("nova", [], tmp_path / "c")
        with pytest.raises(FileExistsError):
            script.criar_cliente("nova", [], tmp_path / "c")

    def test_defaults_do_modulo_sao_copiados(self, tmp_path: Path, exemplo: BaseModule) -> None:
        defaults = exemplo.directory / "defaults"
        defaults.mkdir()
        (defaults / "skills.yaml").write_text(
            "skills:\n- name: padrao\n  description: d\n  method: GET\n"
            "  url_template: https://api.example.com/p\n  allowed_domains: [api.example.com]\n"
        )
        exemplo.defaults_dir = defaults
        destino = _carregar_script().criar_cliente("nova", ["exemplo"], tmp_path / "c")
        skills = yaml.safe_load((destino / "config" / "skills.yaml").read_text())
        assert [s["name"] for s in skills["skills"]] == ["padrao"]
