# Graph Report - .  (2026-08-26)

## Corpus Check
- 11 files · ~2,980 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 100 nodes · 197 edges · 17 communities (16 shown, 1 thin omitted)
- Extraction: 86% EXTRACTED · 14% INFERRED · 0% AMBIGUOUS · INFERRED: 27 edges (avg confidence: 0.62)
- Token cost: 81,669 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Models Pydantic (core)|Models Pydantic (core)]]
- [[_COMMUNITY_Hardening e Deploy Fase 1|Hardening e Deploy Fase 1]]
- [[_COMMUNITY_Contratos (Protocols)|Contratos (Protocols)]]
- [[_COMMUNITY_Vault e Control Plane (Fase 2)|Vault e Control Plane (Fase 2)]]
- [[_COMMUNITY_Fakes e Testes de Contrato|Fakes e Testes de Contrato]]
- [[_COMMUNITY_Motor MCP|Motor MCP]]
- [[_COMMUNITY_Roadmap Faseado|Roadmap Faseado]]
- [[_COMMUNITY_Ciclo Spec-Changelog-Graphify|Ciclo Spec-Changelog-Graphify]]
- [[_COMMUNITY_AuditSink em memoria (teste)|AuditSink em memoria (teste)]]
- [[_COMMUNITY_Camada 3 e Claude|Camada 3 e Claude]]
- [[_COMMUNITY_Fase 3 Multi-Tenant|Fase 3 Multi-Tenant]]

## God Nodes (most connected - your core abstractions)
1. `AuditSink` - 15 edges
2. `RuleSet` - 14 edges
3. `RuleProvider` - 14 edges
4. `SkillProvider` - 14 edges
5. `SkillSet` - 13 edges
6. `_StrictModel` - 11 edges
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
- `Pydantic instead of dataclass (spec requires schema_version: 1 from v1; extra="forbid" makes an unknown key in hand-edited rules.yaml/skills.yaml fail loudly at load instead of being silently ignored)` --rationale_for--> `_StrictModel`  [EXTRACTED]
  docs/specs/001-contracts.md → mcp_engine/core/models.py
- `frozen=True on all models (audit events and rules must not be mutable after being loaded or recorded)` --rationale_for--> `_StrictModel`  [EXTRACTED]
  docs/specs/001-contracts.md → mcp_engine/core/models.py

## Hyperedges (group relationships)
- **Phase 1 contract + model layer (Protocols over versioned Pydantic models)** — core_contracts_ruleprovider, core_contracts_skillprovider, core_contracts_auditsink, core_models_ruleset, core_models_skillset, core_models_auditevent [EXTRACTED 1.00]
- **In-memory fakes proven against runtime_checkable Protocols via isinstance** — test_contracts_inmemoryruleprovider, test_contracts_inmemoryskillprovider, test_contracts_inmemoryauditsink, core_contracts_ruleprovider, core_contracts_skillprovider, core_contracts_auditsink [EXTRACTED 1.00]
- **Numbered spec-then-changelog documentation cycle applied to step 001** — docs_readme_documentation_cycle, specs_001_contracts, changelog_001_contracts [INFERRED 0.85]

## Communities (17 total, 1 thin omitted)

### Community 0 - "Models Pydantic (core)"
Cohesion: 0.22
Nodes (16): BaseModel, AuditDecision, HttpMethod, Rule, RuleEffect, SkillDefinition, _StrictModel, Enum (+8 more)

### Community 1 - "Hardening e Deploy Fase 1"
Cohesion: 0.22
Nodes (13): DLP (Data Loss Prevention), Docker Compose (Deploy Isolado), Fase 1 — MVP Seguro por Cliente, GenericRestSkill, hash_chain.py, Human-in-the-loop, interceptor.py, mTLS / JWT Rotativo (+5 more)

### Community 2 - "Contratos (Protocols)"
Cohesion: 0.26
Nodes (7): AuditSink, RuleProvider, SkillProvider, AuditSink, Protocol, Protocol instead of ABC (duck typing, no forced inheritance; runtime_checkable enables cheap isinstance conformance tests; lets mcp_engine depend on the interface so the Phase 2 Control Plane adapter can replace the YAML one without touching rule_evaluator/interceptor), test_object_missing_methods_does_not_satisfy_protocol

