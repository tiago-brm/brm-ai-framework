# 001 — Contratos da Fase 1 (spec)

> Retroativo: este passo foi discutido e aprovado em chat antes da convenção
> `docs/specs/` existir. Registrado aqui para manter a numeração alinhada com
> `docs/changelog/001-contracts.md` e para que specs futuras tenham o mesmo
> formato de referência.

**Ordem da spec (docs/spec.md §2.1, item 1):** definir os contratos antes do
código. Hoje a implementação lê YAML local, mas `mcp_engine` nunca fala
diretamente com "arquivo" — fala com a interface. Na Fase 2, troca-se o adapter
por um que chama o Control Plane sem tocar em `rule_evaluator`/`interceptor`.

## Escopo deste passo

- `mcp_engine/core/contracts.py`: três `typing.Protocol` `@runtime_checkable` —
  `RuleProvider.get_rules(client_id) -> RuleSet`,
  `SkillProvider.get_skills(client_id) -> SkillSet`,
  `AuditSink.record(event) -> None` + `AuditSink.iter_events(client_id) -> Iterable[AuditEvent]`
  (o `iter_events` cobre o requisito "audit log ... exportável" da tabela de
  hardening, seção 3 da spec).
- `mcp_engine/core/models.py`: `Rule`, `RuleSet`, `SkillDefinition`, `SkillSet`,
  `AuditEvent` como `pydantic.BaseModel`.

## Decisões

- **Protocol, não ABC** — evita herança forçada; uma implementação de teste ou
  futura (Control Plane) só precisa ter os métodos certos. `isinstance()`
  funciona via `@runtime_checkable`, o que dá um teste de conformidade barato.
- **Pydantic, não dataclass** — a spec já exige `schema_version: 1` desde a v1
  (item 5 da §2.1). Pydantic valida isso de graça e, com `extra="forbid"`, faz
  uma chave desconhecida no YAML falhar na leitura em vez de ser ignorada —
  relevante porque `rules.yaml`/`skills.yaml` são editados à mão.
- **`frozen=True`** em todos os models — eventos de auditoria e regras não
  devem ser mutáveis depois de carregados/registrados.

## Fora de escopo (fica para o próximo passo)

Nenhuma implementação concreta dos contratos (`YamlRuleProvider` etc.) — isso é
o item 2 da §2.1 (monorepo mínimo) seguido do item 3 (`rule_evaluator`
sandboxado).

## Critério de pronto

- `uv run ruff check .` limpo.
- `uv run pytest -q` cobrindo: validação de schema_version, rejeição de campo
  extra, `SkillDefinition.allowed_domains` não pode ser vazia, imutabilidade,
  e conformidade de Protocol via `isinstance` para cada contrato.
