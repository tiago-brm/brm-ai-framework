# Database & Data Model

> **Fonte de verdade do modelo de dados.** Não há um SGBD central: o dado de cada cliente vive em arquivos e SQLites **isolados por cliente**. A IA não deve inventar tabelas, campos ou formatos — mude este documento primeiro.

## 1. Data Stores

| Store | Tecnologia | Caminho | Versionado? | Papel |
|---|---|---|---|---|
| Usuários e permissões | SQLite + SQLAlchemy 2 (síncrono) | `CONFIG_ROOT/<cliente>/config/users.db` | **Não** (`clients/*/config/*.db` no `.gitignore`) | Identidade, perfil, permissão por tool |
| Configuração | YAML (`schema_version: 1`) | `CONFIG_ROOT/<cliente>/config/*.yaml` | Sim | Regras, skills, guardrails, acesso ao vault, cenários |
| Rascunhos | JSONL append-only | `CONFIG_ROOT/<cliente>/config/{rules,skills,documents}.draft.jsonl` | Sim | Propostas dos agentes aguardando promoção |
| Auditoria | JSONL append-only, hash-chain | `DATA_ROOT/<cliente>/audit.jsonl` | **Não** (`data/` no `.gitignore`) | Trilha de decisões |
| Índice do vault | SQLite + FTS5 + `sqlite-vec` | `DATA_ROOT/<cliente>/vault_index.sqlite3` | **Não** | Cache **reconstruível** da busca |
| Vault | Markdown + frontmatter (Obsidian) ou páginas Notion | `vault_root` (padrão `CONFIG_ROOT/<cliente>/vault/`) | Depende do cliente | Fonte de verdade do conhecimento |

Variáveis: `CLIENT_ID` (padrão `example`), `CONFIG_ROOT` (padrão `clients`), `DATA_ROOT` (padrão `data`).

**Regra de ouro:** os `.md`/páginas são a fonte de verdade; o índice é descartável (apague e rode `reindex`).

## 2. Naming Conventions

- **Tabelas SQL:** `snake_case`, plural (`users`, `tool_permissions`).
- **Colunas:** `snake_case`. **PK:** `id` inteiro autoincremental. **FK:** `<entidade>_id` (ainda não há FKs entre as tabelas do `users.db`).
- **Timestamps:** `created_at` / `occurred_at` / `updated_at`, **UTC**, serializados em ISO-8601.
- **Chaves YAML/JSON:** `snake_case`; valores de domínio e mensagens em português; identificadores técnicos (`effect`, `allow`, `deny`) em inglês.
- **IDs de regra/skill/draft:** `kebab-case` para regras (`prontuario-somente-psicologo-vinculado`), `snake_case` para skills (`consultar_prontuario`, que vira nome de tool MCP), `draft_id` hexadecimal curto.
- **IDs de domínio no vault:** `PAC-001`, `ALU-017` (prefixo do tipo + número).

## 3. Tables (`users.db`)

### `users`
Pessoas que podem se identificar no engine do cliente.

| Coluna | Tipo | Regras |
|---|---|---|
| `id` | INTEGER | PK |
| `email` | TEXT | **único**, indexado — é a identidade (`actor`) usada em regras e auditoria |
| `api_token` | TEXT | **único**, indexado, anulável — Bearer / `MCP_API_TOKEN`; **hoje em texto puro** |
| `role` | TEXT | `admin` \| `viewer` \| `super_admin` (enum `Role`) |
| `created_at` | DATETIME | UTC |

### `tool_permissions`
Fonte de verdade em runtime do RBAC por tool (substituiu o dict `ROLE_TOOLS`).

| Coluna | Tipo | Regras |
|---|---|---|
| `id` | INTEGER | PK |
| `role` | TEXT | enum `Role` |
| `tool_name` | TEXT | nome da tool MCP |
| `allowed` | BOOLEAN | |

`UNIQUE(role, tool_name)`.

