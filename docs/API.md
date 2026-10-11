# API

> Fonte de verdade dos **contratos**: tools MCP (a superfície principal), REST do painel e eventos de auditoria. Mudou uma tool ou rota? Atualize aqui no mesmo commit.
> Verificado contra `mcp_engine/core/mcp_server.py` e `api_router.py` em 2026-10-11.

## Conventions

| | |
|---|---|
| **Superfícies** | (1) MCP — stdio e SSE; (2) REST `/api/*` — só o painel `super_admin` |
| **Base URL (HTTP)** | `http://localhost:8000` (`uvicorn mcp_engine.core.http_app:app --port 8000`) |
| **MCP/SSE** | `GET /sse` abre o stream; mensagens JSON-RPC em `POST /messages/?session_id=…`. A identidade é resolvida no `GET /sse` e vale para a sessão |
| **MCP/stdio** | `uv run python -m mcp_engine.core.mcp_server`; identidade por `MCP_USER_EMAIL` ou `MCP_API_TOKEN` na subida |
| **Authentication** | `Authorization: Bearer <token>` (HTTP). Sem token/inválido → `anonymous` (`viewer`) |
| **Authorization** | MCP: `tool_permissions` + regras + políticas ([SECURITY.md](SECURITY.md) §2). REST: `super_admin` em toda rota |
| **Versioning** | Não há versão de URL. Configs usam `schema_version: 1`. Mudança incompatível = nova versão de schema + migração |
| **Error format (MCP)** | Tools de negócio **não levantam**: `{"allowed": false, "reason": "<pt-BR>", "proximo_passo"?: "…", "rule_id"?: "…"}` |
| **Error format (REST)** | `{"detail": "<mensagem>"}` — `400` ação negada pelo motor, `403` perfil sem acesso, `404` recurso/simulador desligado, `422` validação do FastAPI |
| **Pagination** | Nenhuma: listas retornam tudo (`total` + itens). `limite` no `POST /simulador/vault` e `limit` em `vault_search` |
| **Rate limits** | Nenhum ([SECURITY.md](SECURITY.md) L-06) |
| **CORS** | `*` (L-04) |
| **Idioma** | Campos de contrato misturam inglês técnico e português (`allowed`/`reason` × `proximo_passo`/`perfil_email`); **não renomeie** sem versionar |

---

## 1. MCP — tools

**Acesso padrão** é o que o seed de `tool_permissions` define. Tool sem nenhuma linha = aberta a todos os perfis, **inclusive `anonymous`** — por isso toda tool sensível precisa de linha no seed (`_DEFAULT_PERMISSIONS`). `super_admin` acessa tudo. Altere em runtime com `atualizar_permissao` ou pelo painel.

### 1.1 Skills do cliente (geradas de `skills.yaml`)

- Uma tool por skill, com o `name` da skill, parâmetros tipados (`type`, `required`, `description` de `parameters`) e descrição + `examples`. Parâmetros declarados em `derived_facts` **não aparecem**.
- Equivalente genérico: `call_skill(skill_name, arguments)`.
- **Acesso:** aberto por padrão; protegido por **regras** (`actor_role`) e pelo interceptor.

**Resposta (todas as skills / `call_skill`):**

```jsonc
// permitida
{"allowed": true, "reason": null, "result": "<texto>", "payload_digest": "<sha256>", "vault": [ /* se skill.vault_read */ ]}
// negada por regra
{"allowed": false, "reason": "<message da regra>", "rule_id": "<id>", "proximo_passo": "<next_step>", "result": "", "payload_digest": null}
// exige aprovação
{"allowed": false, "reason": "human approval required", "rule_id": "<id>", "message": "<message>", "proximo_passo": "<next_step>", "result": "", "payload_digest": null}
// bloqueada pelo interceptor
{"allowed": false, "reason": "domain not in allowlist: <host>" | "private IP blocked: <host>" | "DLP: found <label>", "result": "", "payload_digest": null}
// skill inexistente
{"allowed": false, "reason": "skill not found: <nome>", "result": "", "payload_digest": null}
```
Efeitos: `enviar_email` envia de verdade só com Resend/SMTP configurados e fora de simulação; as demais skills retornam resposta **simulada** (Fase 1).

