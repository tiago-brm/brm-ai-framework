# 015 — `NotionVaultProvider` (implementação)

**Ordem da spec:** implementa a Opção A recomendada em
`docs/specs/014-notion-vault-provider-evaluation.md` ("Notion como
mirror/vitrine sobre o índice local existente"). A 014 era avaliação, não
spec pronta pra codar — esta é a spec do que foi efetivamente construído.

## Resumo

`NotionVaultProvider` (`mcp_engine/vault/notion_adapter.py`) é o segundo
`VaultProvider` do framework, ao lado de `ObsidianVaultProvider`. Ele não
reimplementa busca: alimenta o mesmo `VaultIndex` (FTS5 + `sqlite-vec`,
RRF) que já serve o Obsidian, só que a partir de páginas Notion em vez de
arquivos `.md`. `search`, `get_related`, `list_notes`, `reindex` são
delegação pura pro índice — zero lógica nova ali, exatamente a vantagem que
a 014 apontava.

## `NoteSource`: desacoplando `VaultIndex` do filesystem

Pré-requisito para o adapter existir. `VaultIndex` fazia
`vault_root.rglob("*.md")` + `parse_note()` dentro de `reindex()`, e um
`Path` read direto dentro de `get_note()` — acoplamento que a 014 já tinha
identificado como o único obstáculo real.

Novo protocolo em `mcp_engine/vault/note_source.py`:

```python
class NoteSource(Protocol):
    def list_notes(self) -> Iterable[VaultNote]: ...
    def get_note(self, note_id: str) -> VaultNote | None: ...
```

`VaultIndex.__init__` passou a receber `note_source: NoteSource` no lugar
de `vault_root: Path` (mesma posição de argumento, `vault_db_path`/
`embedding_provider`/`client_id` inalterados). `reindex()` itera
`self._note_source.list_notes()`; `get_note()` delega direto pra
`self._note_source.get_note(note_id)`.

`FilesystemNoteSource(vault_root, client_id)`, no mesmo módulo, reproduz
byte a byte o comportamento antigo: `rglob("*.md")` + `parse_note()`,
guarda de `vault_root.exists()`, `NoteParseError` ignorado por nota (não
derruba o reindex inteiro), leitura por `note_id` relativo ao vault_root.
`ObsidianVaultProvider._index()` hoje constrói
`VaultIndex(FilesystemNoteSource(vault_root, client_id), db_path, ...)`.
Comportamento idêntico ao pré-refactor — `tests/test_vault_obsidian.py`
passa sem mudança de asserções, só a assinatura do construtor de
`VaultIndex` nos fixtures (mecânico).

## `NotionNoteSource`: Notion REST API via `httpx`

Sem SDK novo — `httpx` já é dependência do projeto. Cliente HTTP contra
`https://api.notion.com/v1`, headers `Authorization: Bearer {token}`,
`Notion-Version: 2022-06-28`.

**Descoberta de páginas:** a partir de uma página raiz configurada por
cliente, `GET /v1/blocks/{id}/children` (paginado via `start_cursor`) é
percorrido recursivamente **apenas nos blocos do tipo `child_page`** — ou
seja, o vault Notion de um cliente é uma árvore de páginas soltas sob uma
página raiz que a integração recebeu acesso explícito, o mesmo modelo
"páginas soltas" que a 014 deixou em aberto (a alternativa seria vault
como Notion database; não implementada — ver "fora de escopo").

**Conversão blocks → texto:** conversor pequeno e explícito
(`_block_to_text`), não uma engine geral de Notion→Markdown. Cobre:
`paragraph`, `heading_1/2/3` (prefixo `#`/`##`/`###`), `bulleted_list_item`
(`- `), `numbered_list_item` (`1. `), `quote` (`> `), `code` (texto puro),
`to_do` (`- [x] `/`- [ ] `). Qualquer outro tipo de bloco (tabela, imagem,
callout, embed, coluna, etc.) é ignorado silenciosamente — fidelidade
"suficiente pra busca", não reprodução fiel do documento.

**`VaultNote` resultante:** `note_id` = id da página Notion, `title` = da
propriedade `title` da página (ou do campo `child_page.title` do próprio
bloco quando descoberto via listagem, que evita uma chamada extra),
`content` = blocos convertidos e concatenados com `\n\n`,
`frontmatter = {}`, `links = ()`, `tags = ()` (ver "fora de escopo"),
`updated_at` = `last_edited_time` da página, `content_hash` = sha256 do
`content` convertido — mesmo campo e mesmo algoritmo que
`markdown.parse_note()` já usa pro Obsidian, garantindo que a detecção de
"mudou desde o último reindex" em `VaultIndex._index_note()` funciona
idêntica pras duas fontes.

**`get_note(note_id)`:** busca direta (`GET /v1/pages/{id}` pro título +
`last_edited_time`, `GET /v1/blocks/{id}/children` pro conteúdo) — não
depende de ter passado pela árvore de descoberta antes. 404 vira `None`.

## `NotionVaultProvider`

Mesmo shape público de `ObsidianVaultProvider`: `__init__(config_root,
data_root, audit_sink, embedding_provider=None)`, `VaultIndex` cacheado
por cliente em `self._indices`. Sem `start_watching`/`stop_watching` — o
Notion não tem primitiva equivalente a `watchdog` (ponto já levantado na
014); `BRMEngine` já checa `hasattr(vault_provider, "start_watching")`
antes de chamar, então simplesmente não definir o método é suficiente.

`add_note()` cria a página via `POST /v1/pages` (`parent` = página raiz do
cliente, `properties.title` com o título, `children` = conversão mínima do
markdown de entrada — heading por `#`/`##`/`###`, bullet por `- `, resto
vira parágrafo) e chama `reindex(client_id)` em seguida, mesmo padrão que
`ObsidianVaultProvider.add_note()` usa pra garantir que o índice local
reflete a escrita imediatamente.

**Erro claro em vez de fallback silencioso:** se o cliente está
configurado com `vault_provider: notion` mas falta `notion_token` ou
`notion_root_page_id` em `client_config.yaml`, `NotionConfigError` é
levantado na primeira chamada que precisar do índice — mesmo padrão de
`VaultRootNotFoundError` em `obsidian_adapter.py`.

## `ClientConfig`: três campos novos

```python
vault_provider: Literal["obsidian", "notion"] = "obsidian"
notion_token: str | None = None
notion_root_page_id: str | None = None
```

`vault_provider` é o discriminador; default `"obsidian"` preserva o
comportamento de todo cliente já configurado (nenhum `client_config.yaml`
existente precisa mudar).

## Fábrica em `BRMEngine`

`BRMEngine.__init__` só instancia `ObsidianVaultProvider`/
`NotionVaultProvider` quando **nenhum** `vault_provider` é passado
explicitamente ao construtor — testes que injetam um fake continuam
funcionando sem mudança (checado contra `tests/test_mcp_server.py`
existente). Quando precisa decidir, olha
`self._client_config.vault_provider`.

Isso exigiu mover o carregamento eager de `client_config` (que já existia,
só rodava depois da construção do vault provider) pra antes — sem isso a
fábrica não teria o dado que precisa pra escolher. `get_client_config()`
(`FileSystemClientConfigProvider`) já levanta `ConfigLoadError` se o
arquivo não existe ou é inválido — comportamento inalterado, só a ordem
relativa a outras eager loads mudou (não observável de fora: todo cliente
testado já tinha `client_config.yaml` válido antes desta mudança também).

## Setup pra um operador (o que fica no `client_config.yaml` do cliente)

1. Criar uma integração interna em https://www.notion.so/my-integrations,
   copiar o token (`secret_...`).
2. Compartilhar explicitamente a página raiz do vault desse cliente com a
   integração (Notion não dá acesso a todo o workspace por padrão — "..."
   no canto da página → "Add connections").
3. Copiar o id da página raiz (últimos 32 caracteres da URL, sem hífens ou
   com hífens — a API aceita ambos os formatos) para
   `notion_root_page_id`.
4. No `client_config.yaml` do cliente:
   ```yaml
   vault_provider: notion
   notion_token: secret_...
   notion_root_page_id: <id>
   ```

`clients/example/config/client_config.yaml` **não foi migrado** — não há
token real para esse cliente de exemplo, e ele precisa continuar
funcionando contra o Obsidian como hoje. Um exemplo comentado dos três
campos foi deixado no arquivo, inerte (YAML ignora comentário).

## Fora de escopo (mesma lista da 014, reafirmada aqui)

- **Rate limiting real (~3 req/s do Notion):** não implementado. Nenhuma
  chamada em loop apertado sem pausa foi escrita, mas não há backoff ou
  fila de requisições. Necessário antes de um vault Notion com centenas de
  páginas ser reindexado com frequência.
- **Grafo de link nativo:** `links`/`tags` de `VaultNote` são sempre
  vazios pra notas Notion. *Page mentions* e *relation properties* do
  Notion não são mapeados pra wikilink — `get_related()` funciona (é
  delegação genérica pro índice), mas nunca vai encontrar nada pra uma
  nota Notion enquanto nada popular `note_links` pra ela.
- **Webhooks/polling automático:** sem equivalente ao `VaultWatcher` do
  Obsidian. Mudanças feitas direto no Notion só aparecem no índice local
  depois de um `reindex()` explícito (chamado a cada `search()`, como já
  era pro Obsidian, ou manualmente via tool MCP).
- **Notion database como vault** (alternativa a "páginas soltas") — não
  implementado; exigiria um `NoteSource` diferente mapeando propriedades
  de database em vez de percorrer `child_page` blocks.

## Testes

`tests/test_notion_adapter.py` — mocka a API Notion com
`httpx.MockTransport` (sem dependência nova): descoberta paginada +
recursiva de páginas, conversão de cada tipo de bloco suportado,
`get_note` com 404→`None`, `add_note` validando o payload POST e o
reindex subsequente, e `search`/`get_related` rodando de ponta a ponta
contra o `VaultIndex` alimentado por `NotionNoteSource` — prova de que a
Opção A realmente reaproveita a busca híbrida.

`tests/test_vault_obsidian.py` — inalterado em asserções; só o fixture de
`VaultIndex` ganhou o `FilesystemNoteSource` explícito.

`tests/test_mcp_server.py` — `TestVaultProviderFactory` cobre os três
casos: default Obsidian, seleção Notion via `client_config.yaml`, e
`vault_provider` explícito no construtor vencendo a config.

**Validação:** `uv run ruff check .` limpo, `uv run pytest -q` — 155
passed (144 anteriores + 11 novos).
