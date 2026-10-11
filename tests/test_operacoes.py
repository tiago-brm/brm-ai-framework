import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mcp_engine.core.auth import Role, UserProfile, current_user_var, set_current_user
from mcp_engine.core.executor import SecretStore
from mcp_engine.core.mcp_server import BRMEngine
from mcp_engine.core.operacoes import (
    AGUARDANDO,
    CANCELADA,
    CONFIRMADA,
    ESTADOS,
    EXECUTADA,
    EXPIRADA,
    FALHOU,
    PROPOSTA,
    REJEITADA,
    TRANSICOES,
    OperacaoStore,
)
from mcp_engine.core.state import StateStore

CONFIG = """\
client_id: example
block_private_ip: true
domain_allowlist:
  - api.example.com
operacao_ttl_horas: 24
"""

RULES = """\
schema_version: 1
client_id: example
rules:
  - id: valor-alto
    description: valor acima de 1000 exige aprovação
    condition: { ">": [{ "var": "valor" }, 1000] }
    effect: require_approval
    message: Valor alto
    next_step: Peça ao gestor
    aprovador: super_admin
"""

SKILLS = """\
schema_version: 1
client_id: example
skills:
  - name: leitura
    description: consulta
    executor: { type: native, handler: t.leitura }
  - name: lancar
    description: Lançar recebimento
    autonomia: A2
    resumo: "Recebimento de R$ {valor} de {paciente}"
    executor: { type: native, handler: t.lancar }
    parameters:
      valor: { type: number, required: true }
      paciente: { type: string, required: true }
  - name: estornar
    description: Estornar lançamento
    autonomia: A3
    irreversivel: true
    executor: { type: native, handler: t.estornar }
    parameters:
      id: { type: string, required: true }
  - name: pequeno_ou_grande
    description: Registra despesa
    autonomia: A1
    a1_condicao: { "<=": [{ "var": "valor" }, 100] }
    executor: { type: native, handler: t.despesa }
    parameters:
      valor: { type: number, required: true }
"""

LUCAS = UserProfile(identity="lucas@c.com", role=Role.VIEWER)
CAMILA = UserProfile(identity="camila@c.com", role=Role.VIEWER)
WAGNER = UserProfile(identity="wagner@c.com", role=Role.ADMIN)
HELENA = UserProfile(identity="helena@c.com", role=Role.SUPER_ADMIN)


@pytest.fixture(autouse=True)
def _reset_user():
    token = current_user_var.set(None)
    yield
    current_user_var.reset(token)


@pytest.fixture
def engine(tmp_path: Path) -> BRMEngine:
    config_dir = tmp_path / "config" / "example" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "rules.yaml").write_text(RULES)
    (config_dir / "skills.yaml").write_text(SKILLS)
    (config_dir / "client_config.yaml").write_text(CONFIG)
    eng = BRMEngine(
        "example", tmp_path / "config", tmp_path / "data", secrets=SecretStore(tmp_path)
    )
    eng.calls = []

    def make(name):
        def handler(ctx, args):
            eng.calls.append((name, ctx.actor, dict(args), ctx.idempotency_key))
            return {"ok": name}

        return handler

    for name in ("leitura", "lancar", "estornar", "despesa"):
        eng.handlers.register(f"t.{name}", make(name))
    return eng


def _events(engine: BRMEngine, action_prefix: str = "operacao:"):
    events = engine._audit_sink.iter_events("example")
    return [e for e in events if e.action.startswith(action_prefix)]


