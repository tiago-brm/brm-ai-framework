# 015 — `NotionVaultProvider`: Notion como segundo `VaultProvider`

**O quê:** segundo adapter de vault, ao lado do `ObsidianVaultProvider`,
implementando a Opção A recomendada em
`docs/specs/014-notion-vault-provider-evaluation.md` (mirror sobre o
`VaultIndex` existente, não busca reimplementada do zero). Spec de
implementação: `docs/specs/015-notion-vault-provider.md`.

- `mcp_engine/vault/note_source.py` (novo) — protocolo `NoteSource`
  (`list_notes`/`get_note`) e `FilesystemNoteSource`, extraído do que
  `VaultIndex` fazia inline contra o filesystem. Comportamento
  byte-a-byte preservado pro Obsidian.
- `mcp_engine/vault/index.py` — `VaultIndex.__init__` recebe
  `note_source: NoteSource` no lugar de `vault_root: Path`. `reindex()`
  e `get_note()` delegam pra fonte em vez de fazer `rglob`/leitura de
  disco diretamente.
- `mcp_engine/vault/obsidian_adapter.py` — `_index()` constrói
  `FilesystemNoteSource` e passa pro `VaultIndex`. Sem mudança de
  comportamento observável.
- `mcp_engine/vault/notion_adapter.py` (novo) — `NotionNoteSource`
  (API REST do Notion via `httpx`, descoberta recursiva de páginas a
  partir de uma raiz configurada, conversor blocks→texto pra
  paragraph/heading/bulleted/numbered/quote/code/to_do) e
  `NotionVaultProvider` (mesmo shape público do adapter Obsidian,
  `search`/`get_related`/`list_notes`/`reindex` delegando pro
  `VaultIndex`, `add_note` criando página via API e reindexando).
- `mcp_engine/core/models.py` — `ClientConfig` ganha `vault_provider`
  (`"obsidian"` default | `"notion"`), `notion_token`,
  `notion_root_page_id`.
- `mcp_engine/core/mcp_server.py` — `BRMEngine._build_vault_provider()`
  escolhe o adapter com base em `client_config.vault_provider` quando
  nenhum `vault_provider` é passado explicitamente ao construtor
  (injeção de fake em teste continua funcionando sem mudança).

## Decisão: mirror sobre o índice local, não fonte de verdade remota

Reafirma a recomendação da 014 (Opção A) em vez da Opção B ("Notion
nativo", cada `search()` chamando a API direto). Resultado: busca híbrida
FTS5+vetorial vale pro Notion de graça, sem reimplementar nada — só
`NotionNoteSource` alimentando o mesmo `VaultIndex`.

## O que ainda não existe

- Rate-limit/backoff real contra o limite de ~3 req/s do Notion.
- Grafo de link nativo (page mentions/relations → wikilink) — `links`/
  `tags` de notas Notion são sempre vazios nesta versão.
- Webhooks ou qualquer polling automático — mudanças no Notion só entram
  no índice local no próximo `reindex()` explícito.
- Vault Notion como database (só "páginas soltas" sob uma raiz é
  suportado).

**Arquivos:**
- `mcp_engine/vault/note_source.py` (novo), `notion_adapter.py` (novo)
- `mcp_engine/vault/index.py`, `obsidian_adapter.py` (modificados)
- `mcp_engine/core/models.py`, `mcp_server.py` (modificados)
- `tests/test_notion_adapter.py` (novo)
- `tests/test_vault_obsidian.py`, `test_mcp_server.py` (casos novos/
  fixture atualizado)
- `clients/example/config/client_config.yaml` (exemplo comentado, cliente
  continua no Obsidian)
- `docs/specs/015-notion-vault-provider.md`

**Validação:** `uv run ruff check .` limpo, `uv run pytest -q` — 155
passed (144 anteriores + 11 novos).
