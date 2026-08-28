# 008 — VaultProvider + ObsidianVaultProvider

**O quê:** primeiro passo da Fase 2 — o vault bidirecional. O engine passa a ler
e escrever notas `.md` no vault Obsidian do cliente, com busca híbrida
(vetorial + lexical), backlinks, e detecção de conflito quando cliente e engine
escrevem na mesma nota.

- `mcp_engine/vault/provider.py` — `VaultProvider` Protocol (`search`,
  `get_note`, `add_note`, `get_related`, `list_notes`, `reindex`), no mesmo
  estilo `@runtime_checkable` dos contratos da Fase 1.
- `mcp_engine/vault/markdown.py` — `parse_note` / `serialize_note` sobre
  `python-frontmatter`; extração de wikilinks e tags inline. `NoteParseError`
  co-locada. `link_key()` implementa a resolução por basename do Obsidian.
- `mcp_engine/vault/embeddings.py` — `EmbeddingProvider` Protocol +
  `LocalEmbeddingProvider` (`sentence-transformers`, modelo carregado lazy no
  primeiro `encode`).
- `mcp_engine/vault/index.py` — `VaultIndex`: SQLite com FTS5 + `sqlite-vec`,
  fusão por Reciprocal Rank Fusion, reindexação incremental por `content_hash`.
- `mcp_engine/vault/obsidian_adapter.py` — `ObsidianVaultProvider`: resolução do
  `vault_root`, conflito com arquivo de sombra, auditoria de escrita e de edição
  externa.
- `mcp_engine/vault/watcher.py` — `VaultWatcher` sobre `watchdog`, com debounce
  de 0.5s (Obsidian escreve o mesmo arquivo várias vezes por save).
- `mcp_engine/core/models.py` — novo `VaultNote`; `ClientConfig.vault_root`
  opcional.
- `mcp_engine/core/mcp_server.py` — 3 métodos no `BRMEngine` + 3 tools MCP
  (`vault_search`, `vault_add_note`, `vault_get_related`). Flag
  `vault_watch: bool = False` para não subir thread de watcher em cada
  construção de engine (os testes da Fase 1 constroem muitos).

## Divergência do spec: FTS5-só → busca híbrida

O spec 008 aprovado decidia **FTS5 apenas**, com vetorial listado em "fora de
escopo". Na sessão de planejamento essa decisão foi revista: busca puramente
lexical não atende "casos parecidos com este", que é metade do valor de um vault
jurídico; e busca puramente vetorial erra "art. 206 §3º V", que é a outra
metade. Entram `sqlite-vec` e `sentence-transformers`.

A fusão é por **Reciprocal Rank Fusion** (`score = Σ 1/(60 + rank)`) em vez de
normalizar scores: BM25 e distância vetorial vivem em escalas incomparáveis, e
qualquer normalização exigiria calibração por corpus. RRF descarta a magnitude e
usa só a posição — sem constante mágica por cliente.

Custo aceito: `torch` entra na árvore de dependências (~2GB instalado), e o
modelo baixa ~470MB no primeiro uso. Em troca, **nada sai da máquina do
cliente** — a postura on-prem e a nota de DLP do spec continuam válidas, o que
não seria verdade com embedding em nuvem.

`docs/specs/008-vault-obsidian.md` foi atualizado com essa decisão e as demais
antes de qualquer código ser escrito.

## Divergência do spec: reindex sempre antes de `search()`

O spec previa reindexação como *fallback* para quando o watchdog falhasse.
Ficou como comportamento padrão de `search()`: custa um `stat` por arquivo (só o
que mudou é relido e reembedado) e elimina a classe inteira de bug "índice frio
devolve resposta velha".

Consequência para quem lê o código: `get_note()` e `get_related()` **não**
reindexam. Eles dependem de um `search()`/`reindex()` anterior ou do watcher.
Isso é deliberado — reindexar em toda leitura de backlink seria caro e
redundante — mas é uma armadilha para quem chama `get_related()` primeiro numa
sessão nova, e foi exatamente o que dois testes pegaram.

## Decisão desta sessão: resolução assimétrica do `vault_root`

Levantado durante a implementação, ao apontar o cliente `example` para um vault
Obsidian real: se `vault_root` está declarado no `client_config.yaml` e o
diretório não existe, o engine **falha** (`VaultRootNotFoundError`) em vez de
criar. Se `vault_root` não está declarado, o caminho por convenção
(`clients/<cliente>/vault/`) é criado normalmente.

O motivo é que um `vault_root` explícito aponta para um vault que o cliente já
tem. Criá-lo silenciosamente transforma um erro de digitação em um vault vazio
que "funciona": zero notas indexadas, buscas vazias, e nenhum sinal de que o
engine está olhando para o lugar errado. O caminho por convenção é do próprio
engine — aí criar é o correto.

`.env` foi considerado para essa configuração e recusado: vault é config por
cliente, `.env` é global do processo. Levaria a `VAULT_ROOT_<CLIENTE>`, que é
reinventar o `client_config.yaml`.

## Bugs encontrados e corrigidos

### Perda de dado em nota com frontmatter quebrado

`add_note()` envolvia toda a detecção de conflito num `try/except Exception: pass`.
Se a nota já existente no disco tivesse YAML inválido — cliente editou no
Obsidian e quebrou o frontmatter — `parse_note()` levantava, a exceção era
engolida, e o fluxo caía direto no `write_text()`. **O conteúdo do cliente era
sobrescrito sem conflito, sem backup e sem evento de auditoria.**

