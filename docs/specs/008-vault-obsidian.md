# 008 — VaultProvider + ObsidianVaultProvider (Opção B, bidirecional) (spec)

**Ordem da spec (docs/spec.md §1.1, "Camada 2 (Vault) plugável"):** Primeiro passo da Fase 2. A `docs/spec.md` recomendava Opção A (pgvector como fonte de verdade, Obsidian como espelho read-only) e classificava a Opção B como "Fase 2/3, só com demanda explícita". **Essa demanda foi declarada:** o vault bidirecional é o diferencial comercial ("segundo cérebro do cliente"), não um detalhe de implementação. Esta spec assume Opção B direto e **pula a Opção A** — ver §Decisões para o custo dessa escolha.

**Dependência de fase:** Fase 1 concluída (commit `946513a`). O `mcp_server.py` já aceita `vault_provider` no construtor (hoje `Any | None`, marcado `# unused until Fase 2`) — esta spec preenche esse buraco.

## Escopo deste passo

1. **Contrato `VaultProvider`** (`mcp_engine/vault/provider.py`) — Protocol no mesmo padrão de `RuleProvider`/`SkillProvider`, para que trocar Obsidian por pgvector depois seja troca de adapter.
2. **`ObsidianVaultProvider`** (`mcp_engine/vault/obsidian_adapter.py`) — lê **e escreve** `.md` direto no vault do cliente, com frontmatter YAML e wikilinks.
3. **Índice local** (`mcp_engine/vault/index.py`) — SQLite, para `search()` não ter que varrer o disco a cada query.
4. **File-watching** (`mcp_engine/vault/watcher.py`) — detecta edição manual do cliente no Obsidian e reindexa.
5. **Integração no `BRMEngine`** — novos tools MCP: `vault_search`, `vault_add_note`, `vault_get_related`.

**Não** inclui o agente interpretador (md → HardRule/Skill). Isso é a spec 009, e depende desta.

## Detalhes de implementação

### Modelo `VaultNote`

Vai em `mcp_engine/core/models.py`, seguindo `_StrictModel` (`extra="forbid"`, `frozen=True`) como o resto:

```python
class VaultNote(_StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    note_id: str                                    # caminho relativo ao vault_root, ex.: "teses/prescricao.md"
    client_id: str
    title: str                                      # frontmatter `title`, senão nome do arquivo
    content: str                                    # corpo markdown, sem frontmatter
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    links: tuple[str, ...] = ()                     # wikilinks [[nota]] extraídos do corpo
    tags: tuple[str, ...] = ()                      # frontmatter `tags` + #inline
    updated_at: datetime                            # mtime do arquivo
    content_hash: str                               # sha256(content) — detecta edição externa
```

### Contrato `VaultProvider`

```python
@runtime_checkable
class VaultProvider(Protocol):
    def search(self, client_id: str, query: str, limit: int = 10) -> tuple[VaultNote, ...]: ...
    def get_note(self, client_id: str, note_id: str) -> VaultNote | None: ...
    def add_note(self, client_id: str, title: str, content: str,
                 frontmatter: dict[str, Any] | None = None) -> VaultNote: ...
    def get_related(self, client_id: str, note_id: str) -> tuple[VaultNote, ...]: ...
    def list_notes(self, client_id: str) -> tuple[VaultNote, ...]: ...
    def reindex(self, client_id: str) -> int: ...   # retorna nº de notas indexadas
```

`get_related` usa o **grafo de wikilinks** (backlinks + forward links), não similaridade vetorial — é determinístico, barato, e é o que o cliente já enxerga no Obsidian. Similaridade semântica fica em `search()`.

### Layout em disco

```text
clients/<cliente>/
├── config/                          # já existe (rules, skills, client_config)
└── vault/                           # NOVO — o vault Obsidian real, editável pelo cliente
    ├── teses/prescricao.md
    └── modelos/peticao-inicial.md

data/<cliente>/                       # DATA_ROOT, fora do git (já usado pelo audit)
├── audit.jsonl                       # já existe
└── vault_index.sqlite3               # NOVO — índice derivado, descartável/reconstruível
```

Separação deliberada: **os `.md` são a fonte de verdade** (Opção B), o índice é cache reconstruível via `reindex()`. Se o índice corromper, apaga e reconstrói — nenhum dado do cliente se perde.

