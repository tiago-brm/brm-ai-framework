# 013 — Perfil super_admin e regras condicionadas por perfil

**O quê:** novo perfil `super_admin` (acesso irrestrito, inclusive a tools
futuras não listadas em `ROLE_TOOLS`) com poder de criar/promover outros
usuários — restrito a um `SQLUserStore` por cliente, sem control plane
cross-client. Regras de negócio (`rules.yaml`) agora podem condicionar em
`actor_role`. Spec aprovada antes do código, ver
`docs/specs/012-super-admin-role.md`.

- `mcp_engine/core/auth.py` — `Role.SUPER_ADMIN` novo. `is_tool_allowed`
  faz bypass incondicional para `super_admin` antes de olhar `ROLE_TOOLS` —
  cobre tools restritas futuras sem precisar lembrar de adicionar o perfil
  toda vez. `ROLE_TOOLS[Role.SUPER_ADMIN]` listado à parte (redundante com
  o bypass, necessário só pra `tools/list` mostrar certo).
- `mcp_engine/core/user_store.py` — `UserNotFoundError` (novo),
  `SQLUserStore.update_role(email, role)`. `seed_demo_users` ganha
  `brm@brm.com.br` / `super-admin-token-demo`.
- `mcp_engine/core/mcp_server.py`:
  - `BRMEngine.atualizar_perfil_usuario(email, role)` (novo, tool MCP nova)
    — restrita a `super_admin`.
  - `_forbidden_super_admin_assignment` — só `super_admin` pode atribuir
    `super_admin` a alguém, em `criar_usuario` e `atualizar_perfil_usuario`.
  - **Correção:** `deletar_banco()` tinha uma segunda checagem interna
    (`user.role is not Role.ADMIN`) que bloquearia um `super_admin` mesmo
    depois de passar pelo gate do `RBACFastMCP` — virou `not in (ADMIN,
    SUPER_ADMIN)`.
  - `call_skill` inclui `actor_role` nos `facts` passados pro
    `rule_evaluator` — regras em `rules.yaml` podem referenciar
    `{"var": "actor_role"}` no JSONLogic, ao lado de `skill_name`/`actor`/
    `origem_sistema` já existentes.

## Decisão: super_admin é por cliente, não cross-client (control plane)

Cogitado como registro global de staff BRM operando em qualquer cliente —
descartado nesta sessão porque contradiz a decisão documentada em
`docs/spec.md` (Fase 1 sem Control Plane centralizado). `super_admin` é só
mais um valor de `Role`, guardado no `SQLUserStore` de cada cliente como
`admin`/`viewer` já são.

## Decisão: actor_role no motor de regras (não só visibilidade no painel)

Havia opções mais simples (regras só visíveis num painel administrativo,
sem mudar avaliação; ou RBAC só na promoção de draft). Confirmado com o
usuário: quer o nível mais profundo — `rule_evaluator.evaluate()` já era
JSONLogic puro sobre um dict de facts, então isso ficou aditivo (uma chave
nova no dict, zero mudança na função de avaliação em si). Regras existentes
que não usam `actor_role` continuam idênticas.

## O que ainda não existe

- **Tabela de permissões por tool persistida/editável** (hoje ainda é o
  dict `ROLE_TOOLS` fixo no código) — necessária antes de um dashboard
  poder ligar/desligar acesso a `call_skill`/`vault_*`/
  `consultar_processo_datajud` (hoje livres a qualquer perfil) sem deploy.
- **Dashboard React/Vite** para o `super_admin` gerenciar usuários, perfis,
  tools, regras e vault — próximo passo, depende do item acima.
- Governança de `vault_promote_draft` por perfil.
- Cross-client / control plane — deliberadamente fora, ver decisão acima.

**Arquivos:**
- `mcp_engine/core/auth.py`, `user_store.py`, `mcp_server.py` (modificados)
- `tests/test_auth.py`, `test_user_store.py`, `test_mcp_server.py`,
  `test_rbac_server.py` (casos novos para `super_admin` e `actor_role`)
- `docs/specs/012-super-admin-role.md`

**Validação:** `uv run ruff check .` limpo, `uv run pytest -q` — 205 passed
(190 anteriores + 15 novos).