Isso derrotava a mitigação central da Opção B: aceitamos escrita concorrente
justamente porque o perdedor é preservado. Aqui não era.

A correção separa os dois casos. Se a nota existe mas não parseia, os bytes
originais vão para um sidecar de salvamento **antes** de qualquer escrita, com
`vault_write_conflict` (`denied`) e `reason` explicando. A versão do engine
assume o nome — deixar o arquivo ilegível no lugar travaria aquele `note_id`
para sempre.

### Sidecar de conflito voltava como nota do vault

Os arquivos de conflito nasciam como `<nota>.brm-conflict-<ts>.md`. Terminam em
`.md`, então o `rglob("*.md")` do `reindex()` os pegava e indexava como notas
normais — com o **mesmo `title`** da nota que sombreiam. Resultado: toda busca
que achasse a nota real trazia o sidecar junto, indistinguível no título.

Passaram a ser `<nota>.md-brm-conflict-<ts>` (e `.md-brm-unparseable-<ts>` para o
salvamento acima): fora do glob, invisíveis para o Obsidian como nota, e ainda
óbvios para um humano listando a pasta. Os dois sufixos são distintos de
propósito — `conflict` guarda a versão do **engine**, `unparseable` guarda a do
**cliente**. Mesmo nome para os dois seria uma armadilha na hora de resolver.

Ambos passaram despercebidos porque os testes de conflito sempre reindexavam
antes de `add_note()`, o que mascarava os dois caminhos.

### Wikilink gravado em dois vocabulários

A primeira versão do índice gravava os dois lados de um backlink em vocabulários
diferentes: `note_links.source` recebia o `note_id` completo
(`"teses/prescricao.md"`) e `note_links.target` recebia o nome cru do wikilink
(`"peticao-inicial"`, sem pasta e sem extensão). Nenhuma consulta de backlink
casava — nem forward, nem reverso.

A correção não foi normalizar os dois para `note_id`, porque wikilink de Obsidian
**não carrega pasta**: `[[peticao-inicial]]` resolve por basename, de qualquer
pasta do vault. O índice passou a guardar `link_key` (basename, minúsculo, sem
extensão) em `notes_meta` e `note_links.target_key`, e a consulta faz o join por
essa chave. Três testes pegaram isso, incluindo o que atravessa `teses/` →
`modelos/`.

## O que ainda não existe

- **DLP no caminho do vault.** Escrita local não passa por `intercept()`. Quando
  o vault ganhar sync para nuvem — ou quando o interpretador da spec 009 mandar
  conteúdo de nota para um LLM — isso vira obrigatório. Está registrado no spec.
- **Merge de conflito.** O engine desvia sua versão para
  `<nota>.md-brm-conflict-<timestamp>` e preserva a do cliente. Resolver é
  trabalho humano; merge automático de markdown é armadilha.
- **Limpeza de sidecar.** Conflitos e salvamentos se acumulam na pasta até
  alguém resolver. Nada os expira nem os lista via MCP — hoje só aparecem para
  quem olha o diretório.
- **Renomeação de nota.** Muda o `note_id`, logo muda a identidade no índice.
  Backlinks por nome sobrevivem (é o que `link_key` compra), mas o histórico de
  auditoria da nota antiga não segue para a nova.
- **Watcher em produção.** `vault_watch=False` por padrão; ligado, ele foi
  exercitado só manualmente, não em teste automatizado (depende de timing de
  filesystem).

**Arquivos:**
- `mcp_engine/vault/{__init__,provider,markdown,embeddings,index,obsidian_adapter,watcher}.py`
- `mcp_engine/core/models.py` (`VaultNote`, `ClientConfig.vault_root`)
- `mcp_engine/core/mcp_server.py` (3 métodos + 3 tools + `vault_watch`)
- `clients/example/config/client_config.yaml` (`vault_root` apontando para o
  vault real)
- `tests/test_vault_obsidian.py` (19 casos), `tests/test_embeddings.py` (5 casos)
- `pyproject.toml` / `uv.lock` (`python-frontmatter`, `watchdog`, `sqlite-vec`,
  `sentence-transformers`)
- `docs/specs/008-vault-obsidian.md` (decisões da sessão, antes do código)

**Validação:** `uv run ruff check .` limpo, `uv run pytest -q` — 117 passed
(93 dos passos anteriores + 24 novos). E2E manual contra o vault Obsidian real
do cliente `example`: `vault_search` achou as 2 notas, `vault_add_note` gravou
no disco com wikilink extraído, `vault_get_related` atravessou `teses/` →
`modelos/`, e o teste de conflito confirmou que uma edição feita no Obsidian não
é sobrescrita — a versão do engine foi para o arquivo de sombra e o evento
`vault_write_conflict` (`denied`) entrou na trilha de auditoria.

Os dois bugs acima saíram de uma varredura de casos-limite depois disso, não da
suíte: frontmatter quebrado, colisão de slug, query com sintaxe de FTS5, `note_id`
acentuado (NFC/NFD no macOS), e provider de embedding com dimensão diferente de
384. Os três últimos se comportam corretamente hoje — respectivamente: fallback
silencioso para vetorial-só quando o `MATCH` do FTS5 rejeita a query (`"art. 206"`
é erro de sintaxe para o FTS5), `note_id` estável em NFC, e erro alto do
`sqlite-vec` em vez de vetor truncado.
