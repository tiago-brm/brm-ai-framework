# 010 — RBAC dual-mode (stdio + SSE/FastAPI) (spec)

**Ordem da spec:** depois da spec 009 (agente interpretador) e do trabalho
de integrações reais (changelog 010). Registra o desenho aprovado antes do
código, seguindo o fluxo normal do projeto — a sessão anterior (changelog
010) tinha pulado esse passo por causa de um prazo de demo.

## Escopo

O BRM Engine autentica hoje só no nível de **cliente** (`CLIENT_ID` via env
var — qual escritório). Não existe controle de **quem dentro do escritório**
está chamando uma tool. Esta spec adiciona RBAC com dois perfis
(`admin`/`viewer`), funcionando em dois transportes:

- **stdio (local)** — identidade via variável de ambiente
  (`MCP_USER_EMAIL` ou `MCP_API_TOKEN`).
- **SSE/HTTP (remoto)** — identidade via header `Authorization: Bearer
  <token>`, capturado na conexão inicial (`GET /sse`), servido via FastAPI.

Requisitos de controle de acesso:

1. **Filtragem de `tools/list`** por perfil — viewer só vê `consultar_dados`;
   admin vê `consultar_dados` e `deletar_banco`.
2. **Validação em `tools/call`**, independente da filtragem da lista — uma
   chamada forçada a `deletar_banco` por um viewer é negada, não executa, e
   retorna "Acesso Negado".
3. Mock de "banco de dados" de usuários — dicionário simples, perfil por
   e-mail e por token.

## Decisão de arquitetura

Investigação do SDK `mcp` instalado
(`.venv/lib/python3.11/site-packages/mcp/server/fastmcp/server.py` e
`mcp/server/auth/*`) — duas descobertas guiam o desenho:

1. **`FastMCP.list_tools()` e `.call_tool()` são métodos de instância
   normais**, plugados no server de baixo nível via
   `self._mcp_server.list_tools()(self.list_tools)` dentro do `__init__`.
   Como isso referencia `self.list_tools` (não `FastMCP.list_tools`), uma
   **subclasse que sobrescreve os dois métodos funciona automaticamente**,
   sem tocar em internals do SDK.
2. **O sistema de auth embutido do SDK (`TokenVerifier`/`AuthSettings`) é
   OAuth de recurso real** — exige `issuer_url`/`resource_server_url` e
   validação contra um authorization server externo. Não se aplica a um
   mock de dicionário. Em vez disso: middleware ASGI leve e próprio, que lê
   `Authorization: Bearer <token>` e popula um `contextvars.ContextVar`
   nosso — o mesmo padrão que o `AuthContextMiddleware` do SDK usa por
   baixo, sem o aparato OAuth.

**Por que resolver a identidade uma vez, no handshake do `GET /sse`, cobre a
sessão inteira:** essa conexão é uma única requisição ASGI de longa duração;
o loop que processa `tools/list`/`tools/call` roda como uma task *dentro*
dela (`POST /messages/` só enfileira mensagens pra essa task, não a
reprocessa). `contextvars` propaga automaticamente pra tasks filhas.
Validado na verificação manual (ver changelog 011); se não propagar como
esperado na prática, o plano B é um dicionário `session_id -> perfil`
populado no handshake.

### Isolamento de transporte

Nenhuma tool, nenhuma lógica de negócio, nem o `RBACFastMCP` sabem de onde a
identidade veio. As únicas duas funções cientes de transporte em todo o
sistema são `resolve_stdio_identity()` e `resolve_http_identity()` — ambas
devolvem o mesmo tipo (`UserProfile`) via `set_current_user()`. Tudo o mais
(`RBACFastMCP`, `is_tool_allowed`, `consultar_dados`, `deletar_banco`) só
lê `get_current_user()`. Um terceiro transporte (ex.: WebSocket) exigiria só
um terceiro `resolve_*_identity()`, nada em `rbac_server.py` nem nas tools.

## Componentes

### `mcp_engine/core/auth.py`
`Role` (`str, Enum`), `UserProfile` (`_StrictModel`, mesmo estilo de
`models.py`), mock DB (`MOCK_USERS_BY_EMAIL`/`MOCK_USERS_BY_TOKEN`),
`ANONYMOUS_PROFILE` (viewer — default seguro, nunca admin por omissão),
`ROLE_TOOLS` (tools ausentes daqui continuam visíveis a todos — escopo desta
spec é só as duas tools novas), `current_user_var` (ContextVar),
`set_current_user`/`get_current_user`, `resolve_stdio_identity`,
`resolve_http_identity`, `is_tool_allowed`.

### `mcp_engine/core/rbac_server.py`
`RBACFastMCP(FastMCP)` — `list_tools()` filtra pelo papel do usuário atual;
`call_tool()` valida antes de delegar pro `super().call_tool()`, registra
`AuditEvent(decision=DENIED)` via o `AuditSink` do engine se negado, e
devolve `{"allowed": False, "reason": "Acesso negado: ..."}` — mesmo formato
de resposta usado em `call_skill`/`consultar_processo_datajud`/etc.

### Tools mock: `consultar_dados` / `deletar_banco`
Métodos de `BRMEngine`, registrados em `build_server()`. `deletar_banco`
faz uma segunda checagem interna de `get_current_user()` antes de "agir" —
defesa em profundidade além do gate de transporte do `RBACFastMCP`.

### `mcp_engine/core/http_app.py`
`create_app(engine=None) -> FastAPI` — monta `RBACFastMCP.sse_app()` sob
FastAPI, com middleware que resolve identidade por request via
`resolve_http_identity(request.headers.get("authorization"))`.

## Fora de escopo

- Retrofit de RBAC nas tools já existentes (`call_skill`, `vault_*`,
  `consultar_processo_datajud`) — ficam visíveis a todos os perfis por
  enquanto. Extensão natural futura: adicionar entradas em `ROLE_TOOLS`.
- Persistência real de usuários (hoje é dicionário em memória, não
  YAML/banco versionado por cliente).
- Streamable-HTTP (só SSE é implementado; `FastMCP` já suporta os dois, mas
  o pedido foi especificamente SSE).

## Critério de pronto

- `mcp_engine/core/auth.py`, `rbac_server.py`, `http_app.py` implementados
- `consultar_dados`/`deletar_banco` registrados como tools
- `main()` (stdio) resolve identidade uma vez antes do loop bloqueante
- `tests/test_auth.py` e `tests/test_rbac_server.py` cobrindo os casos do
  requisito (viewer/admin, list filtrado, call negado com auditoria)
- `docs/changelog/011-rbac-dual-mode.md` escrito depois, com validação
  manual dos dois transportes