**Semântica (crítica):** *nenhuma* linha para uma tool = tool **aberta a todo perfil**, inclusive `anonymous`. *Ao menos uma* linha (de qualquer perfil) = tool **restrita**; um perfil sem linha própria é negado. `super_admin` nunca precisa de linha (bypass em `is_tool_allowed`). Seed automático e idempotente **por tool** (`_DEFAULT_PERMISSIONS`): só semeia tools sem nenhuma linha, então bancos antigos ganham os padrões novos sem perder escolhas do `super_admin`: `viewer`: `consultar_dados`; `admin`: `consultar_dados`, `deletar_banco`, `criar_usuario`, `listar_usuarios`, `vault_promote_draft`, `vault_promote_document`, `vault_interpret_and_propose`, `vault_generate_document`, `vault_add_note`, `query_audit_log` (sim) e `atualizar_perfil_usuario`, `listar_permissoes`, `atualizar_permissao` (não).

## 4. Relationships

- `users.role` → `Role` (enum no código, sem tabela).
- `tool_permissions.role` + `tool_name` → combinação com `Role` e com o catálogo de tools registradas em `build_server()` (sem FK; uma `tool_name` inexistente é aceita — ver §7).
- Identidade (`users.email`) ↔ vault: nota com `psicologo_responsavel: <email>` define vínculo (fato derivado `vinculo_com_paciente`, política `vinculo`).
- Wikilinks entre notas: `note_links(source, target_key)`.

## 5. Constraints

- **Required:** `users.email`, `users.role`, `tool_permissions.(role, tool_name, allowed)`.
- **Unique:** `users.email`, `users.api_token`, `(tool_permissions.role, tool_permissions.tool_name)`, `notes_meta.note_id`.
- **Foreign keys:** nenhuma declarada.
- **Config YAML:** modelos Pydantic `extra="forbid"` — chave desconhecida falha na carga (`ConfigLoadError`). `schema_version` deve ser `1`.
- `SkillDefinition.allowed_domains` não pode ser vazio; `ClientConfig.domain_allowlist` não pode ser vazio.

## 6. Indexes

- `users.email`, `users.api_token` (únicos): login por e-mail (stdio) e por token (HTTP).
- `idx_meta_link_key` em `notes_meta(link_key)` e `idx_links_target` em `note_links(target_key)`: backlinks são consulta indexada, não varredura.

## 7. Data Validation

- Toda config passa por Pydantic na leitura; todo evento de auditoria é `AuditEvent` (`extra="forbid"`, `frozen`).
- `criar_usuario`/`atualizar_perfil_usuario` validam `role` contra o enum; `atualizar_permissao` valida só `role` — **`tool_name` não é validado** contra o catálogo.
- Regras: condição JSONLogic malformada levanta `RuleEvaluationError`; no `promote_draft`, o draft é revalidado por Pydantic (`Rule`/`SkillDefinition.model_validate`) antes de gravar; a checagem de JSONLogic roda na proposta. Regra promovida entra **no fim** de `rules.yaml` (primeira que casa decide, então uma regra anterior pode ofuscá-la), o arquivo é reescrito por `yaml.safe_dump` (perde comentários/formatação) e só vale após reiniciar o servidor.

## 8. Soft Delete

Não há. Auditoria e rascunhos são **append-only**: o estado atual de um rascunho é "última linha por `draft_id`". Usuários não são removidos (só criados e promovidos/rebaixados). Remover regra é editar `rules.yaml` (git revert).

## 9. Auditing

Cada decisão relevante vira um `AuditEvent` em `audit.jsonl`.

| Campo | Tipo | Observação |
|---|---|---|
| `schema_version` | int | `1` |
| `client_id` | str | |
| `occurred_at` | datetime UTC | |
| `actor` | str | e-mail logado, ou `claude` sem login |
| `action` | str | nome da skill/tool ou evento (tabela abaixo) |
| `decision` | enum | `allowed` \| `denied` \| `pending_approval` |
| `rule_id` | str? | regra que decidiu |
| `payload_digest` | str? | SHA-256 do texto de resposta (não o conteúdo) |
| `details` | dict | ver nota de segurança abaixo |
| `previous_hash` | str? | só no arquivo: SHA-256 da linha anterior; **não** faz parte do modelo |