### Parsing de markdown (`markdown.py`)

```python
def parse_note(path: Path, vault_root: Path, client_id: str) -> VaultNote: ...
def serialize_note(title: str, content: str, frontmatter: dict) -> str: ...

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")   # [[nota]], [[nota|alias]], [[nota#secao]]
def link_key(name: str) -> str:
    # Obsidian semantics: [[peticao-inicial]] resolves by basename,
    # not full path. modelos/peticao-inicial.md and teses/peticao-inicial.md
    # both match [[peticao-inicial]]. key = stem lowercase, no extension.
    return PurePosixPath(name.strip()).stem.lower()
```

Frontmatter via `python-frontmatter` (dep nova) — não reinventar parser de `---` delimitado.

**Wikilink resolution (semantics da indexação):** Links são extraídos como nomes crus (`[[peticao-inicial]]` → `"peticao-inicial"`, sem path, sem `.md`). O índice armazena dois lados de cada edge de backlink de forma assimétrica:

- **Lado source:** `note_id` — path completo (`"modelos/peticao-inicial.md"`)
- **Lado target:** `link_key` — nome normalizado (`"peticao-inicial"`) derivado de `link_key()`

Na reindexação, se uma nota contém `[[peticao-inicial]]`, insere uma edge `(source="teses/prescricao.md", target_key="peticao-inicial")`. Ao consultar backlinks de `"teses/prescricao.md"`, o índice faz:

```sql
SELECT m.note_id FROM note_links l
JOIN notes_meta m ON m.link_key = l.target_key
WHERE l.source = "teses/prescricao.md"
```

E para quem aponta PARA essa nota:

```sql
SELECT source FROM note_links
WHERE target_key = link_key("teses/prescricao.md")
```

Isso encapsula a semântica do Obsidian: um wikilink é um contrato de "nome" entre notas, independente da pasta. Renomear nota ou mover de pasta muda `note_id` (o índice o detecta e reconstrói as edges), mas não quebra backlinks dirigidos aos nomes (mesmo que as notas tenham sumido; edges órfãs persistem até a próxima reindex).

### Índice + busca (`index.py`)

SQLite com **FTS5 + sqlite-vec** (busca híbrida — ver §Decisões):

```sql
CREATE TABLE notes_meta (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  note_id TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
  content_hash TEXT NOT NULL, updated_at TEXT NOT NULL, tags TEXT NOT NULL
);
CREATE TABLE note_links (source TEXT NOT NULL, target TEXT NOT NULL);
CREATE INDEX idx_links_target ON note_links(target);   -- backlink é query indexada, não varredura
CREATE VIRTUAL TABLE notes_fts USING fts5(note_id UNINDEXED, title, content, tags);
CREATE VIRTUAL TABLE notes_vec USING vec0(embedding FLOAT[384]);  -- rowid == notes_meta.id
```

`notes_vec` casa por `rowid` com `notes_meta.id` em vez de usar `note_id` TEXT como chave primária — versões de `sqlite-vec` divergem no suporte a PK textual, e o join por inteiro funciona em todas.

Reindexação é incremental: compara `content_hash` do arquivo com o da tabela; só reparseia/reembeda o que mudou; remove do índice o que sumiu do disco.

### File-watching (`watcher.py`)

`watchdog` (dep nova) observando `clients/<cliente>/vault/`:

- `on_modified` / `on_created` → reparse + reindex daquela nota
- `on_deleted` → remove do índice
- Debounce de ~500ms (Obsidian escreve várias vezes ao salvar)
- Cada evento reindexado emite `AuditEvent` com `action="vault_external_edit"`, `decision=ALLOWED`

Roda como **thread daemon** iniciada pelo `BRMEngine` quando `vault_watch=True`. Fallback documentado: se `watchdog` falhar (filesystem de rede, container sem inotify), cai para `reindex()` sob demanda a cada `search()` — mais lento, mas correto.

### Integração no `BRMEngine` + tools MCP

```python
@server.tool()
def vault_search(query: str, limit: int = 10) -> dict[str, Any]: ...

@server.tool()
def vault_add_note(title: str, content: str, tags: list[str] | None = None) -> dict[str, Any]: ...

@server.tool()
def vault_get_related(note_id: str) -> dict[str, Any]: ...
```

