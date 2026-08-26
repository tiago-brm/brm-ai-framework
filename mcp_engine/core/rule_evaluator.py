from __future__ import annotations

from typing import Any

from json_logic import jsonLogic

from mcp_engine.core.models import RuleDecision, RuleEffect, RuleSet

DEFAULT_EFFECT = RuleEffect.ALLOW


class RuleEvaluationError(Exception):
    def __init__(self, rule_id: str, cause: Exception) -> None:
        super().__init__(f"rule {rule_id!r} failed to evaluate: {cause}")
        self.rule_id = rule_id
        self.__cause__ = cause


def evaluate(ruleset: RuleSet, facts: dict[str, Any]) -> RuleDecision:
    for rule in ruleset.rules:
        try:
            matched = jsonLogic(rule.condition, facts)
        except Exception as exc:
            raise RuleEvaluationError(rule.id, exc) from exc

        if matched:
            return RuleDecision(effect=rule.effect, rule_id=rule.id, message=rule.message)

    return RuleDecision(effect=DEFAULT_EFFECT)
