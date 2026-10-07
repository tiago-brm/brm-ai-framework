from datetime import UTC, datetime
from pathlib import Path

import pytest

from mcp_engine.core.auth import Role, UserProfile, current_user_var, set_current_user
from mcp_engine.core.mcp_server import BRMEngine
from mcp_engine.core.models import VaultNote


@pytest.fixture(autouse=True)
def _reset_current_user():
    token = current_user_var.set(None)
    yield
    current_user_var.reset(token)


def _note(note_id: str, **frontmatter) -> VaultNote:
    return VaultNote(
        note_id=note_id,
        client_id="clinica",
        title=f"Título {note_id}",
        content=f"conteúdo de {note_id}",
        frontmatter=frontmatter,
        links=("outra",),
        tags=("t",),
        updated_at=datetime.now(UTC),
        content_hash="h",
    )


NOTES = [
    _note(
        "pront.md",
        paciente_id="PAC-1",
        tipo="prontuario",
        unidade="adultos",
        idade=34,
        psicologo_responsavel="lucas@c.com",
    ),
    _note(
        "reg.md",
        paciente_id="PAC-1",
        tipo="registro-documental",
        psicologo_responsavel="lucas@c.com",
    ),
    _note("aula.md", tipo="aula"),
    _note("faq.md", tipo="faq"),
    _note("novo.md", tipo="tipo-novo"),
]


class FakeVault:
    def __init__(self) -> None:
        self.added: list[str] = []

    def reindex(self, client_id):
        return len(NOTES)

    def list_notes(self, client_id):
        return tuple(NOTES)

    def search(self, client_id, query, limit=10):
        return tuple(NOTES[:limit])

    def get_related(self, client_id, note_id):
        return tuple(NOTES)

    def add_note(self, client_id, title, content, frontmatter=None):
        self.added.append(title)
        return _note("criada.md")


CLIENT_CONFIG = "client_id: clinica\ndomain_allowlist:\n  - s.example.com\n"
SKILLS = """\
schema_version: 1
client_id: clinica
skills:
  - name: buscar_aulas
    description: Pesquisa aulas
    method: GET
    url_template: https://s.example.com/aulas
    allowed_domains: [s.example.com]
    parameters:
      pergunta: { required: true, type: string }
    vault_read: { types: [aula], query_from: pergunta, limit: 2 }
"""
RULES = "schema_version: 1\nclient_id: clinica\nrules: []\n"
ACCESS = """\
schema_version: 1
client_id: clinica
default: deny
policies:
  - id: reg-responsavel
    when: { and: [ { "==": [ { var: note.tipo }, registro-documental ] }, { var: vinculo } ] }
    effect: allow
  - id: pront-vinculo
    when: { and: [ { "==": [ { var: note.tipo }, prontuario ] }, { var: vinculo } ] }
    effect: allow
  - id: rt-audita
    when: { "==": [ { var: actor_role }, super_admin ] }
    effect: allow
    audit: acesso_privilegiado
  - id: ficha
    when: { "==": [ { var: note.tipo }, prontuario ] }
    effect: metadata_only
    expose: [paciente_id, unidade]
  - id: geral
    when: { in: [ { var: note.tipo }, [faq] ] }
    effect: allow
"""


def _engine(tmp_path: Path, access: str | None = ACCESS) -> BRMEngine:
    cfg = tmp_path / "config" / "clinica" / "config"
    cfg.mkdir(parents=True)
    (cfg / "client_config.yaml").write_text(CLIENT_CONFIG)
    (cfg / "rules.yaml").write_text(RULES)
    (cfg / "skills.yaml").write_text(SKILLS)
    if access is not None:
        (cfg / "vault_access.yaml").write_text(access)
    return BRMEngine(
        client_id="clinica",
        config_root=tmp_path / "config",
        data_root=tmp_path / "data",
        vault_provider=FakeVault(),
    )


def _as(email: str, role: Role) -> None:
    set_current_user(UserProfile(identity=email, role=role))


def _ids(result: dict) -> set[str]:
    return {n["note_id"] for n in result["notes"]}


