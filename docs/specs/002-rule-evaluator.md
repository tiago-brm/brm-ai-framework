# 002 — Rule Evaluator sandboxado (spec)

**Ordem da spec (docs/spec.md §2.1, item 3):** implementar o `rule_evaluator`
sandboxado logo depois dos contratos — "é pouco código, mas é o componente que
mais dói corrigir depois se nascer inseguro". Regra dura da spec: usar uma lib
de JSONLogic, **nunca `eval` de string livre**.

## Escopo deste passo

- Dependência nova: `json-logic-qubit` (PyPI; import como `json_logic`, função
  `jsonLogic`) — implementação de [JsonLogic](https://jsonlogic.com) em Python.
  **Nota adicionada durante a implementação:** o pacote `json-logic` "oficial"
  citado como exemplo pela spec (repositório `json-logic-py`) está quebrado em
  Python 3 desde 2017 — `tests.keys()[0]` não funciona porque `dict.keys()`
  deixou de ser indexável (`TypeError: 'dict_keys' object is not subscriptable`,
  reproduzido nos testes deste passo). `json-logic-qubit` é o fork mantido
  ([qubitproducts/json-logic-py](https://github.com/qubitproducts/json-logic-py))
  que corrige isso; mesma API (`import json_logic; json_logic.jsonLogic(...)`),
  então nada no design abaixo muda.
- `mcp_engine/core/rule_evaluator.py`:
  - `RuleDecision` (novo model em `models.py`, `_StrictModel`, `frozen=True`):
    `effect: RuleEffect`, `rule_id: str | None`, `message: str | None`.
  - `evaluate(ruleset: RuleSet, facts: dict[str, Any]) -> RuleDecision`.
  - `RuleEvaluationError(Exception)` — erro de config, não de negócio.
- `tests/test_rule_evaluator.py` com regras sintéticas (não regras reais de
  cliente — não existe cliente 1 configurado no repo ainda).

## Decisões

- **`json_logic.jsonLogic(rule.condition, facts)`, nada de `eval`/`exec`.**
  `rule.condition` já é um `dict` estruturado (carregado do YAML, validado só
  na forma pelo Pydantic) — a única superfície de execução é o conjunto fixo
  de operadores da JsonLogic. Não há string livre em nenhum ponto do caminho.
- **Ordem importa, primeira regra que casar decide (`first-match-wins`).**
  Percorre `ruleset.rules` na ordem em que aparecem no YAML; a primeira cujo
  `condition` avalia truthy define o `effect` retornado. Isso é o modelo mais
  simples de auditar: quem lê o `rules.yaml` de cima pra baixo entende a
  decisão sem precisar somar prioridades numéricas.
- **Default quando nenhuma regra casa: `ALLOW`.** O item 3 da spec fala em
  "3-5 Hard Rules" — um punhado de regras específicas que bloqueiam ou exigem
  aprovação para ações perigosas, não uma allowlist exaustiva de tudo que é
  permitido. Modelo de firewall com exceções, não whitelist. **Alternativa
  rejeitada:** default `DENY` — inverteria o modelo para "só o que está
  explicitamente permitido roda", o que exigiria uma regra por skill/ação
  possível; mais seguro em tese, mas não é o que a spec descreve. Se você
  preferir default-deny, é uma linha (`default_effect` vira parâmetro) — mas
  aí quero decidir isso agora, não descobrir depois que uma ação devia ter
  sido bloqueada e passou por falta de regra.
- **Condição malformada = `RuleEvaluationError`, não silenciosamente ignorada
  nem silenciosamente negada.** Se `jsonLogic` levantar exceção (operador
  desconhecido, formato inesperado) ao avaliar uma regra, isso é erro de quem
  editou `rules.yaml` à mão — mesma filosofia do `extra="forbid"` do passo
  001: falhar alto no load/eval em vez de mascarar. Pular a regra quebrada
  silenciosamente é pior: pode ser justo a regra que bloquearia algo perigoso.
- **`RuleDecision` carrega `rule_id`/`message` para o audit log.** O
  `AuditEvent` do passo 001 já tem `rule_id: str | None` — este é o valor que
  alimenta esse campo quando o `interceptor` (próximo passo) registrar a
  decisão.

## Fora de escopo (fica para depois)

- As "3-5 Hard Rules reais do cliente 1" — não existe cliente 1 configurado
  no repo ainda (item 2 da §2.1, `clients/<cliente>/config/`, é posterior na
  prática). Este passo entrega o motor; regras reais entram quando houver
  cliente.
- O `interceptor.py` (item 4 da §2.1: allowlist SSRF, DLP, audit log) — passo
  seguinte.
- Carregar `RuleSet` de um arquivo YAML real (`YamlRuleProvider`) — ainda não
  existe nenhuma implementação concreta de `RuleProvider`; este passo só
  consome o `RuleSet` já validado em memória.

## Critério de pronto

- `uv add json-logic-qubit` limpo, sem conflito de dependências.
- `uv run ruff check .` limpo.
- `uv run pytest -q` cobrindo: regra `ALLOW`/`DENY`/`REQUIRE_APPROVAL` casando
  corretamente; first-match-wins com múltiplas regras; nenhuma regra casa →
  `ALLOW` default; condição malformada levanta `RuleEvaluationError`;
  `RuleDecision` imutável.
