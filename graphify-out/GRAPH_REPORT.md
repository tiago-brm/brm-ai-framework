# Graph Report - .  (2026-08-26)

## Corpus Check
- 7 files · ~5,016 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 142 nodes · 274 edges · 15 communities
- Extraction: 81% EXTRACTED · 19% INFERRED · 0% AMBIGUOUS · INFERRED: 51 edges (avg confidence: 0.71)
- Token cost: 62,494 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Core Contracts (Protocols)|Core Contracts (Protocols)]]
- [[_COMMUNITY_Rule Evaluator Changelog|Rule Evaluator Changelog]]
- [[_COMMUNITY_Pydantic Data Models|Pydantic Data Models]]
- [[_COMMUNITY_Rule Evaluator Runtime|Rule Evaluator Runtime]]
- [[_COMMUNITY_Interceptor & Security Controls (spec)|Interceptor & Security Controls (spec)]]
- [[_COMMUNITY_Control Plane & Providers (spec)|Control Plane & Providers (spec)]]
- [[_COMMUNITY_Vault Layers & Deployment (spec)|Vault Layers & Deployment (spec)]]
- [[_COMMUNITY_JSONLogic & Docs Overview|JSONLogic & Docs Overview]]
- [[_COMMUNITY_Phase Roadmap|Phase Roadmap]]

## God Nodes (most connected - your core abstractions)
1. `RuleSet` - 21 edges
2. `AuditSink` - 15 edges
3. `RuleProvider` - 14 edges
4. `SkillProvider` - 14 edges
5. `SkillSet` - 13 edges
6. `_StrictModel` - 12 edges
7. `AuditEvent` - 11 edges
8. `InMemoryAuditSink` - 11 edges
9. `Changelog 001 — Contratos da Fase 1` - 11 edges
10. `InMemoryRuleProvider` - 10 edges

## Surprising Connections (you probably didn't know these)
- `RuleProvider` --implements--> `RuleProvider`  [EXTRACTED]
  mcp_engine/core/contracts.py → docs/spec.md
- `SkillProvider` --implements--> `SkillProvider`  [EXTRACTED]
  mcp_engine/core/contracts.py → docs/spec.md
- `AuditSink` --implements--> `AuditSink`  [EXTRACTED]
  mcp_engine/core/contracts.py → docs/spec.md
- `frozen=True on all models (audit events and rules must not be mutable after being loaded or recorded)` --rationale_for--> `_StrictModel`  [EXTRACTED]
  docs/specs/001-contracts.md → mcp_engine/core/models.py
- `Changelog 001 — Contratos da Fase 1` --references--> `RuleSet`  [EXTRACTED]
  docs/changelog/001-contracts.md → mcp_engine/core/models.py

## Hyperedges (group relationships)
- **Ciclo de documentação por passo (spec → changelog → graphify)** — docs_readme_specs_dir, docs_readme_changelog_dir, docs_readme_graphify, docs_readme_ciclo_por_passo [EXTRACTED 1.00]
- **Motor de avaliação de regras (passo 002)** — specs_002_rule_evaluator_rule_evaluator_py, specs_002_rule_evaluator_ruleset, specs_002_rule_evaluator_ruledecision, specs_002_rule_evaluator_ruleevaluationerror, specs_002_rule_evaluator_json_logic_qubit [EXTRACTED 1.00]
- **Pipeline de auditoria da decisão (RuleDecision → interceptor → AuditEvent)** — specs_002_rule_evaluator_ruledecision, specs_002_rule_evaluator_interceptor_py, specs_002_rule_evaluator_auditevent [EXTRACTED 1.00]

## Communities (15 total, 0 thin omitted)

### Community 0 - "Core Contracts (Protocols)"
Cohesion: 0.16
Nodes (19): Changelog 001 — Contratos da Fase 1, AuditSink, RuleProvider, SkillProvider, AuditEvent, SkillSet, Spec → Approval → Implementation → Changelog → Graphify cycle, AuditSink (+11 more)

