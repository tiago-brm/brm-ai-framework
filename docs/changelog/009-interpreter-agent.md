# 009 — Agente Interpretador (Obsidian → HardRule/Skill draft)

**O quê:** segundo passo da Fase 2 — o agente que lê uma nota do vault escrita
especificamente para isso e propõe uma HardRule ou Skill (nunca as duas), como
draft revisável. Nada é promovido para `rules.yaml`/`skills.yaml` sem uma chamada
explícita e separada de promoção. Junto veio o fechamento do buraco de DLP que a
spec 008 (linha 170) já tinha registrado: nota do vault indo para uma chamada de
LLM externa (Claude, via Agno) é uma saída de dado do cliente que o `intercept()`
original não cobria.

- `mcp_engine/core/interceptor.py` — `_scan_dlp_matches` extraído do corpo de
  `intercept()` (refactor comportamento-preservado) + `check_vault_egress(text,
  client_config)`, novo, usado só no caminho vault→LLM.
- `mcp_engine/core/config_loader.py` — `save_rules`/`save_skills` nos providers
  existentes (`FileSystemRuleProvider`/`FileSystemSkillProvider`). Os Protocols
  `RuleProvider`/`SkillProvider` continuam só-leitura; os métodos de escrita são
  aditivos nas classes concretas, usados só pelo fluxo de promoção.
- `mcp_engine/vault/draft_store.py` — `DraftStore`: ledger append-only por
  cliente/tipo (`rules.draft.jsonl`, `skills.draft.jsonl`), mesma lógica de
  "última linha por `draft_id` vence" que o resto do projeto já usa para
  resolução de conflito.
- `mcp_engine/vault/interpreter.py` — `InterpreterResult` (reaproveita `Rule` e
  `SkillDefinition` de `models.py` em vez de duplicar schema), o `Agent` do Agno,
  e a orquestração `propose_draft()` / `promote_draft()`.
- `mcp_engine/core/mcp_server.py` — 3 métodos novos no `BRMEngine` + 3 tools MCP
  (`vault_interpret_and_propose`, `vault_list_drafts`, `vault_promote_draft`).
  `interpreter_agent: Any | None = None` no construtor, mesmo padrão de injeção
  que `vault_provider` já usava — testes não precisam de `ANTHROPIC_API_KEY`.

## Divergência do spec: `@tool(requires_confirmation=True)` não se aplica

