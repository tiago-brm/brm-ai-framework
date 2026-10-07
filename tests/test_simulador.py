from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mcp_engine.core.auth import Role
from mcp_engine.core.http_app import create_app
from mcp_engine.core.mcp_server import BRMEngine
from mcp_engine.core.models import VaultNote


class FakeVault:
    def __init__(self, notes: list[VaultNote]) -> None:
        self._notes = notes

    def reindex(self, client_id):
        return len(self._notes)

    def list_notes(self, client_id):
        return tuple(self._notes)

    def search(self, client_id, query, limit=10):
        return tuple(self._notes[:limit])


def _note(note_id: str, **frontmatter) -> VaultNote:
    return VaultNote(
        note_id=note_id,
        client_id="clinica",
        title=note_id,
        content=f"conteúdo {note_id}",
        frontmatter=frontmatter,
        updated_at=datetime.now(UTC),
        content_hash="h",
    )


NOTES = [
    _note(
        "pront.md",
        paciente_id="PAC-1",
        tipo="prontuario",
        psicologo_responsavel="lucas@c.com",
    ),
    _note(
        "reg.md",
        paciente_id="PAC-1",
        tipo="registro-documental",
        psicologo_responsavel="lucas@c.com",
    ),
]

FILES = {
    "client_config.yaml": (
        "client_id: clinica\ndomain_allowlist:\n  - s.example.com\n"
        "dlp_patterns:\n"
        "  - { pattern: '\\d{3}\\.\\d{3}\\.\\d{3}-\\d{2}', label: CPF, action: deny }\n"
    ),
    "rules.yaml": """\
schema_version: 1
client_id: clinica
rules:
  - id: sem-vinculo
    description: x
    condition:
      and:
        - { "==": [{ var: skill_name }, consultar_prontuario] }
        - { "==": [{ var: vinculo_com_paciente }, nao] }
    effect: deny
    message: Sem vínculo.
    next_step: Peça ao responsável.
""",
    "skills.yaml": """\
schema_version: 1
client_id: clinica
skills:
  - name: consultar_prontuario
    description: Abre prontuário.
    method: GET
    url_template: https://s.example.com/p/{paciente_id}
    allowed_domains: [s.example.com]
    parameters:
      paciente_id: { required: true, type: string }
      vinculo_com_paciente: { type: string }
    derived_facts:
      vinculo_com_paciente:
        vault_match: { paciente_id: $paciente_id, tipo: prontuario }
        field: psicologo_responsavel
        transform: equals_actor
        if_true: sim
        if_false: nao
        if_missing: nao
  - name: enviar_email
    description: Envia e-mail.
    method: POST
    url_template: https://s.example.com/mail
    allowed_domains: [s.example.com]
    parameters:
      corpo: { required: true, type: string }
""",
    "vault_access.yaml": """\
schema_version: 1
client_id: clinica
default: deny
policies:
  - id: reg
    when: { and: [ { "==": [ { var: note.tipo }, registro-documental ] }, { var: vinculo } ] }
    effect: allow
  - id: pront
    when: { and: [ { "==": [ { var: note.tipo }, prontuario ] }, { var: vinculo } ] }
    effect: allow
  - id: ficha
    when: { "==": [ { var: note.tipo }, prontuario ] }
    effect: metadata_only
    expose: [paciente_id]
""",
    "demo_scenarios.yaml": """\
schema_version: 1
client_id: clinica
profiles:
  lucas@c.com: Lucas · psicólogo
scenarios:
  - id: abrir
    title: Abrir o prontuário do PAC-1
    skill: consultar_prontuario
    arguments: { paciente_id: PAC-1 }
    profile: lucas@c.com
""",
}


@pytest.fixture
def engine(tmp_path: Path) -> BRMEngine:
    cfg = tmp_path / "config" / "clinica" / "config"
    cfg.mkdir(parents=True)
    for name, text in FILES.items():
        (cfg / name).write_text(text)
    engine = BRMEngine(
        client_id="clinica",
        config_root=tmp_path / "config",
        data_root=tmp_path / "data",
        vault_provider=FakeVault(NOTES),
    )
    engine.user_store.create_user("helena@c.com", Role.SUPER_ADMIN, api_token="helena-tok")
    engine.user_store.create_user("lucas@c.com", Role.ADMIN, api_token="lucas-tok")
    engine.user_store.create_user("camila@c.com", Role.VIEWER, api_token="camila-tok")
    return engine


@pytest.fixture
def client(engine: BRMEngine) -> TestClient:
    return TestClient(create_app(engine=engine))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _run(client: TestClient, perfil: str, skill: str, argumentos: dict) -> dict:
    response = client.post(
        "/api/simulador/executar",
        headers=_auth("helena-tok"),
        json={"perfil_email": perfil, "skill": skill, "argumentos": argumentos},
    )
    assert response.status_code == 200
    return response.json()