### Community 3 - "Vault e Control Plane (Fase 2)"
Cohesion: 0.20
Nodes (11): Control Plane, ObsidianVaultProvider, PgVectorVaultProvider, Rule Builder Visual (Low-Code), RuleProvider, schema_version, Skill Builder Genérico, SkillProvider (+3 more)

### Community 4 - "Fakes e Testes de Contrato"
Cohesion: 0.35
Nodes (7): RuleSet, SkillSet, Pydantic instead of dataclass (spec requires schema_version: 1 from v1; extra="forbid" makes an unknown key in hand-edited rules.yaml/skills.yaml fail loudly at load instead of being silently ignored), InMemoryRuleProvider, InMemorySkillProvider, test_in_memory_rule_provider_satisfies_protocol, test_in_memory_skill_provider_satisfies_protocol

### Community 5 - "Motor MCP"
Cohesion: 0.33
Nodes (7): JSONLogic, mcp_server.py, rule_evaluator.py, BRM Framework, clients/<cliente>/config/ (rules.yaml + skills.yaml), Control Plane (Visual, Centralizado), MCP Engine

### Community 6 - "Roadmap Faseado"
Cohesion: 0.33
Nodes (6): Camada 1 (Fonte Oficial), Camada 2 (Vault), Spec Architecture v2.1: BRM Framework, Fase 2 — Control Plane Real, Gatilho de Saída da Fase 1, Fase 1 — MVP Seguro por Cliente

### Community 7 - "Ciclo Spec-Changelog-Graphify"
Cohesion: 0.47
Nodes (6): Changelog 001 — Contratos da Fase 1, AuditEvent, Documentação — convenção de trabalho, Spec → Approval → Implementation → Changelog → Graphify cycle, Spec 001 — Contratos da Fase 1, frozen=True on all models (audit events and rules must not be mutable after being loaded or recorded)

### Community 9 - "Camada 3 e Claude"
Cohesion: 1.00
Nodes (3): Camada 3 (Agente), Claude, MCP (Model Context Protocol)

### Community 10 - "Fase 3 Multi-Tenant"
Cohesion: 0.67
Nodes (3): Fase 3 — Escala Multi-Tenant e RPA Serverless, RPA Serverless (Wasm/Lambda), Vector Store Compartilhado Multi-Tenant

## Knowledge Gaps
- **10 isolated node(s):** `Camada 1 (Fonte Oficial)`, `PgVectorVaultProvider`, `ObsidianVaultProvider`, `rest_builder.py`, `Skill Builder Genérico` (+5 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `AuditSink` connect `Contratos (Protocols)` to `AuditSink em memoria (teste)`, `Fakes e Testes de Contrato`, `Ciclo Spec-Changelog-Graphify`?**
  _High betweenness centrality (0.209) - this node is a cross-community bridge._
- **Why does `RuleProvider` connect `Contratos (Protocols)` to `AuditSink em memoria (teste)`, `Vault e Control Plane (Fase 2)`, `Fakes e Testes de Contrato`, `Ciclo Spec-Changelog-Graphify`?**
  _High betweenness centrality (0.182) - this node is a cross-community bridge._
- **Why does `hash_chain.py` connect `Hardening e Deploy Fase 1` to `Contratos (Protocols)`?**
  _High betweenness centrality (0.165) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `AuditSink` (e.g. with `RuleSet` and `SkillSet`) actually correct?**
  _`AuditSink` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `RuleSet` (e.g. with `InMemorySkillProvider` and `InMemoryAuditSink`) actually correct?**
  _`RuleSet` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `RuleProvider` (e.g. with `SkillSet` and `AuditEvent`) actually correct?**
  _`RuleProvider` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `SkillProvider` (e.g. with `RuleSet` and `AuditEvent`) actually correct?**
  _`SkillProvider` has 4 INFERRED edges - model-reasoned connections that need verification._