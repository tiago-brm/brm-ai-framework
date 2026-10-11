# BRM Framework

Motor de agentes MCP para clientes da BRM Solutions: o dado permanece na fonte oficial do cliente, o conhecimento curado vive num **Vault** plugável (Obsidian ou Notion), e o Claude, via MCP, orquestra ferramentas sobre essas duas camadas — com **regras de negócio, DLP, controle de acesso e auditoria aplicados pelo motor**, nunca pelo modelo.

## Documentação

Tudo começa em [`docs/README.md`](docs/README.md). Atalhos: [PRD](docs/PRD.md) · [Arquitetura](docs/ARCHITECTURE.md) · [Dados](docs/DATABASE.md) · [Segurança](docs/SECURITY.md) · [Testes](docs/TESTING.md) · [API](docs/API.md) · [Fluxos](docs/UX_FLOWS.md) · [Design](docs/DESIGN_SYSTEM.md) · [Estilo](docs/CODE_STYLE.md). Agentes de código: leia [`AGENTS.md`](AGENTS.md).

## Status

**Fase 1 — MVP seguro por cliente.** Sem Control Plane: regras e skills vivem em YAML versionado por cliente, um engine por cliente, com hardening desde o primeiro deploy (JSONLogic sandboxado, allowlist SSRF, DLP, auditoria append-only com hash-chain, RBAC, acesso por nota no vault). O painel `super_admin`, o simulador de governança e os agentes de rascunho (regras, skills, documentos) existem; a execução HTTP das skills ainda é **simulada**.

**Gatilho de saída da Fase 1:** 2–3 clientes pagando e pelo menos um pedindo explicitamente self-service de regras. Antes disso, não migrar para o Control Plane.

⚠️ Antes de usar dado real de cliente, leia as lacunas abertas em [`docs/SECURITY.md`](docs/SECURITY.md) §10 (tokens em texto puro, regra com efeito padrão `allow`, CORS `*`…). O build do painel falha em clone limpo — ver [`docs/TESTING.md`](docs/TESTING.md) §8.

## Setup

```bash
uv sync                      # Python 3.11, dependências em uv.lock
uv run ruff check .
uv run pytest -q             # 2 testes baixam o modelo de embeddings (~470 MB) e exigem rede
```

## Rodando

```bash
# 1) Servidor MCP local (stdio) — um usuário por processo
CLIENT_ID=instituto-afw MCP_USER_EMAIL=wagner@clinica-demo.com.br \
  uv run python -m mcp_engine.core.mcp_server

# 2) Servidor HTTP (SSE do MCP + REST do painel) em :8000
CLIENT_ID=instituto-afw uv run uvicorn mcp_engine.core.http_app:app --port 8000

# 3) Painel (precisa do motor HTTP rodando; login com token de super_admin)
cd dashboard && npm ci && npm run dev        # http://localhost:5173
```

Perfis e tokens de demonstração: `uv run python -m mcp_engine.core.user_store` (cliente `example`) ou `uv run python scripts/preparar_demo_chat.py <perfil>` (cliente `instituto-afw`, também imprime a configuração do Claude Desktop). **Nunca** rode o seed em cliente real.

Conectar ao Claude Code: `claude mcp add brm-engine -e CLIENT_ID=… -e CONFIG_ROOT=$(pwd)/clients -e DATA_ROOT=$(pwd)/data -- uv run python -m mcp_engine.core.mcp_server`. Ao editar `rules.yaml`/`skills.yaml`, reinicie o servidor. Passo a passo e Claude Desktop: [`docs/UX_FLOWS.md`](docs/UX_FLOWS.md) Flow A2.

Variáveis: `CLIENT_ID` (padrão `example`), `CONFIG_ROOT` (`clients`), `DATA_ROOT` (`data`), `MCP_USER_EMAIL` / `MCP_API_TOKEN`; segredos opcionais em [`docs/SECURITY.md`](docs/SECURITY.md) §4.

## Estrutura

```text
brm-ai-framework/
├── AGENTS.md CLAUDE.md          # instruções para agentes de código
├── docs/                        # PRD, ARCHITECTURE, DATABASE, SECURITY, ...
├── mcp_engine/{core,vault}/     # motor compartilhado
├── audit/                       # trilha append-only com hash-chain
├── clients/<cliente>/{config,vault}/   # regras, skills, guardrails, vault por cliente
├── dashboard/                   # painel super_admin (React + Vite)
├── scripts/                     # demos, pré-voo, preparação de perfis
└── tests/                       # pytest
```

Clientes de exemplo: `example` (jurídico), `clinica-psi` e `instituto-afw` (saúde, dados fictícios).
