# 010 — Gerador de documentos, agnosticismo de modelo, e três integrações reais

**O quê:** sessão de preparação para uma demo ao vivo com o Escritório Assis
Castro Vigo, que acabou virando um passo real de produto. Sem spec prévio —
registrado aqui, depois do fato, como o `docs/README.md` já prevê para
divergência do fluxo normal. Quatro entregas:

1. **`mcp_engine/vault/document_generator.py`** — terceiro tipo de draft
   (`DraftKind` ganhou `"document"` em `draft_store.py`), paralelo ao de
   regra/skill da spec 009, mas para documentos padronizados do vault
   (modelos de petição, contestação, notificação, contrato). Mesmo padrão
   draft → revisão humana → `promote_document` — nunca escreve direto no
   vault. Diferença deliberada do fluxo de regra/skill: aceita instrução
   direta em texto (`vault_generate_document(instrucoes)`), não exige nota
   pré-existente em `config_drafts/` — o risco de um documento ruim é baixo
   (não é código executável), então o atrito extra da convenção de pasta não
   se justifica aqui.
2. **`mcp_engine/vault/_agent_common.py`** — extraído de `interpreter.py`:
   `resolve_agent_model()` (escolhe Anthropic direto ou OpenRouter pela
   variável de ambiente presente) e `record_audit()`, agora compartilhados
   entre o agente interpretador e o gerador de documentos. Motivo do
   agnosticismo: `ANTHROPIC_API_KEY` não estava configurada na máquina de
   demo, só `OPENROUTER_API_KEY` — o motor precisava funcionar com qualquer
   uma das duas sem mudança de código.
3. **`mcp_engine/core/email_sender.py`** — `enviar_email` (Fase 1 é
   mocked-by-design, ver spec.md) ganhou execução real: Resend como
   provedor principal (API HTTP, mais robusto para domínio de destino
   arbitrário), fallback para SMTP se `RESEND_BRM_API_KEY` não estiver
   configurada. Chamado só depois do gate de DLP/allowlist em `call_skill` —
   nunca antes.
4. **`mcp_engine/core/datajud.py`** — consulta em tempo real à API pública
   do CNJ (DataJud). Resolve o tribunal automaticamente a partir do número
   único do processo (Resolução CNJ 65/2008: dígitos de segmento + código de
   tribunal/UF). `consultar_processo_datajud` grava o resultado como nota
   estruturada no vault (frontmatter com classe, órgão julgador, grau) —
   sem isso, a consulta funcionava mas o resultado se perdia no fim da
   conversa.

## Divergência: número único do processo é exact-match, então tribunal errado nunca mostra dado errado

`resolve_tribunal_alias` usa uma tabela de código de UF (Resolução 65/2008,
Anexo IX/X) reconstruída de memória, não 100% verificada contra o texto
oficial (o PDF da resolução não carregou). Aceito o risco porque a consulta
ao DataJud filtra por `numeroProcesso` exato — se a tabela apontar pro
tribunal errado, o resultado é "não encontrado" (0 hits), nunca um processo
diferente mostrado como se fosse o certo. O modo de falha é seguro; só não é
completo (não cobre TJ/TRE de todas as 27 UFs testado individualmente).

## Bug real encontrado: max_tokens do agno para OpenRouter

`OpenRouter` do agno usa `max_tokens=1024` por padrão. Um documento jurídico
completo excede isso, o JSON do `output_schema` trunca no meio de uma string
e falha ao parsear (`"generator did not return a structured result"`).
Subiu para `max_tokens=8192` em `resolve_agent_model()` — afeta os dois
agentes (interpretador e gerador de documentos), já que ambos passam pelo
mesmo builder.

## Bug real encontrado: `use_json_mode` em vez de `strict_output=False`

Já registrado no código (spec 009 não previa OpenRouter): o modo estrito de
JSON schema do agno para modelos OpenAI-like colapsa `dict[str, Any]` livre
(a condição JSONLogic de uma regra) para `{}`, mesmo com `strict_output=False`
na classe do modelo — descoberto que é `Agent(use_json_mode=True)`, não uma
flag do `Model`, que força o caminho de "pedir JSON por prompt" em vez de
decodificação restrita por schema.

## O que ainda não existe

- **UI de staging pra documento.** Mesma lacuna do draft de regra/skill —
  revisão hoje é `vault_list_drafts(kind="document")` cru.
- **Cobertura de teste do DataJud/e-mail real.** `datajud.py` e
  `email_sender.py` foram validados manualmente contra as APIs reais
  (documentado nesta sessão), mas não têm suíte automatizada — as chamadas
  de rede tornam isso não-trivial sem um double/mock de `httpx`.
- **Tabela de UF do DataJud não auditada linha a linha** contra o texto
  oficial da Resolução 65/2008.

**Arquivos:**
- `mcp_engine/vault/document_generator.py` (novo)
- `mcp_engine/vault/_agent_common.py` (novo, extraído de `interpreter.py`)
- `mcp_engine/vault/interpreter.py` (refactor: usa `_agent_common`, sem
  mudança de comportamento)
- `mcp_engine/vault/draft_store.py` (`DraftKind` ganha `"document"`)
- `mcp_engine/core/email_sender.py` (novo), `mcp_engine/core/datajud.py` (novo)
- `mcp_engine/core/mcp_server.py` (3 métodos + 3 tools novos: `vault_generate_document`,
  `vault_promote_document`, `consultar_processo_datajud`; docstrings adicionadas
  em todas as tools — sem elas, a busca semântica de ferramentas do Claude
  Desktop não associa `call_skill` a "enviar e-mail")
- `tests/test_document_generator.py` (6 casos)
- `.claude/skills/brm-onboarding/SKILL.md` (novo — empacota o fluxo de
  onboarding de cliente: config → guardrails → vault → regras/skills →
  documentos)
- `pyproject.toml`/`uv.lock` (`openai`, `httpx` como dependências diretas)

**Validação:** `uv run ruff check .` limpo, `uv run pytest -q` — 150 passed
(144 anteriores + 6 novos). Testado manualmente contra APIs reais: OpenRouter
(Claude Sonnet 4.5) gerando regra e documento, Resend enviando e-mail real,
DataJud retornando dados reais de um processo do TRF1.