class TestAutonomyLevels:
    def test_a0_runs_directly(self, engine: BRMEngine) -> None:
        set_current_user(CAMILA)
        assert engine.call_skill("leitura", {})["allowed"] is True

    def test_a2_creates_a_proposal_without_executing(self, engine: BRMEngine) -> None:
        set_current_user(CAMILA)
        result = engine.call_skill("lancar", {"valor": 275, "paciente": "Maria"})
        assert result["allowed"] is False
        assert result["status"] == PROPOSTA
        assert result["resumo"] == "Recebimento de R$ 275 de Maria"
        assert engine.calls == []

    def test_a1_within_limit_runs_and_above_limit_asks_confirmation(
        self, engine: BRMEngine
    ) -> None:
        set_current_user(CAMILA)
        assert engine.call_skill("pequeno_ou_grande", {"valor": 50})["allowed"] is True
        grande = engine.call_skill("pequeno_ou_grande", {"valor": 500})
        assert grande["status"] == PROPOSTA
        assert len(engine.calls) == 1

    def test_a3_goes_straight_to_awaiting_approval(self, engine: BRMEngine) -> None:
        set_current_user(CAMILA)
        result = engine.call_skill("estornar", {"id": "x"})
        assert result["status"] == AGUARDANDO

    def test_irreversible_skill_cannot_be_a1(self) -> None:
        from mcp_engine.core.models import SkillDefinition, SkillExecutor

        with pytest.raises(ValueError):
            SkillDefinition(
                name="x",
                description="d",
                autonomia="A1",
                irreversivel=True,
                executor=SkillExecutor(type="native", handler="h"),
            )

    def test_simulation_creates_no_operation(self, engine: BRMEngine) -> None:
        set_current_user(CAMILA)
        result = engine.call_skill("lancar", {"valor": 1, "paciente": "M"}, simulacao=True)
        assert result["allowed"] is True
        assert engine.operacoes.listar() == []


class TestConfirmation:
    def _propose(self, engine):
        set_current_user(CAMILA)
        return engine.call_skill("lancar", {"valor": 275, "paciente": "Maria"})["operacao_id"]

    def test_confirming_executes_once_as_the_proposer(self, engine: BRMEngine) -> None:
        op_id = self._propose(engine)
        result = engine.confirmar_operacao(op_id)
        assert result["allowed"] is True and result["status"] == EXECUTADA
        argumentos = {"valor": 275, "paciente": "Maria"}
        assert engine.calls == [("lancar", "camila@c.com", argumentos, op_id)]
        assert engine.confirmar_operacao(op_id)["allowed"] is False
        assert len(engine.calls) == 1

    def test_someone_else_cannot_confirm(self, engine: BRMEngine) -> None:
        op_id = self._propose(engine)
        set_current_user(LUCAS)
        assert engine.confirmar_operacao(op_id)["allowed"] is False
        assert engine.calls == []

    def test_tampered_arguments_are_not_executed(self, engine: BRMEngine) -> None:
        op_id = self._propose(engine)
        from sqlalchemy import update
        from sqlalchemy.orm import Session

        from mcp_engine.core.state import OperacaoRow

        with Session(engine._state.engine) as s:
            s.execute(
                update(OperacaoRow)
                .where(OperacaoRow.id == op_id)
                .values(argumentos=json.dumps({"valor": 999999, "paciente": "Maria"}))
            )
            s.commit()
        result = engine.confirmar_operacao(op_id)
        assert result["allowed"] is False
        assert engine.calls == []
        assert engine.operacoes.get(op_id).status == "falhou"

    def test_expired_operation_does_not_run(self, engine: BRMEngine) -> None:
        op_id = self._propose(engine)
        from sqlalchemy import update
        from sqlalchemy.orm import Session

        from mcp_engine.core.state import OperacaoRow

        with Session(engine._state.engine) as s:
            s.execute(
                update(OperacaoRow)
                .where(OperacaoRow.id == op_id)
                .values(expira_em=datetime.now(UTC) - timedelta(minutes=1))
            )
            s.commit()
        assert engine.confirmar_operacao(op_id)["allowed"] is False
        assert engine.operacoes.get(op_id).status == EXPIRADA
        assert engine.calls == []

    def test_cancel_by_proposer_only(self, engine: BRMEngine) -> None:
        op_id = self._propose(engine)
        set_current_user(LUCAS)
        assert engine.cancelar_operacao(op_id)["allowed"] is False
        set_current_user(CAMILA)
        assert engine.cancelar_operacao(op_id)["status"] == "cancelada"
        assert engine.confirmar_operacao(op_id)["allowed"] is False

    def test_every_transition_is_audited_with_the_person(self, engine: BRMEngine) -> None:
        op_id = self._propose(engine)
        engine.confirmar_operacao(op_id)
        estados = [(e.action, e.user_email, e.details["operacao_id"]) for e in _events(engine)]
        assert ("operacao:confirmada", "camila@c.com", op_id) in estados
        assert ("operacao:executada", "camila@c.com", op_id) in estados


