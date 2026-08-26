import pytest

from mcp_engine.core.models import Rule, RuleEffect, RuleSet
from mcp_engine.core.rule_evaluator import RuleEvaluationError, evaluate


def _rule(id_: str, condition: dict, effect: RuleEffect, message: str | None = None) -> Rule:
    return Rule(id=id_, description=id_, condition=condition, effect=effect, message=message)


def test_matching_rule_returns_its_effect():
    rule = _rule(
        "max-valor",
        {">": [{"var": "valor"}, 10000]},
        RuleEffect.DENY,
        message="valor acima do limite",
    )
    ruleset = RuleSet(schema_version=1, client_id="acme", rules=(rule,))

    decision = evaluate(ruleset, {"valor": 20000})

    assert decision.effect is RuleEffect.DENY
    assert decision.rule_id == "max-valor"
    assert decision.message == "valor acima do limite"


def test_require_approval_effect():
    rule = _rule(
        "envio-externo",
        {"==": [{"var": "acao"}, "enviar_email"]},
        RuleEffect.REQUIRE_APPROVAL,
    )
    ruleset = RuleSet(schema_version=1, client_id="acme", rules=(rule,))

    decision = evaluate(ruleset, {"acao": "enviar_email"})

    assert decision.effect is RuleEffect.REQUIRE_APPROVAL


def test_first_match_wins():
    first = _rule("bloqueia-tudo", {"==": [1, 1]}, RuleEffect.DENY)
    second = _rule("permite-tudo", {"==": [1, 1]}, RuleEffect.ALLOW)
    ruleset = RuleSet(schema_version=1, client_id="acme", rules=(first, second))

    decision = evaluate(ruleset, {})

    assert decision.rule_id == "bloqueia-tudo"
    assert decision.effect is RuleEffect.DENY


def test_no_rule_matches_defaults_to_allow():
    rule = _rule("max-valor", {">": [{"var": "valor"}, 10000]}, RuleEffect.DENY)
    ruleset = RuleSet(schema_version=1, client_id="acme", rules=(rule,))

    decision = evaluate(ruleset, {"valor": 100})

    assert decision.effect is RuleEffect.ALLOW
    assert decision.rule_id is None


def test_empty_ruleset_defaults_to_allow():
    ruleset = RuleSet(schema_version=1, client_id="acme")

    decision = evaluate(ruleset, {"anything": True})

    assert decision.effect is RuleEffect.ALLOW


def test_malformed_condition_raises_rule_evaluation_error():
    rule = _rule("regra-quebrada", {"operador_inexistente": [1, 2]}, RuleEffect.DENY)
    ruleset = RuleSet(schema_version=1, client_id="acme", rules=(rule,))

    with pytest.raises(RuleEvaluationError) as exc_info:
        evaluate(ruleset, {})

    assert exc_info.value.rule_id == "regra-quebrada"


def test_rule_decision_is_immutable():
    from pydantic import ValidationError

    from mcp_engine.core.models import RuleDecision

    decision = RuleDecision(effect=RuleEffect.ALLOW)
    with pytest.raises(ValidationError):
        decision.effect = RuleEffect.DENY