**Cadeia:** linha 1 tem `previous_hash: null`; cada linha seguinte guarda o SHA-256 (UTF-8) da linha anterior serializada. `iter_events` levanta `AuditChainError(linha, esperado, encontrado)` na primeira divergência. Detecta edição/remoção; **não** impede nem assina.

**Ações registradas:** `<skill>` (decisão da skill), `<skill>:fato_divergente`, `simulacao:<skill|vault>`, `vault_search`, `vault_list_notes`, `vault_get_related`, `vault_add_note`, `vault_external_edit`, `vault_write_conflict`, `vault_generate_draft`, `vault_promote_draft`, `vault_promote_error`, `vault_generate_document`, `vault_promote_document`, `vault_promote_document_error`, `vault_content_egress_check`, `vault_document_egress_check`, `consultar_processo_datajud`, e `<tool>` com `details.reason = "rbac_denied"`.

> `details.dlp_matches` guarda só **rótulo → contagem** (ex.: `{"CPF": 2}`), nunca o valor casado (inclui os eventos `*_egress_check`).

## 10. Sensitive Data

| Dado | Onde | Proteção hoje |
|---|---|---|
| `api_token` | `users.db` | Texto puro; arquivo fora do git; **sem hash** (L-03) |
| Prontuário, hipóteses, CID, CPF, telefone | Vault (notas) e respostas | `vault_access.yaml` (nota a nota), DLP no retorno, DLP de saída antes de LLM, embeddings **locais** |
| `notion_token` | `client_config.yaml` **ou** env `NOTION_TOKEN` | Preferir o env; `.env` no `.gitignore` (L-07) |
| Conteúdo de nota e valores de DLP | Auditoria | Não gravados (só contagens/políticas/`payload_digest`) |
| Chaves de LLM/SMTP/Resend | Variáveis de ambiente | Nunca em arquivo versionado |

Retenção e exclusão de dado pessoal (LGPD) **não estão definidas** — ver Open Questions em [PRD.md](PRD.md).

## 11. Migration Rules

Mudança de schema deve: **1)** estar documentada aqui; **2)** ter migração; **3)** ser testada; **4)** não quebrar dado de produção.

- `users.db`: hoje `Base.metadata.create_all()` idempotente, **sem Alembic**. Criar tabela nova é seguro; **alterar/remover coluna não é coberto** — antes disso, adote Alembic (ou migração SQL versionada) e teste com um `users.db` real.
- `*.yaml`: versionadas por `schema_version`. Mudar o formato exige novo valor de versão e migração dos arquivos existentes.
- `vault_index.sqlite3`: descartável — mudou o schema do índice, apague e reindexe. Dimensão do vetor (`FLOAT[384]`) está atrelada ao modelo de embeddings.
- `audit.jsonl`: **nunca** reescrever. Novo campo = opcional com default; a cadeia é calculada sobre a linha serializada.

## 12. Seed Data

- `uv run python -m mcp_engine.core.user_store` cria 3 usuários demo (idempotente): `brm@brm.com.br` (`super_admin`, `super-admin-token-demo`), `socio@escritorio.com.br` (`admin`, `admin-token-demo`), `estagiario@escritorio.com.br` (`viewer`, `viewer-token-demo`). **Nunca** roda sozinho nem deve rodar em cliente real.
- `scripts/preparar_demo_chat.py [perfil]` cria os perfis do cliente `instituto-afw` (8 pessoas fictícias) e imprime a configuração do Claude Desktop.
- Vaults demo fictícios em `clients/clinica-psi/vault/` e `clients/instituto-afw/vault/`.

## 13. Config schemas (`CONFIG_ROOT/<cliente>/config/`)

