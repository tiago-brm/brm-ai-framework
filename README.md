# BRM Framework

Harness de governança para agentes de IA (MCP) em clientes da BRM Solutions. O agente
(Claude, via MCP) age sobre sistemas e dados do cliente; o motor decide, por pessoa e por
ação, o que pode, o que precisa de confirmação e o que fica registrado. O dado permanece
na fonte oficial, o conhecimento curado vive num vault em markdown, e cada cliente tem
seu próprio processo, configuração e dados.

Arquitetura e princípios: `docs/specs/019-fundacao-airp.md` (gate dos 5 pilares) e
`docs/spec.md`. Fluxo de trabalho: spec, aprovação, implementação, changelog
(`docs/README.md`).

## Como funciona

Cada chamada de skill passa por: identidade e RBAC (default deny), DLP de entrada, fatos
derivados do vault, regras JSONLogic, autonomia A0 a A3 (com operações de confirmação) e
só então o executor (mock, handler nativo ou HTTP pela allowlist). Tudo grava auditoria
encadeada por hash, com identidade e `trace_id`.

## Como rodar

```bash
uv sync --locked
CLIENT_ID=example uv run --locked python -m mcp_engine.core.mcp_server  # MCP via stdio
PYTHONPATH=. uv run --locked python scripts/preflight_demo.py          # checagem da demo
```

Novo cliente (menos de 15 minutos):

```bash
PYTHONPATH=. uv run --locked python scripts/novo_cliente.py minha-empresa --modulos resend
```

O script copia `clients/_template`, habilita os módulos, valida com o loader e cria os
bancos. Depois, adote as skills desejadas em `skills.yaml` com `usar: modulo.skill`.

## Estrutura

```
clients/<id>/config/     rules.yaml, skills.yaml, client_config.yaml, vault_access.yaml
clients/<id>/vault/      conhecimento em markdown
clients/<id>/modules/    módulos privados do cliente (opcional)
clients/_template/       ponto de partida para clientes novos
mcp_engine/core/         núcleo agnóstico: identidade, RBAC, regras, DLP, executor, operações
mcp_engine/vault/        providers e política de acesso do vault
mcp_engine/modules/      catálogo de módulos compartilhados (datajud, resend)
audit/                   trilha de auditoria append-only
scripts/                 demo, migração de permissões, criação de cliente
```

## Testes

```bash
uv run --locked python -m pytest -q
uv run --locked ruff check .
```

## Mapa das specs 019 a 031

| Spec | Tema |
|---|---|
| 019 | Fundação AIRP: princípios, gate dos 5 pilares, roadmap |
| 020 | Identidade por pessoa, RBAC default deny, DLP de entrada |
| 021 | Executor de skills: handlers nativos, HTTP, SecretStore |
| 022 | Operações com confirmação e aprovação |
| 023 | Módulo financeiro (ledger gerencial) |
| 024 | Importador e exportador de planilhas |
| 025 | Segundo cérebro: vault, editor web, memória |
| 026 | Conector BRM Agenda governado |
| 027 | Cliente agnóstico: módulos, capacidades por cliente, template |
| 028 | Empacotamento Docker, backup e nuvem |
| 029 | Avaliação de intenção e plano de testes |
| 030 | Caderno como interface de configuração |
| 031 | Gatilhos e automações declarativas |

O estado de cada uma está no campo Status do arquivo da spec; entregas ficam em
`docs/changelog/`.
