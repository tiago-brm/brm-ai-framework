# 014 — Permissões persistidas, API REST e painel super_admin

**O quê:** o dict fixo `ROLE_TOOLS` virou uma tabela editável em runtime
(`tool_permissions`), exposta via API REST (`/api/*`, restrita a
super_admin) e consumida por um painel React/Vite novo em `dashboard/`.
Spec aprovada antes do código, ver
`docs/specs/013-permissoes-persistidas-e-dashboard.md`.

- `mcp_engine/core/user_store.py` — `ToolPermissionRow`, `ToolPermission`,
  `_DEFAULT_PERMISSIONS` (espelha o `ROLE_TOOLS` de hoje), seedado
  automaticamente. `SQLUserStore.is_tool_allowed/set_permission/list_permissions`.
- `mcp_engine/core/auth.py` — `is_tool_allowed(role, tool_name, store=None)`:
  com `store`, decide pela tabela; sem `store` (fallback de quem chama a
  função isolada), mantém o dict estático de sempre.
- `mcp_engine/core/rbac_server.py` — `RBACFastMCP` recebe `user_store`
  opcional e repassa pro `is_tool_allowed` em `list_tools`/`call_tool`.
- `mcp_engine/core/mcp_server.py` — `listar_permissoes`/`atualizar_permissao`
  (tools novas, super_admin-only), `vault_list_notes` (tool nova, mesmo
  padrão de `vault_search`), `rules` (property só-leitura pro `RuleSet`
  interno), `build_server()` passa `engine.user_store` pro `RBACFastMCP`.
- `mcp_engine/core/api_router.py` (novo) — REST fino sobre o mesmo engine:
  `/api/me`, `/api/users` (GET/POST), `/api/users/{email}/role` (PATCH),
  `/api/permissions` (GET/PUT), `/api/tools`, `/api/rules`,
  `/api/vault/notes`, `/api/audit`. Toda rota exige `Role.SUPER_ADMIN`.
- `mcp_engine/core/http_app.py` — `CORSMiddleware` (`allow_origins=["*"]`,
  aceitável no estágio local-first atual), `include_router(api_router,
  prefix="/api")` registrado antes do `mount("/", mcp_server.sse_app())`.
- `dashboard/` (novo, React + Vite + TypeScript) — login por
  URL+token, 6 abas (Usuários, Permissões, Tools, Regras, Vault,
  Auditoria). `src/api.ts` é o único lugar que fala com `/api/*`.

## Decisão: tabela substitui o dict, mas com o mesmo fallback pra não quebrar quem já chamava `is_tool_allowed` isolado

`is_tool_allowed` ganhou `store` como parâmetro opcional em vez de
obrigatório — os testes unitários de `auth.py` (e qualquer chamador futuro
que não tenha um `SQLUserStore` à mão) continuam funcionando exatamente
como antes, contra o dict `ROLE_TOOLS`. Todo caminho real (stdio, HTTP) já
passa `engine.user_store` via `RBACFastMCP`, então o comportamento em
produção é 100% da tabela.

## Decisão: painel fala REST, não MCP/SSE, com o navegador

Reaproveitar o protocolo MCP no browser exigiria um client MCP em
JavaScript rodando sobre SSE — viável, mas seria muito mais código pra um
painel "simples" cujo único consumidor é uma tela HTML. REST + `fetch()`
sobre o mesmo `BRMEngine` é a casca mais fina possível; nenhuma lógica de
negócio foi duplicada, cada rota chama exatamente o mesmo método que a
tool MCP equivalente chama.

## Validação end-to-end

`uvicorn` + seed rodando com o cliente `example` real (dados de exemplo já
existentes no vault) e o dashboard via `npm run dev`: login com
`super-admin-token-demo`, criar usuário, trocar perfil, listar/alternar
permissão, listar as 19 tools registradas, as 3 regras de `rules.yaml`, as
18 notas do vault, e a trilha de auditoria — tudo via `curl` reproduzindo
exatamente as chamadas que o dashboard faz. Token de perfil `admin`
confirmado como `403` em qualquer rota `/api/*`.

## O que ainda não existe

- Painel não escreve regra nem nota de vault — só leitura; edição continua
  pelos fluxos de draft já existentes.
- Sem hash/expiração de sessão no painel — usa o Bearer token bruto do
  `SQLUserStore`, igual ao MCP.
- Deploy de produção do dashboard (hoje só validado com `npm run dev`).

**Arquivos:**
- `mcp_engine/core/user_store.py`, `auth.py`, `rbac_server.py`,
  `mcp_server.py`, `http_app.py` (modificados)
- `mcp_engine/core/api_router.py` (novo)
- `dashboard/` (novo, React + Vite + TypeScript)
- `tests/test_user_store.py`, `test_auth.py`, `test_rbac_server.py`,
  `test_mcp_server.py` (casos novos), `tests/test_api_router.py` (novo, 11
  casos)
- `docs/specs/013-permissoes-persistidas-e-dashboard.md`

**Validação:** `uv run ruff check .` limpo, `uv run pytest -q` — 228
passed (217 anteriores + 11 novos). `npx tsc -b` e `npm run build` do
dashboard sem erro.