### `client_config.yaml` — `ClientConfig`
`client_id`; `context?` (descrição curta do negócio, vai nas instruções do servidor); `simulador` (padrão `true`); `domain_allowlist[]` (obrigatório, não vazio, `fnmatch`); `block_private_ip` (padrão `true`); `dlp_patterns[]` (`pattern` regex, `label`, `action: mask|deny`); `vault_root?` (explícito → deve existir); `vault_provider: obsidian|notion` (padrão `obsidian`); `notion_token?`; `notion_root_page_id?`.

### `rules.yaml` — `RuleSet`
`schema_version: 1`, `client_id`, `rules[]` com `id`, `description`, `condition` (JSONLogic), `effect: allow|deny|require_approval`, `message?`, `next_step?`. **Ordem importa** (primeira que casa decide; sem match = `allow`). Fatos disponíveis: `skill_name`, `actor`, `actor_role`, `origem_sistema`, argumentos da skill, fatos derivados.

### `skills.yaml` — `SkillSet`
`skills[]`: `name` (vira tool MCP), `description`, `method` (GET/POST/PUT/PATCH/DELETE), `url_template`, `allowed_domains[]` (não vazio), `parameters{nome: {type, required, description}}`, `derived_facts{}`, `examples[]`, `vault_read?`, `timeout_seconds` (30).
- `derived_facts.<fato>`: `vault_match{campo: literal|$argumento}`, `field`, `transform: value|equals_actor|in`, `values[]`, `if_true`, `if_false`, `if_missing`.
- `vault_read`: `types[]` (não vazio), `query_from` (argumento usado como consulta), `limit` (3).

### `vault_access.yaml` — `VaultAccess`
`schema_version: 1`, `client_id`, `default: allow|deny` (padrão `deny`), `write_roles[]` (padrão `admin`, `super_admin`), `policies[]`: `id`, `when` (JSONLogic), `effect: allow|metadata_only|deny`, `expose[]`, `audit?`. Fatos: `actor`, `actor_role`, `note.<campo do frontmatter>` (+ `note.note_id`), `vinculo` (`note.psicologo_responsavel == actor`). **Arquivo opcional**: ausente = sem filtro.

### `demo_scenarios.yaml` — `DemoScenarios`
`profiles{email: rótulo}`, `scenarios[]`: `id`, `title`, `skill`, `arguments`, `profile?`, `note?`, `in_matrix` (padrão `true`). Opcional.

### `*.draft.jsonl`
Uma linha por *snapshot*: `draft_id`, `from_note_id`, `generated_at`, `generated_by`, `kind` (`rule|skill|document`), `content{}`, `status` (`pending|approved|rejected`), `reviewed_by`, `reviewed_at`. Estado atual = última linha por `draft_id`.

## 14. Vault: convenção de notas

- Arquivo `.md` com frontmatter YAML; `note_id` = caminho relativo ao `vault_root`; título = frontmatter `title` ou nome do arquivo; links = `[[wikilink]]` resolvidos **pelo nome** (basename minúsculo, sem extensão).
- Campos usados pelas políticas e fatos derivados nos clientes de saúde: `tipo` (`prontuario`, `resumo`, `registro-documental`, `aula`, `faq`, `modelo`, `aluno`), `paciente_id`, `psicologo_responsavel` (e-mail), `unidade`, `faixa_etaria`, `idade`, `matricula`, `frequencia_percentual`, `mensalidade_status`, `tags`.
- Cópias de conflito/irrecuperáveis: `<nome>.md-brm-conflict-<timestamp>` e `<nome>.md-brm-unparseable-<timestamp>` (sem extensão `.md` final → fora do índice).
- Índice (`vault_index.sqlite3`): `notes_meta(id, note_id UNIQUE, link_key, title, content_hash, updated_at, tags)`, `note_links(source, target_key)`, `notes_fts` (FTS5: `note_id UNINDEXED, title, content, tags`), `notes_vec` (`vec0`, `FLOAT[384]`, `rowid == notes_meta.id`). Reindex incremental por `content_hash`.