class TestApproval:
    def _propose(self, engine, valor=5000):
        set_current_user(CAMILA)
        result = engine.call_skill("lancar", {"valor": valor, "paciente": "Maria"})
        return result

    def test_rule_require_approval_creates_a_pending_operation(self, engine: BRMEngine) -> None:
        result = self._propose(engine)
        assert result["reason"] == "human approval required"
        assert result["status"] == AGUARDANDO
        assert result["operacao_id"]
        assert engine.calls == []

    def test_proposer_cannot_approve_own_operation(self, engine: BRMEngine) -> None:
        op_id = self._propose(engine)["operacao_id"]
        set_current_user(HELENA.model_copy(update={"identity": "camila@c.com"}))
        assert engine.aprovar_operacao(op_id)["allowed"] is False
        assert engine.calls == []

    def test_role_below_the_required_one_cannot_approve(self, engine: BRMEngine) -> None:
        op_id = self._propose(engine)["operacao_id"]
        set_current_user(WAGNER)
        denied = engine.aprovar_operacao(op_id)
        assert denied["allowed"] is False
        assert "super_admin" in denied["reason"]

    def test_authorised_other_profile_approves_and_it_runs(self, engine: BRMEngine) -> None:
        op_id = self._propose(engine)["operacao_id"]
        set_current_user(HELENA)
        result = engine.aprovar_operacao(op_id)
        assert result["allowed"] is True and result["status"] == EXECUTADA
        assert engine.calls[0][1] == "camila@c.com"
        assert engine.operacoes.get(op_id).aprovado_por == "helena@c.com"

    def test_reject_records_the_reason_and_never_runs(self, engine: BRMEngine) -> None:
        op_id = self._propose(engine)["operacao_id"]
        set_current_user(HELENA)
        assert engine.rejeitar_operacao(op_id, "valor errado")["status"] == "rejeitada"
        assert engine.operacoes.get(op_id).motivo == "valor errado"
        assert engine.aprovar_operacao(op_id)["allowed"] is False
        assert engine.calls == []

    def test_deny_rules_still_apply_after_approval(self, engine: BRMEngine, tmp_path: Path) -> None:
        op_id = self._propose(engine)["operacao_id"]
        rules = tmp_path / "config" / "example" / "config" / "rules.yaml"
        rules.write_text(
            RULES
            + "  - id: bloqueio\n    description: x\n    condition: { '>': [{var: valor}, 1] }\n"
            "    effect: deny\n    message: bloqueado\n"
        )
        novo = BRMEngine("example", tmp_path / "config", tmp_path / "data")
        novo.handlers.register("t.lancar", lambda ctx, args: {"ok": 1})
        set_current_user(HELENA)
        result = novo.aprovar_operacao(op_id)
        assert result["allowed"] is False
        assert novo.operacoes.get(op_id).status == "falhou"


