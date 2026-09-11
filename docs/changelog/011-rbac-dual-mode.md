# 011 — RBAC dual-mode (stdio + SSE/FastAPI)

**O quê:** controle de acesso por perfil (`admin`/`viewer`) nos dois
transportes do engine — stdio (identidade via `MCP_USER_EMAIL`/
`MCP_API_TOKEN`) e o novo transporte remoto SSE/FastAPI (identidade via
`Authorization: Bearer <token>` na conexão inicial). Spec aprovada antes do
código, ver `docs/specs/010-rbac-dual-mode.md` para o raciocínio completo de
arquitetura.

- `mcp_engine/core/auth.py` — `Role`/`UserProfile` (mesmo estilo de
  `models.py`: `str, Enum` + `_StrictModel`), mock DB (`MOCK_USERS_BY_EMAIL`/
  `MOCK_USERS_BY_TOKEN`), `ANONYMOUS_PROFILE` (viewer — default seguro),
  `ROLE_TOOLS`, e a fronteira de isolamento de transporte:
  `resolve_stdio_identity()`/`resolve_http_identity()` são as únicas duas
  funções cientes de onde a identidade veio; tudo mais (incluindo as
  próprias tools) só chama `get_current_user()`.
- `mcp_engine/core/rbac_server.py` — `RBACFastMCP(FastMCP)`, subclasse que
  sobrescreve `list_tools()` (filtra pelo perfil atual) e `call_tool()`
  (revalida antes de delegar — cobre chamada forçada a uma tool que nem
  apareceu na lista, registra `AuditEvent(decision=DENIED)`, devolve
  `{"allowed": False, "reason": "Acesso negado: ..."}`, mesmo formato do
  resto do engine).
- `mcp_engine/core/mcp_server.py` — `build_server()` agora constrói
  `RBACFastMCP` (não mais `FastMCP` puro); `main()` (stdio) resolve a
  identidade uma vez, antes do loop bloqueante; duas tools novas
  (`consultar_dados` — sem restrição; `deletar_banco` — admin only, com uma
  segunda checagem de `get_current_user()` dentro do próprio método, além
  do gate do `RBACFastMCP`).
- `mcp_engine/core/http_app.py` (novo) — `create_app()` monta
  `RBACFastMCP.sse_app()` sob FastAPI, com middleware que resolve identidade
  por request via `resolve_http_identity(request.headers.get("authorization"))`.

## Por que a subclasse de `FastMCP` funciona sem tocar em internals do SDK

`FastMCP.__init__` registra os handlers assim:
`self._mcp_server.list_tools()(self.list_tools)`. Como isso referencia
`self.list_tools` (resolução de atributo em tempo de execução, não
`FastMCP.list_tools` direto), uma subclasse que sobrescreve `list_tools`/
`call_tool` é automaticamente usada — Python resolve `self.list_tools` pela
MRO da instância real. Confirmado lendo `mcp/server/fastmcp/server.py` do
SDK instalado antes de escrever a spec.

## Decisão: não usar o `TokenVerifier`/`AuthSettings` embutido do SDK

O SDK `mcp` tem um sistema de auth de verdade (`TokenVerifier` +
`AuthSettings`), mas é modelado como OAuth de recurso real —
`FastMCP.__init__` recusa `token_verifier` sem `auth=AuthSettings(...)`, que
por sua vez exige `issuer_url`/`resource_server_url`. Não faz sentido pra um
mock de dicionário. Em vez disso: middleware ASGI próprio (mesmo padrão que
o `AuthContextMiddleware` do SDK usa por baixo — `contextvars`, confirmado
lendo o código dele — só sem o aparato OAuth).

## Risco identificado na spec, validado na prática: propagação de `contextvars` na sessão SSE

A dúvida da spec 010: a conexão `GET /sse` é uma requisição ASGI de longa
duração; `POST /messages/` só enfileira mensagens pra essa task, não a
reprocessa — então resolver a identidade uma vez no handshake do `GET /sse`
deveria bastar pra sessão inteira, já que `contextvars` propaga pra tasks
filhas. Validado com um cliente MCP real (`mcp.client.sse.sse_client`, não
só `curl`): subiu o servidor via `uvicorn`, conectou com token de viewer e
depois de admin, listou tools e chamou `deletar_banco` nos dois. Funcionou
exatamente como esperado nos dois casos — plano B (dicionário
`session_id -> perfil`) não foi necessário.

## O que ainda não existe

- **RBAC nas tools pré-existentes.** `call_skill`, `vault_*`,
  `consultar_processo_datajud` continuam visíveis/executáveis por qualquer
  perfil — escopo desta spec foram só as duas tools de demonstração.
  Extensão natural: adicionar entradas em `ROLE_TOOLS`.
- **Persistência real de usuários.** Mock DB é um dicionário em memória, não
  uma tabela versionada por cliente.
- **Streamable-HTTP.** Só SSE foi implementado (era o pedido); `FastMCP` já
  suporta os dois transportes, então adicionar é possível depois.

**Arquivos:**
- `mcp_engine/core/auth.py`, `rbac_server.py`, `http_app.py` (novos)
- `mcp_engine/core/mcp_server.py` (`build_server` usa `RBACFastMCP`, `main()`
  resolve identidade stdio, tools `consultar_dados`/`deletar_banco`)
- `tests/test_auth.py` (17 casos), `tests/test_rbac_server.py` (7 casos)
- `docs/specs/010-rbac-dual-mode.md`
- `pyproject.toml`/`uv.lock` (`fastapi`)

**Validação:** `uv run ruff check .` limpo, `uv run pytest -q` — 174 passed
(150 anteriores + 24 novos). Testado manualmente nos dois transportes: stdio
com `MCP_USER_EMAIL` (viewer vs admin, filtragem de `list_tools` e negação
de `deletar_banco` confirmadas); SSE/FastAPI com um cliente MCP real
conectado via `uv run uvicorn mcp_engine.core.http_app:app`, mesmo resultado
sobre a rede.