### Community 1 - "Rule Evaluator Changelog"
Cohesion: 0.10
Nodes (28): Changelog 002 — Rule Evaluator sandboxado, json-logic 0.6.3 (PyPI, quebrado em Python 3), json-logic-qubit (dependência adotada), mcp_engine/core/rule_evaluator.py — evaluate(ruleset, facts), RuleDecision (models.py), RuleEvaluationError, tests/test_rule_evaluator.py (7 casos), Documentação — convenção de trabalho (+20 more)

### Community 2 - "Pydantic Data Models"
Cohesion: 0.20
Nodes (17): BaseModel, AuditDecision, HttpMethod, Rule, RuleEffect, SkillDefinition, _StrictModel, Enum (+9 more)

### Community 3 - "Rule Evaluator Runtime"
Cohesion: 0.28
Nodes (13): RuleDecision, RuleSet, evaluate(), RuleEvaluationError, Exception, _rule(), test_empty_ruleset_defaults_to_allow(), test_first_match_wins() (+5 more)

### Community 4 - "Interceptor & Security Controls (spec)"
Cohesion: 0.22
Nodes (13): DLP (Data Loss Prevention), Docker Compose (Deploy Isolado), Fase 1 — MVP Seguro por Cliente, GenericRestSkill, hash_chain.py, Human-in-the-loop, interceptor.py, mTLS / JWT Rotativo (+5 more)

### Community 5 - "Control Plane & Providers (spec)"
Cohesion: 0.22
Nodes (10): Control Plane, ObsidianVaultProvider, PgVectorVaultProvider, Rule Builder Visual (Low-Code), RuleProvider, schema_version, Skill Builder Genérico, SkillProvider (+2 more)

### Community 6 - "Vault Layers & Deployment (spec)"
Cohesion: 0.25
Nodes (9): Camada 1 (Fonte Oficial), Camada 2 (Vault), Camada 3 (Agente), Claude, Spec Architecture v2.1: BRM Framework, Fase 3 — Escala Multi-Tenant e RPA Serverless, MCP (Model Context Protocol), RPA Serverless (Wasm/Lambda) (+1 more)

### Community 7 - "JSONLogic & Docs Overview"
Cohesion: 0.29
Nodes (8): JSONLogic, mcp_server.py, rule_evaluator.py, BRM Framework, clients/<cliente>/config/ (rules.yaml + skills.yaml), Control Plane (Visual, Centralizado), MCP Engine, Vault (Camada de Conhecimento Curado)

### Community 8 - "Phase Roadmap"
Cohesion: 0.67
Nodes (3): Fase 2 — Control Plane Real, Gatilho de Saída da Fase 1, Fase 1 — MVP Seguro por Cliente

## Knowledge Gaps
- **15 isolated node(s):** `Camada 1 (Fonte Oficial)`, `PgVectorVaultProvider`, `ObsidianVaultProvider`, `rest_builder.py`, `Skill Builder Genérico` (+10 more)
  These have ≤1 connection - possible missing edges or undocumented components.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Changelog 001 — Contratos da Fase 1` connect `Core Contracts (Protocols)` to `Pydantic Data Models`, `Rule Evaluator Runtime`?**
  _High betweenness centrality (0.327) - this node is a cross-community bridge._
- **Why does `Spec → Approval → Implementation → Changelog → Graphify cycle` connect `Core Contracts (Protocols)` to `Rule Evaluator Changelog`?**
  _High betweenness centrality (0.304) - this node is a cross-community bridge._
- **Why does `Documentação — convenção de trabalho` connect `Rule Evaluator Changelog` to `Core Contracts (Protocols)`?**
  _High betweenness centrality (0.301) - this node is a cross-community bridge._
- **Are the 12 inferred relationships involving `RuleSet` (e.g. with `InMemorySkillProvider` and `InMemoryAuditSink`) actually correct?**
  _`RuleSet` has 12 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `AuditSink` (e.g. with `RuleSet` and `SkillSet`) actually correct?**
  _`AuditSink` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `RuleProvider` (e.g. with `SkillSet` and `AuditEvent`) actually correct?**
  _`RuleProvider` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `SkillProvider` (e.g. with `RuleSet` and `AuditEvent`) actually correct?**
  _`SkillProvider` has 4 INFERRED edges - model-reasoned connections that need verification._