class TestListingAndRecovery:
    def test_listing_is_scoped_by_person_and_approver_level(self, engine: BRMEngine) -> None:
        set_current_user(CAMILA)
        engine.call_skill("lancar", {"valor": 5000, "paciente": "M"})
        engine.call_skill("lancar", {"valor": 10, "paciente": "M"})

        assert engine.listar_operacoes()["total"] == 2
        set_current_user(LUCAS)
        assert engine.listar_operacoes()["total"] == 0
        set_current_user(WAGNER)
        assert engine.listar_operacoes()["total"] == 0
        set_current_user(HELENA)
        assert engine.listar_operacoes()["total"] == 2

    def test_orphan_confirmed_operation_resumes_without_duplicating(
        self, engine: BRMEngine
    ) -> None:
        set_current_user(CAMILA)
        op_id = engine.call_skill("lancar", {"valor": 10, "paciente": "M"})["operacao_id"]
        engine.operacoes.transicao(op_id, PROPOSTA, CONFIRMADA, confirmado_por="camila@c.com")

        assert engine.retomar_operacoes_orfas() == 1
        assert engine.operacoes.get(op_id).status == EXECUTADA
        assert len(engine.calls) == 1
        assert engine.retomar_operacoes_orfas() == 0

    def test_resuming_after_the_effect_already_happened_does_not_repeat_it(
        self, engine: BRMEngine
    ) -> None:
        set_current_user(CAMILA)
        op_id = engine.call_skill("lancar", {"valor": 10, "paciente": "M"})["operacao_id"]
        engine.confirmar_operacao(op_id)
        assert len(engine.calls) == 1
        from sqlalchemy import update
        from sqlalchemy.orm import Session

        from mcp_engine.core.state import OperacaoRow

        with Session(engine._state.engine) as s:
            s.execute(update(OperacaoRow).where(OperacaoRow.id == op_id).values(status=CONFIRMADA))
            s.commit()
        engine.retomar_operacoes_orfas()
        assert len(engine.calls) == 1
        assert engine.operacoes.get(op_id).status == EXECUTADA


class TestStateMachineProperties:
    @given(
        st.lists(
            st.sampled_from(sorted(ESTADOS)),
            min_size=1,
            max_size=12,
        ),
        st.sampled_from([PROPOSTA, AGUARDANDO]),
    )
    @settings(max_examples=60, deadline=None)
    def test_no_sequence_escapes_the_machine(self, alvos: list[str], inicial: str) -> None:
        terminais = {EXECUTADA, FALHOU, EXPIRADA, CANCELADA, REJEITADA}
        with tempfile.TemporaryDirectory() as tmp:
            store = OperacaoStore(StateStore(Path(tmp), "example"))
            op = store.criar(
                skill="s",
                argumentos={},
                resumo="r",
                status=inicial,
                proposto_por="a",
                proposto_papel="viewer",
                aprovador_papel=None,
                regra_id=None,
                ttl_horas=1,
            )
            historico = [inicial]
            for alvo in alvos:
                atual = store.get(op.id).status
                try:
                    trocou = store.transicao(op.id, atual, alvo)
                except ValueError:
                    assert alvo not in TRANSICOES.get(atual, frozenset())
                    continue
                assert trocou
                historico.append(alvo)

            for anterior, proximo in zip(historico, historico[1:]):
                assert proximo in TRANSICOES[anterior]
                assert anterior not in terminais
                if proximo == EXECUTADA:
                    assert anterior == CONFIRMADA

    def test_execution_requires_passing_through_confirmed(self) -> None:
        for origem, destinos in TRANSICOES.items():
            if EXECUTADA in destinos:
                assert origem == CONFIRMADA

    def test_invalid_transition_is_rejected(self, engine: BRMEngine) -> None:
        set_current_user(CAMILA)
        op_id = engine.call_skill("lancar", {"valor": 10, "paciente": "M"})["operacao_id"]
        with pytest.raises(ValueError):
            engine.operacoes.transicao(op_id, PROPOSTA, EXECUTADA)

    def test_compare_and_swap_prevents_double_transition(self, engine: BRMEngine) -> None:
        set_current_user(CAMILA)
        op_id = engine.call_skill("lancar", {"valor": 10, "paciente": "M"})["operacao_id"]
        assert engine.operacoes.transicao(op_id, PROPOSTA, CONFIRMADA) is True
        assert engine.operacoes.transicao(op_id, PROPOSTA, CONFIRMADA) is False
