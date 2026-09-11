# 011 — Persistência de usuários/RBAC (SQLite + SQLAlchemy) (spec)

**Ordem da spec:** depois da 010 (RBAC dual-mode). O RBAC da spec 010 usa um
dicionário em memória como "banco de usuários" — funciona pra demonstrar o
mecanismo, mas não sobrevive a um restart, e adicionar alguém exige editar
código-fonte. Esta spec troca isso por persistência real.

## Escopo

Só **usuários e permissionamento/perfis**. Regras, skills e audit continuam
YAML + JSONL — essa troca não mexe neles. SQLite local hoje, via SQLAlchemy,
para que migrar para Postgres/nuvem depois seja trocar a connection string,
não reescrever a camada de acesso.

Inclui tools MCP para gerenciar usuários (`criar_usuario`/`listar_usuarios`,
admin-only) — sem isso, persistir usuários sem conseguir administrá-los é
meio-caminho.

## Decisões de arquitetura

1. **Um SQLite por cliente** (`clients/<cliente>/config/users.db`), não um
   banco único multi-tenant com coluna `client_id`. Segue o padrão que todo
   o resto do projeto já usa — `FileSystemRuleProvider`,
   `FileSystemSkillProvider`, `FileSystemClientConfigProvider`,
   `DraftStore`: todos resolvem `config_root / client_id / "config" / <algo>`.
   O limite entre clientes já é "qual arquivo", não uma coluna.
2. **SQLAlchemy 2.0 síncrono** (`DeclarativeBase`/`Mapped`/`mapped_column`,
   `create_engine` sync) — todo o resto do engine é síncrono; SQLite local é
   rápido o bastante para não precisar de I/O assíncrono, mesmo chamado de
   dentro do middleware async do FastAPI.
3. **Sem Alembic por agora.** Uma tabela, `Base.metadata.create_all()`
   idempotente no `__init__` do store. Migração de schema vira necessária
   quando o schema mudar de verdade — não antes.
4. **Token em texto puro, não hasheado.** Simplificação assumida e
   documentada, não escondida: já é uma melhoria sobre o mock anterior
   (tokens hardcoded no código-fonte). Produção real exigiria hash — fica
   registrado como próximo passo, fora do escopo confirmado desta spec.

## Isolamento de transporte preservado

`resolve_stdio_identity()`/`resolve_http_identity()` continuam as únicas
funções cientes de transporte (env var vs. header) — agora recebem um
`SQLUserStore` como parâmetro em vez de ler dicionários globais do módulo.
`RBACFastMCP`, `is_tool_allowed`, `get_current_user()`/`current_user_var`:
inalterados — nunca tocaram em storage, só na fronteira de identidade que já
existia.

`SQLUserStore` vira mais um provider do `BRMEngine`, no mesmo padrão de
injeção de `vault_provider`. `main()` (stdio) passa a construir o `engine`
antes de resolver a identidade (para poder usar `engine.user_store`) — hoje
é o contrário.

## Componentes

### `mcp_engine/core/user_store.py`
`UserRow` (SQLAlchemy model: id, email único, api_token único nullable,
role, created_at), `UserAlreadyExistsError`, `SQLUserStore` (`get_by_email`,
`get_by_token`, `create_user` — gera token via `secrets.token_urlsafe(24)`
se omitido, devolve o token em texto puro só nesse momento —,
`list_users` — nunca inclui token). `seed_demo_users(store)` explícito, não
automático (nunca roda sozinho no `main()`/`create_app()` — auto-seedar
credenciais conhecidas na inicialização de um cliente real seria um
problema de segurança), acionável via `uv run python -m
mcp_engine.core.user_store`.

### Tools novas: `criar_usuario` / `listar_usuarios`
Métodos de `BRMEngine`, delegando pro `self.user_store`. Adicionadas a
`ROLE_TOOLS[Role.ADMIN]` apenas — um viewer não vê nem pode chamar.

## Fora de escopo

- Hash de token (ver decisão 4 acima).
- Edição/desativação de usuário, troca de perfil — só criar/listar por
  agora.
- Migração de regras/skills/audit para SQL — permanece fora, por decisão
  explícita do usuário nesta sessão.

## Critério de pronto

- `mcp_engine/core/user_store.py` implementado e testado isoladamente
- `auth.py`, `mcp_server.py`, `http_app.py` migrados do dict pro store
- `criar_usuario`/`listar_usuarios` funcionando e restritos a admin
- Testes manuais da spec 010 repetidos com sucesso sobre o novo storage
- `docs/changelog/012-persistencia-usuarios.md` escrito depois