`vault_root` entra em `ClientConfig` como campo opcional (`vault_root: str | None = None`) — default `clients/<cliente>/vault`. Campo opcional com default não quebra os `client_config.yaml` existentes mesmo com `extra="forbid"`.

**Resolução do `vault_root` (decidido nesta sessão).** Dois modos, com tratamento deliberadamente assimétrico:

| Modo | Quando | Diretório ausente |
|---|---|---|
| **Convenção** — `clients/<cliente>/vault/` | `vault_root` não declarado | **Cria** (`mkdir -p`). Cliente novo funciona sem setup manual. |
| **Explícito** — caminho no `client_config.yaml` | `vault_root` declarado | **Falha** com `VaultRootNotFoundError`. |

A assimetria é o ponto: um `vault_root` explícito aponta para um vault que o cliente **já tem** (aberto no Obsidian dele, possivelmente sincronizado). Criar silenciosamente nesse caso transformaria um erro de digitação no caminho em um vault vazio que "funciona" — o engine indexaria zero notas e responderia buscas vazias sem nunca dizer que está olhando para o lugar errado. Já o caminho por convenção é do próprio engine; criá-lo é a única coisa razoável a fazer.

`~` é expandido (`Path.expanduser()`). Caminho absoluto para fora do repositório é o caso normal, não a exceção — é onde vaults reais do Obsidian moram.

**`.env` foi considerado e recusado:** vault é config *por cliente*, e `.env` é global do processo. Cairia em `VAULT_ROOT_<CLIENTE>`, que é reinventar o `client_config.yaml` — onde `domain_allowlist` e `dlp_patterns` já moram.

### Auditoria de escrita

`add_note()` e cada reindex disparado por edição externa emitem `AuditEvent`. Motivo: a tabela de hardening da `docs/spec.md` trata "trilha auditável imutável" como controle de **Fase 1** — uma camada nova que escreve no disco do cliente sem deixar rastro seria regressão de postura, não neutralidade.

**Nota sobre DLP:** o `intercept()` de hoje roda sobre resposta HTTP de skill (`SkillCall.response_text`). Escrita no vault local não passa por ele nesta spec — o dado não sai da máquina do cliente. **Quando o vault ganhar sync para nuvem (ou o interpretador da spec 009 mandar conteúdo de nota para um LLM), isso muda** e o DLP passa a ser obrigatório nesse caminho. Registrado aqui para não virar buraco esquecido.

## Decisões (session-updated)

| Pergunta | Resposta | Consequência |
|---|---|---|
| Onde mora o vault | **Local no servidor** — `clients/<cliente>/vault/` | Desenho de deploy da spec vale como escrito. Sem agente local, sem pasta de rede. |
| Tipo de busca | **Híbrida: vetorial + FTS5** | **Diverge do spec aprovado**, que decidiu FTS5-só. Entra `sqlite-vec` + fusão de rankings. |
| Multi-vault | **Um vault por cliente** | `VaultProvider` fica só com `client_id`, sem `vault_id`. Contrato como na spec. |
| Provider de embedding | **Local (`sentence-transformers`)** | Nada sai da máquina do cliente — a postura on-prem e a nota de DLP da spec §Auditoria continuam válidas. Custo: dependência pesada (torch). |

**Busca híbrida detalhes:** FTS5 (BM25) e `vec0` (distância) produzem scores em escalas incomparáveis. Em vez de normalizar, funde por **Reciprocal Rank Fusion** — `score = Σ 1/(60 + rank)` sobre as duas listas top-N. Sem calibração, sem constante mágica por corpus. É o que faz a busca achar tanto "art. 206 §3º" (termo exato, FTS5 acerta, vetorial erra) quanto "casos parecidos com este" (o inverso).

**Reindex antes de search():** A spec previa reindex como *fallback* para quando o watchdog falhar. Adotar sempre custa um `stat` por arquivo (só o que mudou é lido e reembedado) e elimina a classe inteira de bugs "índice frio devolve resposta velha". O watcher deixa de ser dependência de correção e vira o que ele é bom: frescor para `get_note`/`get_related` entre buscas, e a trilha de auditoria de edição externa.

