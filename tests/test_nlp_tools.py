import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mcp_engine.core.auth import Role, UserProfile, current_user_var, set_current_user
from mcp_engine.core.mcp_server import BRMEngine, build_server
from mcp_engine.core.models import VaultNote


@pytest.fixture(autouse=True)
def _reset_current_user():
    token = current_user_var.set(None)
    yield
    current_user_var.reset(token)


class FakeVault:
    def __init__(self, notes: list[VaultNote]) -> None:
        self._notes = notes
        self.reindexed = 0

    def reindex(self, client_id: str) -> int:
        self.reindexed += 1
        return len(self._notes)

    def list_notes(self, client_id: str) -> tuple[VaultNote, ...]:
        return tuple(self._notes)

    def search(self, client_id, query, limit=10):
        return ()


def _note(note_id: str, frontmatter: dict) -> VaultNote:
    return VaultNote(
        note_id=note_id,
        client_id="clinica",
        title=note_id,
        content="x",
        frontmatter=frontmatter,
        updated_at=datetime.now(UTC),
        content_hash="h",
    )


CLIENT_CONFIG = """\
client_id: clinica
context: Clínica de psicologia de teste.
domain_allowlist:
  - sistema.example.com
"""

RULES = """\
schema_version: 1
client_id: clinica
rules:
  - id: sem-vinculo
    description: sem vínculo, sem prontuário
    condition:
      and:
        - { "==": [{ "var": "skill_name" }, "consultar_prontuario"] }
        - { "==": [{ "var": "vinculo_com_paciente" }, "nao"] }
    effect: deny
    message: Sem vínculo com este paciente.
    next_step: Peça ao psicólogo responsável.
"""

SKILLS = """\
schema_version: 1
client_id: clinica
skills:
  - name: consultar_prontuario
    description: Abre o prontuário de um paciente.
    method: GET
    url_template: https://sistema.example.com/pacientes/{paciente_id}
    allowed_domains: [sistema.example.com]
    examples:
      - Resuma as últimas sessões do PAC-001
    parameters:
      paciente_id:
        description: ID do paciente, ex. PAC-001
        required: true
        type: string
      vinculo_com_paciente:
        type: string
    derived_facts:
      vinculo_com_paciente:
        vault_match: { paciente_id: $paciente_id, tipo: prontuario }
        field: psicologo_responsavel
        transform: equals_actor
        if_true: sim
        if_false: nao
        if_missing: nao
"""


@pytest.fixture
def engine(tmp_path: Path) -> BRMEngine:
    config_dir = tmp_path / "config" / "clinica" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "client_config.yaml").write_text(CLIENT_CONFIG)
    (config_dir / "rules.yaml").write_text(RULES)
    (config_dir / "skills.yaml").write_text(SKILLS)
    vault = FakeVault(
        [
            _note(
                "pacientes/pac-001.md",
                {
                    "paciente_id": "PAC-001",
                    "tipo": "prontuario",
                    "psicologo_responsavel": "lucas@clinica.com",
                },
            )
        ]
    )
    return BRMEngine(
        client_id="clinica",
        config_root=tmp_path / "config",
        data_root=tmp_path / "data",
        vault_provider=vault,
    )


def _as(email: str, role: Role = Role.ADMIN) -> None:
    set_current_user(UserProfile(identity=email, role=role))


class TestSkillTools:
    def test_each_skill_becomes_a_typed_tool(self, engine: BRMEngine) -> None:
        _as("lucas@clinica.com")
        server = build_server(engine)
        tools = {t.name: t for t in asyncio.run(server.list_tools())}

        tool = tools["consultar_prontuario"]
        assert "Abre o prontuário" in tool.description
        assert "Resuma as últimas sessões" in tool.description
        assert tool.inputSchema["required"] == ["paciente_id"]
        assert "vinculo_com_paciente" not in tool.inputSchema["properties"]
        assert (
            tool.inputSchema["properties"]["paciente_id"]["description"]
            == "ID do paciente, ex. PAC-001"
        )

    def test_server_instructions_carry_client_context(self, engine: BRMEngine) -> None:
        server = build_server(engine)
        assert "Clínica de psicologia de teste." in server.instructions
        assert "vault_search" in server.instructions

    def test_generic_docstrings_are_not_legal_specific(self, engine: BRMEngine) -> None:
        _as("lucas@clinica.com")
        tools = {t.name: t for t in asyncio.run(build_server(engine).list_tools())}
        assert "jurídico" not in tools["vault_search"].description
        assert "jurídico" not in tools["call_skill"].description


class TestDerivedFacts:
    def test_linked_psychologist_is_allowed(self, engine: BRMEngine) -> None:
        _as("lucas@clinica.com")
        result = engine.call_skill("consultar_prontuario", {"paciente_id": "PAC-001"})
        assert result["allowed"] is True

    def test_unlinked_user_is_denied_with_next_step(self, engine: BRMEngine) -> None:
        _as("wagner@clinica.com")
        result = engine.call_skill("consultar_prontuario", {"paciente_id": "PAC-001"})
        assert result["allowed"] is False
        assert result["rule_id"] == "sem-vinculo"
        assert result["proximo_passo"] == "Peça ao psicólogo responsável."

    def test_caller_cannot_claim_a_link(self, engine: BRMEngine) -> None:
        _as("wagner@clinica.com")
        result = engine.call_skill(
            "consultar_prontuario",
            {"paciente_id": "PAC-001", "vinculo_com_paciente": "sim"},
        )
        assert result["allowed"] is False

        events = engine.query_audit_log()["events"]
        assert any(e["action"] == "consultar_prontuario:fato_divergente" for e in events)

    def test_unknown_patient_is_denied(self, engine: BRMEngine) -> None:
        _as("lucas@clinica.com")
        result = engine.call_skill("consultar_prontuario", {"paciente_id": "PAC-999"})
        assert result["allowed"] is False
