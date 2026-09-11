# 012 — Persistência de usuários/RBAC (SQLite + SQLAlchemy)

**O quê:** troca do dicionário em memória da spec 010
(`MOCK_USERS_BY_EMAIL`/`MOCK_USERS_BY_TOKEN`) por um banco de verdade — um
SQLite por cliente, via SQLAlchemy, mais duas tools pra gerenciar usuários
(`criar_usuario`/`listar_usuarios`, admin-only). Spec aprovada antes do
código, ver `docs/specs/011-persistencia-usuarios.md`.

- `mcp_engine/core/user_store.py` (novo) — `UserRow` (SQLAlchemy 2.0 style,
  `DeclarativeBase`/`Mapped`/`mapped_column`), `SQLUserStore`
  (`get_by_email`, `get_by_token`, `create_user` — gera token via
  `secrets.token_urlsafe(24)` quando omitido, devolvido só nesse momento —,
  `list_users` — nunca inclui token), `seed_demo_users` (idempotente,
  explícito, nunca chamado automaticamente por `main()`/`create_app()`).
  Um arquivo por cliente, `clients/<cliente>/config/users.db` — mesmo
  padrão `config_root / client_id / "config" / <algo>` que
  `FileSystemRuleProvider`/`DraftStore`/etc. já seguem.
- `mcp_engine/core/auth.py` — `resolve_stdio_identity`/`resolve_http_identity`
  passam a receber um `SQLUserStore` e consultá-lo em vez de ler os
  dicionários removidos. `ROLE_TOOLS[Role.ADMIN]` ganha `criar_usuario`/
  `listar_usuarios`. Todo o resto (`RBACFastMCP`, `is_tool_allowed`,
  `current_user_var`/`get_current_user`) — inalterado, porque nunca tocou
  em storage, só na fronteira de identidade que a spec 010 já isolava.
- `mcp_engine/core/mcp_server.py` — `BRMEngine.user_store` (DI, mesmo
  padrão de `vault_provider`); `main()` reordenado — o engine é construído
  antes da resolução de identidade agora, porque é ele quem carrega o
  `SQLUserStore` que a resolução consulta.
- `mcp_engine/core/http_app.py` — middleware passa a chamar
  `resolve_http_identity(engine.user_store, ...)`.

## Decisão: um SQLite por cliente, não um banco único com coluna client_id

Confirmado com o usuário: escopo é só usuários/perfis, sem migrar
regras/skills/audit para SQL. Dado esse escopo, seguir o padrão de
tenancy que o resto do projeto já usa (arquivo por cliente, não coluna)
era mais importante do que a economia de manter um único banco — é
consistente com `FileSystemRuleProvider`/`FileSystemSkillProvider`/
`FileSystemClientConfigProvider`/`DraftStore`, todos resolvidos por
`config_root / client_id / "config"`.

## Decisão: sem hash de token, por enquanto — registrado, não escondido

Token fica em texto puro no SQLite. Já é uma melhoria sobre o mock anterior
(tokens hardcoded no código-fonte, visíveis a qualquer um com acesso ao
repo) — sair do código pro banco, e ficar gerável por `criar_usuario`, é
progresso real. Mas não é produção-pronto: hash de token é o próximo passo
óbvio, fora do escopo confirmado desta sessão (só usuários/perfis).

## Decisão: seed nunca automático

`seed_demo_users()` só roda via `uv run python -m
mcp_engine.core.user_store` — nunca dentro de `main()`/`create_app()`.
Auto-popular um cliente real com credenciais conhecidas
(`admin-token-demo`) na primeira inicialização seria uma vulnerabilidade,
não uma conveniência.

## Validação end-to-end (mesmo método da spec 010: cliente MCP real, não só unit test)

Repetido com um cliente MCP real (`mcp.client.sse.sse_client`) contra
`uv run uvicorn mcp_engine.core.http_app:app`, depois de rodar o seed:
viewer não vê `deletar_banco`/`criar_usuario`/`listar_usuarios` em
`tools/list`, e chamada forçada de `deletar_banco` é negada; admin vê e
executa tudo, incluindo criar um usuário novo (token gerado devolvido uma
vez) e listá-lo (sem o token). Confirmado nos dois transportes (stdio via
`MCP_USER_EMAIL`, SSE via `Authorization: Bearer`) — comportamento idêntico
ao da spec 010, agora lendo do SQLite.

## O que ainda não existe

- **Hash de token** (ver decisão acima).
- **Editar/desativar usuário, trocar perfil** — só criar/listar por agora.
- **Migração de schema (Alembic).** Uma tabela só, `create_all()`
  idempotente é suficiente pro estágio atual; vira necessário quando o
  schema mudar de verdade.

**Arquivos:**
- `mcp_engine/core/user_store.py` (novo)
- `mcp_engine/core/auth.py`, `mcp_server.py`, `http_app.py` (migrados)
- `tests/test_user_store.py` (10 casos), `tests/test_auth.py` (migrado pra
  `SQLUserStore` de `tmp_path`, 18 casos), `tests/test_mcp_server.py`
  (`TestUserManagement`, 5 casos novos)
- `.gitignore` (`clients/*/config/*.db`)
- `docs/specs/011-persistencia-usuarios.md`
- `pyproject.toml`/`uv.lock` (`sqlalchemy`)

**Validação:** `uv run ruff check .` limpo, `uv run pytest -q` — 190 passed
(174 anteriores + 16 novos). Testado manualmente nos dois transportes com
o seed rodado contra `clients/example/config/users.db`.