A spec 009 assumia que o decorator de confirmação do Agno seria o mecanismo que
força aprovação humana antes de `promote_draft`. Não é. Instalado `agno==3.0.1`
para inspecionar a API real (a skill `agno-framework` documentava só exemplos
v2, e suas próprias referências `references/core-api.md`/`references/patterns.md`
não existem — pré-requisito #2 da spec ficou pendente): `requires_confirmation`
pausa uma chamada de tool **dentro do loop de execução do próprio agente Agno**.
`promote_draft` não é uma tool que o agente interpretador chama de si mesmo — é
uma tool MCP de nível superior, exposta pelo `FastMCP` do engine, chamada
separadamente por quem está do outro lado da conversa (Claude Desktop, ou o
Control Plane na Fase 3). `@server.tool()` do FastMCP não tem mecanismo de
confirmação (confirmado por exploração direta do `mcp_server.py`) — os dois
decorators são sistemas não relacionados.

O gate humano continua real, só que procedural em vez de criptográfico: promoção
exige uma chamada nomeada e separada, com um `draft_id` específico que só existe
depois de uma geração prévia, nunca automática. É exatamente o que a própria
tabela de guardrails da spec 009 já descrevia ("Tool MCP sempre requer input
humano... nunca proativo") — nada foi perdido, só a mecânica mudou.

Outras correções de API do Agno v3.0.1 vs. o pseudocódigo da spec 009:
`response_model=` → `output_schema=`; `Agent.run()` retorna `RunOutput` (não
`RunResponse`), resultado parseado em `.content`.

## Decisão desta sessão: DLP no caminho vault→LLM é mais estrito que no `intercept()` de skill

`intercept()` deixa uma resposta de skill passar quando só padrões `mask`
casam — reporta no audit, não bloqueia. Para `check_vault_egress()`,
**qualquer padrão DLP configurado bloqueia, `mask` incluso**. O motivo: não há
"redige e continua" neste caminho — o texto vai para uma chamada de LLM externa
cujo resultado vira uma regra de negócio proposta. O custo de um bloqueio falso
é baixo (cliente edita a nota, tenta de novo); o custo de um padrão sensível
chegando a uma chamada de LLM não é recuperável do mesmo jeito que uma resposta
de tool dentro da própria sessão é. `intercept()` para chamadas de skill não foi
alterado.

## Decisão desta sessão: convenção de pasta é o único mecanismo anti-promoção-acidental

A pergunta original do usuário incluía: o escritório vai usar o vault pra FAQ,
passo a passo, documentação — coisas que não devem virar regra/skill. Isso não
pediu nenhum código novo: `vault_search`/`vault_get_related` (spec 008) já
servem qualquer nota do vault como base de conhecimento. O único gate novo é
`propose_draft()` rejeitando de cara qualquer `note_id` que não comece com
`config_drafts/`. Fora dessa pasta, uma nota nunca chega perto do LLM
interpretador — ela só existe para busca e backlink, exatamente como qualquer
outra nota do "segundo cérebro".

## Auto-check de JSONLogic antes do draft ser salvo

`rule_evaluator.py` não tem validação estrutural de `Rule.condition` — é
`dict[str, Any]` puro, qualquer shape passa no Pydantic. Isso significa que uma
regra gerada com JSONLogic inválido (ex.: operador inexistente) só falharia em
runtime, contra fatos reais de produção, dentro de `RuleEvaluationError`. Antes
de `propose_draft()` persistir um draft de regra, ele chama `jsonLogic(rule.
condition, {})` num fato sintético vazio e descarta o draft se levantar exceção.
Não valida a *semântica* de negócio (isso é humano, na revisão), só que a
condição é JSONLogic estruturalmente válido — mesma filosofia de "validar na
borda" que o resto do projeto já segue.

## O que ainda não existe

- **Endpoint de Control Plane.** O usuário confirmou que tanto a tool MCP quanto
  um futuro endpoint de Control Plane devem poder disparar `propose_draft`/
  `promote_draft`. Só a tool MCP está implementada — o Control Plane é Fase 3.
- **Ranking de notas relacionadas.** `get_related()` retorna backlinks sem
  ordenação por relevância; `propose_draft` corta em `MAX_RELATED_NOTES = 5`
  pegando os primeiros retornados, não os "mais relevantes". Fica honesto até
  existir uma métrica de relevância pra backlink.
- **Nota → múltiplos drafts.** Decisão do usuário: 1 nota → 1 draft. Uma nota
  descrevendo 3 regras diferentes gera uma interpretação (o agente decide como
  tratar isso nas instruções, mas o sistema nunca persiste mais de um draft por
  chamada).
- **UI de staging.** Revisão de draft pendente hoje é `vault_list_drafts` (lista
  crua) + leitura do `.draft.jsonl` — não há renderização amigável no Obsidian
  nem no Control Plane ainda.

**Arquivos:**
- `mcp_engine/core/interceptor.py`, `mcp_engine/core/config_loader.py`
- `mcp_engine/vault/{draft_store,interpreter}.py`
- `mcp_engine/core/mcp_server.py` (3 métodos + 3 tools + `interpreter_agent` no
  construtor)
- `tests/test_draft_store.py` (5 casos), `tests/test_vault_interpreter.py` (15
  casos), `tests/TestCheckVaultEgress` em `test_interceptor.py` (5 casos),
  `TestVaultInterpreterWiring` em `test_mcp_server.py` (3 casos)
- `pyproject.toml` / `uv.lock` (`agno>=3.0.1`, `anthropic>=1.2.0`)

**Validação:** `uv run ruff check .` limpo, `uv run pytest -q` — 144 passed (117
da Fase 2 passo 1 + 27 novos). Sem `ANTHROPIC_API_KEY` configurada — todos os
testes injetam um agente falso via `interpreter_agent=`, mesmo padrão de
`FakeEmbeddingProvider` da spec 008. Uso real em produção exige a variável de
ambiente configurada; não validado contra a API da Anthropic nesta sessão.
