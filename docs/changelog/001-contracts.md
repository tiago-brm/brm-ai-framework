# 001 — Contratos da Fase 1

**O quê:** definidos os três contratos que a spec (seção 2.1, item 1) pede antes de
qualquer implementação concreta: `RuleProvider`, `SkillProvider`, `AuditSink`.

- `mcp_engine/core/contracts.py` — três `typing.Protocol` (`@runtime_checkable`),
  não `abc.ABC`. Duck-typing sem herança forçada: uma implementação de teste ou
  a futura implementação do Control Plane (Fase 2) só precisa ter os métodos
  certos, sem importar nada de `mcp_engine.core`.
- `mcp_engine/core/models.py` — `Rule`, `RuleSet`, `SkillDefinition`, `SkillSet`,
  `AuditEvent` como `pydantic.BaseModel`, todos com `extra="forbid"` e `frozen=True`.
  `schema_version: Literal[1]` obrigatório em `RuleSet`/`SkillSet`, conforme item 5
  da seção 2.1 ("versione o schema desde a v1").

**Por quê Pydantic em vez de `dataclass`:** a spec já exige schema versionado desde
o primeiro commit. `extra="forbid"` faz uma config YAML com chave desconhecida
falhar na leitura em vez de ser silenciosamente ignorada — é validação de entrada
de graça, relevante porque `rules.yaml`/`skills.yaml` são editados à mão pelo
cliente/BRM.

**Por quê `Protocol` em vez de `ABC`:** o `mcp_engine` (rule_evaluator, interceptor)
vai depender só da interface, nunca da implementação concreta (YAML hoje, API do
Control Plane na Fase 2). `Protocol` permite testar com fakes simples sem herdar
de nada, e `isinstance()` funciona em runtime via `@runtime_checkable` — usado nos
testes de conformidade.

**O que ainda não existe:** nenhuma implementação concreta (`YamlRuleProvider`
etc.) — só os contratos e os models. Isso é o próximo passo (item 2 da seção 2.1:
monorepo mínimo + primeira implementação real por trás dos contratos).

**Arquivos:**
- `mcp_engine/core/contracts.py`
- `mcp_engine/core/models.py`
- `tests/test_models.py` (7 casos: schema_version inválido, campo extra rejeitado,
  domain allowlist não pode ser vazia, imutabilidade, etc.)
- `tests/test_contracts.py` (4 casos: fakes em memória satisfazem cada Protocol
  via `isinstance`; um objeto sem os métodos não satisfaz nenhum)

**Validação:** `uv run ruff check .` limpo, `uv run pytest -q` — 11 passed.