- **Pular a Opção A tem custo real.** A Opção A (pgvector como fonte de verdade) não tem conflito de concorrência: só o engine escreve. Na Opção B, cliente e engine escrevem no mesmo arquivo. Estamos aceitando esse problema porque o valor comercial está justamente em o cliente editar. Mitigação abaixo — não é solução completa, é contenção.

- **Conflito: last-write-wins com backup do perdedor.** Se `add_note()` for escrever numa nota cujo `content_hash` no índice diverge do arquivo em disco (= cliente editou no Obsidian desde a última indexação), o engine **não sobrescreve calado**: salva a versão do engine como `<nota>.brm-conflict-<timestamp>.md` ao lado e mantém a do cliente. Merge automático de markdown é armadilha; deixar o humano resolver é honesto. Registrado em audit.

- **`note_id` é o caminho relativo, não UUID em frontmatter.** UUID seria mais estável a renomeações, mas injeta metadado do engine em todo arquivo do cliente — hostil a um vault que o cliente considera dele. Caminho relativo é o que o Obsidian já usa como identidade. Custo aceito: renomear nota no Obsidian = nova identidade no índice (backlinks quebram do mesmo jeito que quebram no Obsidian sem "update links").

- **Watcher em thread, não processo separado.** Processo separado exigiria IPC ou lock no SQLite entre processos. Thread daemon dentro do engine é suficiente para um vault de um cliente. Se virar gargalo com vault gigante, reavaliar.

- **Índice fica em `DATA_ROOT`, não dentro do vault.** Enfiar `.sqlite3` dentro de `clients/<cliente>/vault/` sujaria o vault do cliente (aparece no Obsidian, vai pro sync dele). Fora do vault, e reconstruível.

## Provider de embedding (`embeddings.py`)

`EmbeddingProvider` Protocol (`dim: int`, `encode(texts) -> list[list[float]]`) para os testes
injetarem um fake determinístico e offline. `LocalEmbeddingProvider` usa
`sentence-transformers`, modelo carregado **lazy** no primeiro `encode` (não no `__init__` —
senão todo teste e todo boot do engine paga o carregamento). Modelo default:
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384 dims, ~470MB, multilíngue
com PT-BR), numa constante de módulo para troca fácil.

## Fora de escopo (fica para depois)

- Agente interpretador md → HardRule/Skill — **spec 009**
- `PgVectorVaultProvider` — só quando existir cliente que queira vault gerenciado pela BRM em vez de arquivo próprio
- Embedding em nuvem — a escolha é provider local; nuvem exigiria DLP nesse caminho (ver §Auditoria)
- Sync do vault para nuvem da BRM — muda o modelo de ameaça (DLP passa a ser obrigatório), spec própria
- Renderização de Obsidian Canvas / Bases / Dataview — o engine lê markdown + frontmatter, não interpreta plugins

## Critério de pronto

- `uv add python-frontmatter watchdog sqlite-vec sentence-transformers` limpo
- `VaultProvider` Protocol em `mcp_engine/vault/provider.py`, `VaultNote` em `models.py`
- `ObsidianVaultProvider` implementando o contrato inteiro
- `markdown.py` com parse/serialize de frontmatter + extração de wikilinks e tags
- `embeddings.py` com `EmbeddingProvider` Protocol + `LocalEmbeddingProvider` (lazy load)
- `index.py` com FTS5 + sqlite-vec, **busca híbrida com RRF**, reindex incremental por `content_hash`
- `watcher.py` com debounce e fallback documentado
- `ClientConfig.vault_root` opcional + carregamento no `config_loader`
- 3 tools MCP registrados (`vault_search`, `vault_add_note`, `vault_get_related`)
- `tests/test_vault_obsidian.py` cobrindo: parse de frontmatter/wikilink, busca FTS, busca por paráfrase (vetorial), `get_related` por backlink, `add_note` criando arquivo válido, **detecção de conflito gerando `.brm-conflict-*.md`**, reindex incremental pulando nota inalterada, evento de audit em escrita
- `tests/test_embeddings.py` smoke test do `LocalEmbeddingProvider` real (dimensão correta, vetores estáveis), skipif sem sentence-transformers
- `tests/test_example_vault_fixture_is_valid()` golden fixture sobre `clients/example/vault/`
- `uv run ruff check .` limpo
- `uv run pytest -q` verde (44 existentes + ~10 novos)
- `docs/changelog/008-vault-obsidian.md` com seção **"Divergência do spec"**