class TestAccess:
    @pytest.mark.parametrize("token", ["lucas-tok", "camila-tok", "nenhum"])
    def test_only_super_admin_can_use_the_simulator(self, client: TestClient, token: str) -> None:
        response = client.get("/api/simulador/perfis", headers=_auth(token))
        assert response.status_code == 403

    def test_disabled_simulator_returns_404(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config" / "clinica" / "config"
        cfg.mkdir(parents=True)
        for name, text in FILES.items():
            (cfg / name).write_text(text)
        (cfg / "client_config.yaml").write_text(FILES["client_config.yaml"] + "simulador: false\n")
        engine = BRMEngine(
            client_id="clinica",
            config_root=tmp_path / "config",
            data_root=tmp_path / "data",
            vault_provider=FakeVault(NOTES),
        )
        engine.user_store.create_user("helena@c.com", Role.SUPER_ADMIN, api_token="helena-tok")
        response = TestClient(create_app(engine=engine)).get(
            "/api/simulador/perfis", headers=_auth("helena-tok")
        )
        assert response.status_code == 404


class TestCatalog:
    def test_profiles_carry_labels(self, client: TestClient) -> None:
        body = client.get("/api/simulador/perfis", headers=_auth("helena-tok")).json()
        labels = {p["identity"]: p["rotulo"] for p in body["perfis"]}
        assert labels["lucas@c.com"] == "Lucas · psicólogo"
        assert labels["camila@c.com"] == "camila"

    def test_skills_hide_derived_parameters(self, client: TestClient) -> None:
        body = client.get("/api/simulador/skills", headers=_auth("helena-tok")).json()
        skill = next(s for s in body["skills"] if s["name"] == "consultar_prontuario")
        assert list(skill["parameters"]) == ["paciente_id"]

    def test_scenarios_come_from_the_client_file(self, client: TestClient) -> None:
        body = client.get("/api/simulador/cenarios", headers=_auth("helena-tok")).json()
        assert body["cenarios"][0]["id"] == "abrir"


class TestExecution:
    def test_same_request_gives_different_decisions_per_profile(self, client: TestClient) -> None:
        args = {"paciente_id": "PAC-1"}
        responsavel = _run(client, "lucas@c.com", "consultar_prontuario", args)
        recepcao = _run(client, "camila@c.com", "consultar_prontuario", args)

        assert responsavel["decisao"] == "allowed"
        assert responsavel["fatos_derivados"] == {"vinculo_com_paciente": "sim"}
        assert recepcao["decisao"] == "denied"
        assert recepcao["fatos_derivados"] == {"vinculo_com_paciente": "nao"}
        assert recepcao["resultado"]["proximo_passo"] == "Peça ao responsável."

    def test_session_identity_is_untouched(self, client: TestClient) -> None:
        _run(client, "camila@c.com", "consultar_prontuario", {"paciente_id": "PAC-1"})
        me = client.get("/api/me", headers=_auth("helena-tok")).json()
        assert me["identity"] == "helena@c.com"

    def test_simulation_is_audited_with_both_identities(
        self, client: TestClient, engine: BRMEngine
    ) -> None:
        _run(client, "camila@c.com", "consultar_prontuario", {"paciente_id": "PAC-1"})
        events = [
            e
            for e in engine._audit_sink.iter_events("clinica")
            if e.action.startswith("simulacao:")
        ]
        assert events[-1].details["executado_por"] == "helena@c.com"
        assert events[-1].details["como"] == "camila@c.com"

    def test_audit_records_the_real_person_instead_of_claude(
        self, client: TestClient, engine: BRMEngine
    ) -> None:
        _run(client, "camila@c.com", "consultar_prontuario", {"paciente_id": "PAC-1"})
        eventos = engine._audit_sink.iter_events("clinica")
        acoes = [e for e in eventos if e.action == "consultar_prontuario"]
        assert acoes[-1].actor == "camila@c.com"

    def test_dlp_is_reported(self, client: TestClient) -> None:
        body = _run(client, "lucas@c.com", "enviar_email", {"corpo": "CPF 123.456.789-09"})
        assert body["decisao"] == "denied"
        assert "CPF" in body["dlp"]

    def test_simulation_never_sends_real_email(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import mcp_engine.core.mcp_server as server

        sent: list[str] = []
        monkeypatch.setattr(server, "email_configured", lambda: True)
        monkeypatch.setattr(server, "send_email", lambda **kw: sent.append("x") or {})
        body = _run(client, "lucas@c.com", "enviar_email", {"corpo": "olá"})
        assert body["decisao"] == "allowed"
        assert sent == []

    def test_unknown_profile_is_404(self, client: TestClient) -> None:
        response = client.post(
            "/api/simulador/executar",
            headers=_auth("helena-tok"),
            json={"perfil_email": "x@c.com", "skill": "consultar_prontuario", "argumentos": {}},
        )
        assert response.status_code == 404


class TestVaultAndMatrix:
    def test_vault_view_changes_with_the_profile(self, client: TestClient) -> None:
        def view(perfil: str) -> dict:
            return client.post(
                "/api/simulador/vault",
                headers=_auth("helena-tok"),
                json={"perfil_email": perfil, "consulta": "x"},
            ).json()

        responsavel = view("lucas@c.com")
        recepcao = view("camila@c.com")
        assert {n["modo"] for n in responsavel["notas"]} == {"completa"}
        assert [n["modo"] for n in recepcao["notas"]] == ["so_ficha"]
        assert recepcao["ocultas"] == 1

    def test_matrix_crosses_scenarios_and_profiles_without_auditing(
        self, client: TestClient, engine: BRMEngine
    ) -> None:
        before = len(list(engine._audit_sink.iter_events("clinica")))
        body = client.get("/api/simulador/matriz", headers=_auth("helena-tok")).json()
        after = len(list(engine._audit_sink.iter_events("clinica")))

        row = body["cenarios"][0]
        effects = {c["perfil"]: c["efeito"] for c in row["celulas"]}
        assert effects["lucas@c.com"] == "allow"
        assert effects["camila@c.com"] == "deny"
        assert after == before
