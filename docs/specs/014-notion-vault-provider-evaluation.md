# 014 — Notion como segundo `VaultProvider`, ao lado do Obsidian (avaliação)

**Natureza deste documento:** avaliação/recomendação, não spec pronta pra
implementar direto. Pedido do usuário: "avaliar usar o Notion como mais
uma opção de segundo cérebro junto com o Obsidian". Verifiquei se havia um
MCP do Notion conectado nesta sessão pra inspecionar um workspace real —
não havia (`brm-test-acme` é o único MCP com erro de conexão listado;
Notion não aparece configurado). Esta avaliação é feita contra a API
pública documentada do Notion, não contra um workspace real. Recomendo
conectar um MCP do Notion (`/mcp` ou config do Claude Code) antes de
implementar, pra validar as decisões abaixo contra a estrutura real do
workspace do usuário.

## Onde isso entra na arquitetura existente

`docs/spec.md` já antecipa múltiplos adapters de vault atrás de um
`VaultProvider` Protocol (`mcp_engine/vault/provider.py`): "implementar
`VaultProvider` como interface já na Fase 1 [...] `ObsidianVaultProvider`
entra assim que o primeiro cliente pedir". Notion é exatamente esse
próximo adapter — o contrato (`search`, `get_note`, `add_note`,
`get_related`, `list_notes`, `reindex`) foi desenhado pra ser
implementável por mais de uma ferramenta de PKM.

O problema não é o contrato — é que `ObsidianVaultProvider` de hoje
(`mcp_engine/vault/obsidian_adapter.py`) delega toda busca/embedding pro
`VaultIndex` (`mcp_engine/vault/index.py`), e `VaultIndex` está **acoplado
ao filesystem**: `reindex()` faz `vault_root.rglob("*.md")` e
`get_note()` relê o arquivo do disco a cada chamada. Notion não tem
arquivos — tem páginas via API REST. Um `NotionVaultProvider` não pode
simplesmente instanciar um `VaultIndex` como o Obsidian faz; ou reimplementa
busca do zero, ou o `VaultIndex` precisa parar de assumir "fonte = disco".

## Diferenças estruturais Notion × Obsidian que importam pro design

| | Obsidian (hoje) | Notion |
|---|---|---|
| Unidade de conteúdo | arquivo `.md` com frontmatter YAML | página, como árvore de *blocks* (parágrafo, heading, bullet, etc.) — sem markdown bruto |
| Links entre notas | `[[wikilink]]` — regex simples em `markdown.py` | *page mention* (bloco especial) ou relation property de database — modelo bem diferente, não é regex sobre texto |
| Acesso | leitura direta do disco, grátis e ilimitada | API REST, integração interna precisa ser **compartilhada explicitamente** por página/database (não é "acesso ao workspace inteiro" por padrão), rate limit ~3 req/s |
| Escrita | `write_text()` local, conflito resolvido comparando hash (já implementado) | `PATCH`/`POST` de blocks via API — sem "hash de conteúdo" nativo, precisamos calcular o nosso a partir do conteúdo convertido |
| Busca | FTS5 + embeddings locais (`VaultIndex`) | endpoint `search` do Notion existe mas é full-text simples, sem hybrid/vetorial, e só busca o que foi compartilhado com a integração |
| Detecção de edição externa | `watchdog` no filesystem (`VaultWatcher`) | sem *filesystem events* — precisaria polling (`last_edited_time` de cada página) ou um webhook (Notion tem webhooks em beta) |

