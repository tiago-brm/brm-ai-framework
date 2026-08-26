# BRM Framework

Motor de agentes MCP para clientes da BRM Solutions: dado permanece na fonte oficial
do cliente, conhecimento curado vive num Vault plugável, e um agente (Claude, via MCP)
orquestra ferramentas sobre essas duas camadas.

Arquitetura completa: ver `docs/spec.md` (spec v2.1, pós Red Team/Blue Team).
Fluxo de trabalho (spec por passo → aprovação → implementação → changelog): ver `docs/README.md`.

## Status

**Fase 1 — MVP Seguro por Cliente.** Sem Control Plane centralizado: regras e skills
vivem em config versionado (YAML) por cliente, um MCP Engine por cliente, hardening de
segurança (JSONLogic sandboxado, allowlist SSRF, DLP básico, audit log append-only)
desde o primeiro deploy.

**Gatilho de saída da Fase 1:** 2–3 clientes pagando, com pelo menos um pedindo
explicitamente self-service de regras. Antes disso, não migrar para o Control Plane
visual — a decisão fica ancorada em demanda real, não em antecipação arquitetural.

## Setup

```bash
uv sync
source .venv/bin/activate.fish
```

## Estrutura (Fase 1)

```
brm-ai-framework/
├── clients/<cliente>/config/    # rules.yaml + skills.yaml, versionados em git
├── mcp_engine/                  # motor compartilhado (core, dynamic_skills, vault)
└── audit/                       # log de auditoria append-only, hash-chained
```

Ainda não implementado — ver `docs/spec.md` seção 2.1 para a ordem recomendada
(contratos primeiro, depois rule_evaluator sandboxado, depois os 3 controles
inegociáveis do interceptor).
