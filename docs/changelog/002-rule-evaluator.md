# 002 — Rule Evaluator sandboxado

**O quê:** implementado o motor de avaliação de regras que a spec (seção 2.1,
item 3) pede logo depois dos contratos: JSONLogic sandboxado, nunca `eval`.

- `mcp_engine/core/rule_evaluator.py` — `evaluate(ruleset, facts) -> RuleDecision`.
  Percorre `ruleset.rules` na ordem do YAML, primeira regra cujo `condition`
  avalia truthy via `json_logic.jsonLogic(...)` decide o `effect`. Nenhuma regra
  casa → `RuleDecision(effect=ALLOW)` (default). Condição malformada levanta
  `RuleEvaluationError(rule_id, cause)` em vez de ser ignorada ou tratada como
  DENY silencioso.
- `mcp_engine/core/models.py` — novo model `RuleDecision` (`effect`, `rule_id`,
  `message`), `_StrictModel` como os demais (frozen, extra="forbid").
- `tests/test_rule_evaluator.py` — 7 casos: efeito ALLOW/DENY/REQUIRE_APPROVAL
  quando a regra casa, first-match-wins com duas regras conflitantes, default
  ALLOW sem match, `RuleSet` vazio também cai no default, condição malformada
  levanta `RuleEvaluationError` com o `rule_id` certo, `RuleDecision` imutável.

## Divergência do spec: troca de `json-logic` por `json-logic-qubit`

O spec 002 citava `json-logic` (o pacote que a spec.md também cita como
exemplo). Ao escrever o primeiro teste, `jsonLogic({"==": [1, 1]})` quebrou com
`TypeError: 'dict_keys' object is not subscriptable` — o código publicado no
PyPI (`json-logic==0.6.3`) usa `tests.keys()[0]`, sintaxe válida em Python 2 mas
não em Python 3 (issue aberta desde 2017 no repositório upstream, nunca
corrigida na versão publicada). Ou seja: **o pacote citado pela própria spec
está quebrado para qualquer uso em Python 3**, não é peculiaridade deste
projeto.

Troquei por `json-logic-qubit` (fork mantido em
[qubitproducts/json-logic-py](https://github.com/qubitproducts/json-logic-py),
recomendado pelos próprios mantenedores do repositório original nas issues).
Mesmo nome de módulo e função (`from json_logic import jsonLogic` /
`json_logic.jsonLogic(...)`) — zero mudança no design do `rule_evaluator.py`
além do nome do pacote no `pyproject.toml`. `docs/specs/002-rule-evaluator.md`
foi atualizado com essa nota para não deixar o spec aprovado desatualizado no
disco.

**Por que isso importa reportar:** é exatamente o tipo de coisa que "hard rules
que mais doem corrigir depois se nascerem inseguras" (linguagem da própria
spec) descreve — se tivéssemos seguido a spec ao pé da letra sem rodar teste
nenhum, o `rule_evaluator` teria uma dependência que quebra em toda chamada.

## O que ainda não existe

- `interceptor.py` (allowlist SSRF, DLP, audit log) — item 4 da §2.1, próximo
  passo.
- `YamlRuleProvider` — nenhuma implementação concreta de `RuleProvider` ainda;
  `evaluate()` só consome `RuleSet` já em memória.
- Regras reais de cliente — não existe cliente 1 configurado no repo.

**Arquivos:**
- `mcp_engine/core/rule_evaluator.py`
- `mcp_engine/core/models.py` (adicionado `RuleDecision`)
- `tests/test_rule_evaluator.py`
- `pyproject.toml` / `uv.lock` (dependência `json-logic-qubit`)

**Validação:** `uv run ruff check .` limpo, `uv run pytest -q` — 18 passed
(11 dos passos anteriores + 7 novos).