class TestReadPolicies:
    def test_responsible_psychologist_sees_chart_and_documental_record(self, tmp_path):
        engine = _engine(tmp_path)
        _as("lucas@c.com", Role.ADMIN)
        assert _ids(engine.vault_search("x")) == {"pront.md", "reg.md", "faq.md"}

    def test_reception_gets_only_the_id_card(self, tmp_path):
        engine = _engine(tmp_path)
        _as("camila@c.com", Role.VIEWER)
        result = engine.vault_search("x")

        assert _ids(result) == {"pront.md", "faq.md"}
        card = next(n for n in result["notes"] if n["note_id"] == "pront.md")
        assert card["content"] == ""
        assert card["frontmatter"] == {"paciente_id": "PAC-1", "unidade": "adultos"}
        assert card["title"] == "PAC-1"
        assert card["links"] == [] and card["tags"] == []

    def test_other_psychologist_has_no_access_to_content(self, tmp_path):
        engine = _engine(tmp_path)
        _as("wagner@c.com", Role.ADMIN)
        result = engine.vault_search("x")
        card = next(n for n in result["notes"] if n["note_id"] == "pront.md")
        assert card["content"] == ""
        assert "reg.md" not in _ids(result)

    def test_technical_lead_sees_everything_and_is_audited(self, tmp_path):
        engine = _engine(tmp_path)
        _as("helena@c.com", Role.SUPER_ADMIN)
        assert len(engine.vault_search("x")["notes"]) == len(NOTES)

        event = [e for e in engine._audit_sink.iter_events("clinica") if e.action == "vault_search"]
        assert event[-1].details["acesso_privilegiado"]

    def test_unknown_note_type_is_hidden_by_default_deny(self, tmp_path):
        engine = _engine(tmp_path)
        _as("lucas@c.com", Role.ADMIN)
        assert "novo.md" not in _ids(engine.vault_list_notes())
        assert "aula.md" not in _ids(engine.vault_list_notes())

    def test_related_notes_are_filtered_too(self, tmp_path):
        engine = _engine(tmp_path)
        _as("camila@c.com", Role.VIEWER)
        assert "reg.md" not in _ids(engine.vault_get_related("pront.md"))

    def test_search_overfetches_so_hidden_notes_do_not_eat_the_limit(self, tmp_path):
        engine = _engine(tmp_path)
        _as("camila@c.com", Role.VIEWER)
        assert _ids(engine.vault_search("x", limit=2)) == {"pront.md", "faq.md"}

    def test_search_audit_counts_hidden_without_content(self, tmp_path):
        engine = _engine(tmp_path)
        _as("camila@c.com", Role.VIEWER)
        engine.vault_search("x")
        event = [e for e in engine._audit_sink.iter_events("clinica") if e.action == "vault_search"]
        assert event[-1].details["hidden"] == 3
        assert event[-1].actor == "camila@c.com"

    def test_without_policy_file_the_vault_stays_open(self, tmp_path):
        engine = _engine(tmp_path, access=None)
        _as("camila@c.com", Role.VIEWER)
        assert len(engine.vault_search("x")["notes"]) == len(NOTES)


class TestWritePolicy:
    def test_viewer_cannot_add_notes(self, tmp_path):
        engine = _engine(tmp_path)
        _as("camila@c.com", Role.VIEWER)
        result = engine.vault_add_note("t", "c")
        assert result["allowed"] is False
        assert engine._vault_provider.added == []

    def test_admin_can_add_notes(self, tmp_path):
        engine = _engine(tmp_path)
        _as("lucas@c.com", Role.ADMIN)
        engine.vault_add_note("t", "c")
        assert engine._vault_provider.added == ["t"]


class TestSkillMediatedRead:
    def test_viewer_reads_only_declared_types_through_the_skill(self, tmp_path):
        engine = _engine(tmp_path)
        _as("beatriz@c.com", Role.VIEWER)
        assert "aula.md" not in _ids(engine.vault_search("x"))

        result = engine.call_skill("buscar_aulas", {"pergunta": "modos"})
        assert result["allowed"] is True
        assert [n["note_id"] for n in result["vault"]] == ["aula.md"]