Conclusão prática: um `NotionVaultProvider` que se comporta exatamente
como o Obsidian (fonte de verdade remota, edição bidirecional, watcher em
tempo real, grafo de link nativo) é o equivalente da **Opção B** que
`docs/spec.md` já descreve pra Obsidian ("motor lê/escreve direto nos
arquivos do cliente, watcher, grafo de wikilinks") — e ali o próprio
`spec.md` recomenda começar pela Opção A (mirror) e só ir pra B com demanda
explícita. O mesmo raciocínio se aplica ainda mais forte ao Notion, porque
o custo de replicar "watcher em tempo real" e "grafo de link nativo" é
maior (não tem primitiva de SO equivalente a `watchdog`; relations do
Notion não mapeiam 1:1 pra wikilink).

## Opções

### Opção A — Notion como mirror/vitrine sobre o índice local existente (recomendada)

`NotionVaultProvider` não reimplementa busca. Ele:
1. Periodicamente (poll, não webhook — mais simples, aceitável pro volume
   de um vault jurídico) busca páginas via API (`search` ou
   `databases.query`, dependendo de o cliente organizar o vault como
   database ou páginas soltas).
2. Converte cada página (blocks → texto) para `VaultNote` — mesma forma
   que `parse_note()` produz pra Obsidian, hash de conteúdo calculado
   sobre o texto convertido (mesmo campo `content_hash` que já existe em
   `VaultNote`).
3. Alimenta o **mesmo** `VaultIndex` que já faz FTS+vetorial — que precisa
   de um refactor pontual (ver abaixo) pra aceitar notas de uma fonte que
   não é `Path.rglob`.
4. `add_note()` cria/atualiza a página via API Notion **e** reindexa local.

Vantagem: zero duplicação de lógica de busca/embedding — o hybrid search
que já existe (RRF entre FTS5 e `sqlite-vec`) passa a valer pro Notion de
graça. Mesmo raciocínio de "Opção A" do `spec.md`: baixo risco, sem
conflito de concorrência complexo (o local é sempre derivado, nunca fonte
de verdade concorrente).

**Refactor necessário em `VaultIndex`:** hoje `reindex()` sabe fazer
`rglob("*.md")` e `parse_note(path, ...)`. Precisa virar algo como
`reindex(notes: Iterable[VaultNote])`, com quem chama (Obsidian ou Notion)
responsável por produzir essa lista — Obsidian continua fazendo
`rglob`+`parse_note` como hoje, só que por fora, no adapter, não dentro do
índice. `get_note()`/`list_notes()` do índice já trabalham com `note_id`
abstrato — o único ponto realmente acoplado ao disco é `reindex()` e o
`_vault_root.exists()` inicial. Escopo pequeno, isolado num arquivo.

### Opção B — NotionVaultProvider "nativo" (fonte de verdade real, sem mirror)

Cada `search()`/`get_related()` chama a API do Notion direto, sem índice
local. Mais simples de descrever, mas: busca pior (sem híbrido
FTS+vetorial — só o que o endpoint `search` do Notion oferece), sem
detecção de link/relação equivalente a backlink, e cada chamada de tool MCP
vira 1+ round-trip de rede sujeito a rate limit (3 req/s) — ruim pra uma
tool que o Claude pode chamar em sequência rápida. **Não recomendada** como
primeira implementação.

## Fora de escopo desta avaliação (não decidido, não implementado)

- Qual conversor blocks↔markdown usar (`notion-py`, biblioteca própria, ou
  chamar a API REST direto com `httpx` já é dependência do projeto —
  provavelmente a via mais simples, sem dependência nova).
- Se o vault de um cliente no Notion é uma database (mais estruturado,
  fácil de mapear frontmatter → properties) ou páginas soltas (mais livre,
  mais parecido com Obsidian) — isso muda a implementação de
  `_list_source_notes()` e só dá pra decidir olhando o workspace real do
  cliente.
- Autenticação: token de integração interna por cliente (mesmo padrão de
  isolamento por cliente que `SQLUserStore`/vault_root já seguem — token
  guardado em `client_config.yaml` ou variável de ambiente por cliente,
  nunca hardcoded).
- Webhooks do Notion (beta) como alternativa a polling — vale revisitar se
  polling se mostrar devagar demais na prática.

## Recomendação

Implementar Opção A, em duas etapas separáveis:
1. Refactor de `VaultIndex.reindex()` pra aceitar uma fonte de notas
   injetável (isolado, sem mudar o comportamento do Obsidian).
2. `NotionVaultProvider` novo, só depois de validar contra um workspace
   real (conectar o MCP do Notion nesta sessão, ou o usuário compartilhar
   a estrutura real do vault de um cliente candidato) — decisões como
   "database ou páginas soltas" não devem ser tomadas às cegas.

Não implementado nesta sessão: sem MCP do Notion conectado para validar as
suposições acima contra um workspace real, e o refactor de `VaultIndex` é
uma mudança em componente já usado em produção pelo Obsidian — melhor
revisada como spec própria antes do código, seguindo o mesmo fluxo das
specs 008/009.
