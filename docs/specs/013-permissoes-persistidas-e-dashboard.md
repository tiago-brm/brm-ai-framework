# 013 — Permissões persistidas, API REST e painel super_admin (spec)

**Ordem da spec:** depois da 012 (perfil super_admin). Aquela spec deixou
registrado como próximo passo: "permissão por tool persistida e editável
(tabela em vez do dict `ROLE_TOOLS` fixo no código)" e "dashboard para o
super_admin gerenciar usuários, perfis, tools e visualizar regras/vault".
Esta spec entrega os dois.

## Escopo confirmado com o usuário

- Painel "muito simples": só o super_admin tem acesso, e ele precisa ver
  **tudo** que o MCP local expõe — usuários/perfis/permissionamento, o
  catálogo de tools, as regras de negócio, e o conteúdo do vault.
- Sem multi-tenant/control-plane (reafirmado — mesma decisão da spec 012).

## Componentes

### `mcp_engine/core/user_store.py` — tabela `tool_permissions`

- `ToolPermissionRow` (role, tool_name, allowed — `UniqueConstraint` no
  par) substitui `ROLE_TOOLS` como fonte de verdade em runtime.
- Semântica preservada: ausência de **qualquer** linha para uma tool = tool
  aberta a todo perfil (igual ao "não está no dict" de hoje); presença de
  ao menos uma linha = tool restrita, e um perfil sem linha própria é
  negado. `super_admin` nunca precisa de linha — o bypass em
  `is_tool_allowed` já é incondicional (spec 012).
- `_DEFAULT_PERMISSIONS` espelha o `ROLE_TOOLS` de hoje; seedado
  automaticamente em `__init__` (idempotente, roda uma vez só — não é
  credencial, é config de schema, mesmo raciocínio de
  `Base.metadata.create_all` já ser automático).
- `SQLUserStore.is_tool_allowed/set_permission/list_permissions` — CRUD
  completo sobre a tabela.

### `mcp_engine/core/auth.py` / `rbac_server.py` — fio até o RBAC

- `is_tool_allowed(role, tool_name, store=None)` ganha um terceiro
  parâmetro opcional: com `store`, delega pra
  `SQLUserStore.is_tool_allowed` (editável em runtime); sem `store`
  (fallback usado só por quem chama a função isolada, ex. testes unitários
  de auth.py), continua no dict estático `ROLE_TOOLS` de sempre — mesma
  semântica, comportamento idêntico ao pré-existente.
- `RBACFastMCP` ganha `user_store` (mesmo padrão de DI de `audit_sink`/
  `client_id`) e passa adiante em `list_tools`/`call_tool`.
- `build_server()` passa `engine.user_store` pro `RBACFastMCP` — dali em
  diante, toda decisão de RBAC real (stdio e HTTP) já é sobre a tabela
  persistida, não o dict.

### Tools novas: `listar_permissoes` / `atualizar_permissao`

Mesmo padrão de `criar_usuario`/`atualizar_perfil_usuario` — métodos de
`BRMEngine` delegando pro `user_store`, registrados em `ROLE_TOOLS[SUPER_ADMIN]`
(e seedados como `False` pra `admin` em `_DEFAULT_PERMISSIONS`, garantindo
que só super_admin — via bypass — alcance essa gestão).

### `mcp_engine/core/api_router.py` (novo) + `http_app.py`

REST fino sobre o mesmo `BRMEngine`/`RBACFastMCP` que stdio/SSE já usam —
um browser `fetch()` consome JSON muito mais fácil que abrir uma sessão
MCP. Toda rota exige `Role.SUPER_ADMIN` (dependency `_require_super_admin`,
lida via `get_current_user()` — a mesma identidade que o middleware HTTP já
resolve do header `Authorization`, nada de sistema de login paralelo).

| Rota | Método | Engine |
|---|---|---|
| `/api/me` | GET | identidade atual |
| `/api/users` | GET/POST | `listar_usuarios`/`criar_usuario` |
| `/api/users/{email}/role` | PATCH | `atualizar_perfil_usuario` |
| `/api/permissions` | GET/PUT | `listar_permissoes`/`atualizar_permissao` |
| `/api/tools` | GET | `mcp_server.list_tools()` (catálogo completo — a
  identidade super_admin do próprio request já garante isso via bypass) |
| `/api/rules` | GET | `engine.rules` (nova property, só leitura) |
| `/api/vault/notes` | GET | `engine.vault_list_notes()` (novo, mesmo
  padrão de `vault_search`/`vault_get_related`) |
| `/api/audit` | GET | `query_audit_log` |

`app.include_router(..., prefix="/api")` é registrado **antes** de
`app.mount("/", mcp_server.sse_app())` — um `Mount("/")` casa qualquer
path, então uma rota adicionada depois dele nunca seria alcançada.
`CORSMiddleware(allow_origins=["*"])` liberado — aceitável no estágio atual
(Fase 1, local-first, sem exposição pública, ver `docs/spec.md`); a
dashboard roda em outra origem (Vite dev server) e autentica com o mesmo
Bearer token do `SQLUserStore`, sem cookie/sessão pra proteger de CSRF.

### `dashboard/` (novo) — React + Vite + TypeScript

Aplicação separada, não publicada como pacote do `mcp_engine`. Tela de
login pede URL do `http_app` + token Bearer (reaproveita a mesma
autenticação já usada por stdio/SSE — sem sistema de sessão próprio).
Abas: Usuários (criar, trocar perfil), Permissões (matriz viewer/admin ×
tool, super_admin sempre marcado à parte como "acesso total"), Tools
(catálogo completo, somente leitura), Regras (somente leitura — editar
continua via `vault_interpret_and_propose`/`vault_promote_draft`), Vault
(notas, somente leitura), Auditoria (trilha recente).

## Fora de escopo (registrado, não decidido agora)

- Hash/expiração de token de sessão do painel (usa o token Bearer bruto do
  `SQLUserStore`, igual ao MCP já usa).
- Edição de regra/vault pelo próprio painel — hoje só leitura; escrever
  continua pelos fluxos de draft já existentes (specs 008/009).
- Deploy de produção do dashboard (build estático servido por quê? Nginx,
  Vercel, etc.) — só validado via `npm run dev` nesta spec.

## Critério de pronto

- `tool_permissions` funcionando e editável em runtime, sem restart do
  processo (verificado em teste: `test_toggling_permission_takes_effect_without_restart`).
- `/api/*` acessível só por super_admin (403 para admin/viewer/sem token),
  cobrindo usuários/permissões/tools/regras/vault/auditoria.
- Dashboard React fazendo login e navegando pelas 6 abas contra um
  `http_app` real.
- `uv run ruff check .` e `uv run pytest -q` verdes; `npx tsc -b` e
  `npm run build` do dashboard sem erro.