### 1.2 Catálogo fixo

| Tool | Parâmetros | Acesso padrão | O que faz / retorna |
|---|---|---|---|
| `call_skill` | `skill_name`, `arguments{}` | todos | Executa uma skill pelo nome (ver §1.1) |
| `query_audit_log` | `start_date?`, `end_date?` (ISO-8601), `action?`, `decision?` (`allowed`\|`denied`\|`pending_approval`) | `admin` | `{total, events[{occurred_at, actor, action, decision, rule_id}]}` (sem `details`) |
| `vault_search` | `query`, `limit=10` | todos | `{total, notes[VaultNote]}` filtrado por `vault_access`; com política: `+ ocultas`, `politicas[]` |
| `vault_list_notes` | — | todos | `{total, notes[]}` filtrado por política |
| `vault_get_related` | `note_id` | todos | `{total, notes[]}` (backlinks/links), filtrado |
| `vault_add_note` | `title`, `content`, `tags?[]` | `admin`; **com `vault_access.yaml`**, além disso só `write_roles` | `VaultNote`; negado → `{allowed:false, reason, proximo_passo}` e evento `DENIED` |
| `vault_interpret_and_propose` | `note_id` | `admin` | `{draft_id, kind, preview}` ou `{allowed:false, reason}`; DLP de saída estrito |
| `vault_list_drafts` | `kind?` (`rule`\|`skill`\|`document`), `status?` | todos | `{total, drafts[]}` |
| `vault_promote_draft` | `draft_id`, `kind` (`rule`\|`skill`) | `admin` | `{promoted:true, draft_id, kind}` ou `{allowed:false, reason}` |
| `vault_generate_document` | `instrucoes` | `admin` | `{draft_id, kind:"document"`, preview}` ou `{allowed:false, reason}` (DLP/credenciais) |
| `vault_promote_document` | `draft_id` | `admin` | `{promoted:true, draft_id, note_id}` (grava a nota no vault; auditado) ou `{allowed:false, reason}` |
| `consultar_processo_datajud` | `numero_processo` | todos | `{allowed, encontrado, tribunal, grau, classe, …, vault_note_id}` ou `{allowed:false, reason}`; grava nota no vault |
| `consultar_dados` | — | `viewer`, `admin` (demo) | `{allowed, data[]}` |
| `deletar_banco` | — | `admin` (demo, mock) | `{allowed, result}`; reconfere o perfil por dentro |
| `criar_usuario` | `email`, `role` (`admin`\|`viewer`\|`super_admin`), `api_token?` | `admin` | `{allowed, identity, role, api_token}` — token **só nesta resposta**; `super_admin` só por `super_admin` |
| `listar_usuarios` | — | `admin` | `{total, users[{identity, role}]}` (nunca token) |
| `atualizar_perfil_usuario` | `email`, `role` | `super_admin` | `{allowed, identity, role}` ou motivo (`usuário não encontrado`, …) |
| `listar_permissoes` | — | `super_admin` | `{total, permissions[{role, tool_name, allowed}]}` |
| `atualizar_permissao` | `role`, `tool_name`, `allowed` | `super_admin` | `{allowed, role, tool_name, is_allowed}` |

**Negativa de RBAC** (qualquer tool): `{"allowed": false, "reason": "Acesso negado: perfil '<role>' não pode executar '<tool>'."}` + evento `DENIED` com `details.reason = "rbac_denied"`.

**`VaultNote`:** `note_id`, `client_id`, `title`, `content`, `frontmatter{}`, `links[]`, `tags[]`, `updated_at`, `content_hash`, `schema_version`. Com `metadata_only`: `title` = `paciente_id` (ou `(restrito)`), `content` vazio, `frontmatter` reduzido a `expose`.

### 1.3 Instruções do servidor (enviadas ao modelo)
`Cliente: <client_id>. <context>` + regras fixas: use as tools tipadas; resolva pessoas/casos por `vault_search` e passe o **ID**; se houver mais de um candidato, **pergunte antes de agir**; nunca informe fatos de segurança (vínculo, matrícula, frequência, idade); se negado/pendente, explique `reason` + `proximo_passo` e **não contorne**.

---

## 2. REST do painel — `/api/*`

Todas exigem `Authorization: Bearer <token de super_admin>`; senão `403 {"detail":"esta API é restrita ao perfil super_admin"}`. Registradas **antes** de `mount("/")` no `http_app`. Sem corpo = `GET`.

| Método e rota | Corpo / query | Resposta | Equivale a |
|---|---|---|---|
| `GET /api/me` | — | `{identity, role}` | identidade atual |
| `GET /api/users` | — | `{total, users[]}` | `listar_usuarios` |
| `POST /api/users` | `{email, role, api_token?}` | `201` `{allowed, identity, role, api_token}`; `400` com o motivo | `criar_usuario` |
| `PATCH /api/users/{email}/role` | `{role}` | `{allowed, identity, role}`; `400` | `atualizar_perfil_usuario` |
| `GET /api/permissions` | — | `{total, permissions[]}` | `listar_permissoes` |
| `PUT /api/permissions` | `{role, tool_name, allowed}` | `{allowed, role, tool_name, is_allowed}` | `atualizar_permissao` |
| `GET /api/tools` | — | `{total, tools[{name, description}]}` (catálogo **completo**) | `list_tools` como `super_admin` |
| `GET /api/rules` | — | `{total, rules[{id, description, condition, effect, message}]}` (sem `next_step`) | regras ativas (somente leitura) |
| `GET /api/vault/notes` | — | `{total, notes[]}` | `vault_list_notes` |
| `GET /api/audit` | `start_date?`, `end_date?`, `action?`, `decision?` | `{total, events[]}` | `query_audit_log` |

### Simulador (só com `simulador: true` no `client_config.yaml`; senão `404`)

| Método e rota | Corpo | Resposta |
|---|---|---|
| `GET /api/simulador/perfis` | — | `{total, perfis[{identity, role, rotulo}]}` |
| `GET /api/simulador/skills` | — | `{total, skills[{name, description, examples[], parameters{nome:{type, required, description}}}]}` (sem parâmetros derivados) |
| `GET /api/simulador/cenarios` | — | `{total, cenarios[{id, titulo, skill, argumentos, perfil, nota}]}` (de `demo_scenarios.yaml`; vazio se ausente) |
| `POST /api/simulador/executar` | `{perfil_email, skill, argumentos{}}` | `{perfil{identity, role}, decisao: allowed\|denied\|pending_approval, resultado{…}, fatos_derivados{}, dlp{}}`; `404` perfil desconhecido |
| `POST /api/simulador/vault` | `{perfil_email, consulta, limite=6}` | `{perfil, total, ocultas, politicas[], notas[{note_id, title, tipo, modo: completa\|so_ficha, trecho}]}` |
| `GET /api/simulador/matriz` | — | `{perfis[{identity, role, rotulo}], cenarios[{id, titulo, celulas[{perfil, efeito, rule_id}]}]}` — só cenários com `in_matrix: true`; **não escreve auditoria** |

Simulação: a identidade do perfil vale só durante a requisição; nenhum efeito externo; cada execução grava `simulacao:<skill|vault>` com `executado_por` (real) e `como` (simulado).

---

## 3. Eventos de auditoria

Esquema e cadeia: [DATABASE.md](DATABASE.md) §9. Contrato de gravação: `AuditSink.record(AuditEvent)` / `iter_events(client_id)`. Ações registradas e decisão: ver lista em §9 daquele documento. Novo evento = nova `action` documentada lá e **nunca** com dado sensível em `details` ([SECURITY.md](SECURITY.md) §7).

## 4. Checklist de um endpoint/tool novo

- **Purpose** e perfil exigido (`tool_permissions` ou `Role.SUPER_ADMIN`)?
- Parâmetros tipados, valores permitidos, status/`reason` de erro?
- Autenticação (quem é) separada de autorização por propriedade (o que pode ver)?
- Efeitos colaterais (escrita, e-mail, chamada externa) e **idempotência** (repetir o pedido)?
- Passa por regras/interceptor/auditoria? Fato de segurança é derivado, não informado?
- Testes: sucesso, entrada inválida, sem autenticação, perfil proibido ([TESTING.md](TESTING.md)).
- Registrada em `build_server()` e documentada **aqui**.
