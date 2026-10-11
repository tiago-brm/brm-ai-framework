# Testing

> O agente não para em "o código parece correto": roda os checks, exercita os fluxos críticos e relata o que **não** rodou.
> Baseline medido em 2026-10-11 (Python 3.11 via `uv`, Node 22): `ruff` limpo; `pytest` → **278 passed, 1 skipped, 2 failed** (os 2 falham só offline: baixam o modelo de embeddings do Hugging Face).

## 1. Testing Goals

Funcionalidade crítica (nesta ordem de risco):

- **Governança de ação:** regras (allow/deny/require_approval), fatos derivados, `actor_role`.
- **Interceptor:** allowlist, IP privado, DLP `mask`/`deny`, redação por perfil, DLP de saída para LLM.
- **Identidade e RBAC:** stdio e HTTP, `tools/list` filtrado, `tools/call` revalidado, `super_admin`, permissões em runtime.
- **Acesso por nota no vault** e escrita restrita (matriz perfil × tipo de nota).
- **Auditoria:** hash-chain íntegra, quebra detectada, nenhum conteúdo/segredo indevido.
- **Rascunhos:** nada vai à produção sem promoção; validação antes de gravar.
- **Vault:** busca híbrida, conflito de escrita, reindex incremental, Notion.
- **API do painel:** só `super_admin`; simulador sem efeito externo.

## 2. Test Types

### Unit
Funções puras e módulos isolados: `rule_evaluator`, `interceptor`, `models`/`contracts`, `markdown`, `draft_store`, `audit/sink`, `auth`, `config_loader`, `embeddings` (com provider falso).

### Integration
Engine + providers reais em disco temporário (`tmp_path`): `BRMEngine`/`build_server` (tools, RBAC), `ObsidianVaultProvider` (índice FTS5 + `sqlite-vec`), `NotionVaultProvider` (API simulada com `httpx.MockTransport`), `SQLUserStore`, rotas `/api/*` (FastAPI `TestClient`), agentes interpretador/gerador com agente falso injetado (sem chave de LLM).

### End-to-End (manual/semiautomatizado)
Fluxos de ponta a ponta com o servidor real e perfis reais:
- `scripts/preflight_demo.py` — roda as demos (`demo_clinica.py`, `demo_casos_de_uso.py`) sem pausa e confere as contagens esperadas (✓ permitidas, ✕ bloqueadas, ⏸ em aprovação). Se uma contagem mudar de propósito, atualize `ESPERADO`.
- `scripts/demo_clinica.py`, `demo_casos_de_uso.py`, `demo_dia_a_dia.py` — roteiros de cenas.
- Claude Desktop/Claude Code conectado ao servidor MCP com `MCP_USER_EMAIL` de um perfil ([UX_FLOWS.md](UX_FLOWS.md) F-01).
- **Não existe** E2E automatizado do painel nem teste de componente React (lacuna).

## 3. Testing Tools

- **Python:** `pytest` (≥ 9), `ruff`. Fakes locais em vez de mocks mágicos (ex.: `RecordingAuditSink`), `tmp_path`, `httpx.MockTransport`.
- **Painel:** `oxlint`, `tsc -b` (via `npm run build`). Sem runner de teste configurado — *meta:* Vitest + Testing Library para `api.ts` e páginas críticas; Playwright para login → simulador → matriz.
- **CI:** não há pipeline versionado (nenhum `.github/workflows`) — *meta:* rodar os comandos da §7 em cada push.

## 4. Test File Structure

```text
tests/
├── test_<módulo>.py     # um arquivo por módulo, funções test_* diretas
└── __init__.py
```
Sem separação `unit/`/`integration/` hoje; **mantenha um arquivo por módulo** e nomeie o teste pelo comportamento (`test_wildcard_does_not_match_bare_domain`). Cenários demo ficam em `scripts/`, não em `tests/`.

## 5. Critical User Flows

**Flow: ação negada por vínculo (sigilo)**
1. `camila` (viewer, recepção) pede o prontuário do PAC-005.
2. Motor calcula `vinculo_com_paciente = nao` a partir do vault.
3. Regra `prontuario-somente-psicologo-vinculado` nega.
Expected: `allowed:false`, `reason` + `proximo_passo`, evento `DENIED` com `rule_id`; nenhum conteúdo do prontuário.

**Flow: modelo tenta informar fato de segurança**
1. Chamada com `vinculo_com_paciente: "sim"` por usuário sem vínculo.
Expected: argumento descartado, regra decide pelo valor derivado, evento `<skill>:fato_divergente`.

**Flow: leitura do vault por perfil restrito**
1. `viewer` busca "paciente com autocrítica".
Expected: registro documental e prontuário **não** aparecem; ficha em `metadata_only`; contagem `ocultas` > 0; auditoria sem conteúdo.

**Flow: rascunho → promoção**
1. Nota em `config_drafts/` descreve uma regra → `vault_interpret_and_propose`.
2. DLP de saída estrito; draft `pending` em `rules.draft.jsonl`.
3. `vault_promote_draft` → regra no fim de `rules.yaml`, draft `approved`, evento `vault_promote_draft`.
Expected: nada vai à produção antes do passo 3; nota com CPF/CID **não** chega ao LLM.

**Flow: RBAC em runtime**
1. `super_admin` desliga a tool para `admin` via `atualizar_permissao`.
Expected: efeito imediato, sem reiniciar; chamada forçada é negada e auditada.

**Flow: simulador**
1. `super_admin` executa o mesmo cenário como 3 perfis.
Expected: decisões diferentes; nenhum efeito externo (e-mail não enviado); eventos `simulacao:*`; matriz não escreve na auditoria; `simulador: false` → 404.

**Flow: integridade da auditoria**
1. Editar uma linha de `audit.jsonl`.
Expected: `iter_events` levanta `AuditChainError` na linha correta.

## 6. Coverage

- **Alvo prático:** sem meta numérica configurada. Toda linha de decisão de segurança (regras, RBAC, interceptor, vault_access, rascunhos) deve ter teste de caminho feliz e de **negação**.
- **Priorizar:** lógica de negócio crítica, autenticação/autorização, mutações de dado (usuários, permissões, vault, config promovida), jornadas principais.
- *Meta:* `pytest --cov` no CI para tornar as lacunas visíveis. Lacunas conhecidas sem teste: L-01…L-05 de [SECURITY.md](SECURITY.md) (cada correção deve entrar com seu teste), painel React, execução real de e-mail (`enviar_email`).

## 7. Commands

```bash
# Setup
uv sync                                   # Python 3.11, dependências travadas em uv.lock

# Backend
uv run ruff check .                       # lint (E, F, I, linha 100)
uv run pytest -q                          # todos os testes
uv run pytest -q --deselect tests/test_embeddings.py   # sem rede (pula os que baixam o modelo)
uv run pytest -q tests/test_interceptor.py -k dlp      # um módulo / filtro

# Pré-voo das demos
uv run python scripts/preflight_demo.py

# Painel
cd dashboard && npm ci && npm run lint && npm run build   # build = tsc -b && vite build
```
Type check do Python: **não configurado** (*meta*, ver [CODE_STYLE.md](CODE_STYLE.md) §10).

## 8. Known issues que afetam a verificação

- **O painel não compila em clone limpo.** `npm run build` falha com `Cannot find module '@/lib/router'`, `@/lib/useLoadable` e (indiretamente) `@/lib/utils`: o `.gitignore` raiz contém `lib/` (padrão Python) e **ignora `dashboard/src/lib/`**, que nunca entrou no git. Enquanto isso não for corrigido (versionar `src/lib/` com `!dashboard/src/lib/` no `.gitignore` e recriar os arquivos), `lint`/`build` do painel só funcionam na máquina que ainda tem esses arquivos.
- **2 testes exigem rede:** `test_encode_returns_vectors_of_declared_dim` e `test_encode_is_deterministic` baixam `paraphrase-multilingual-MiniLM-L12-v2` (~470 MB). Falham com `ProxyError: 403` em ambientes sem acesso ao Hugging Face. Não é regressão; relate-os como "não executáveis aqui".
- 1 teste é *skipped* fora da máquina do autor: `tests/test_vault_obsidian.py:359` valida o vault real apontado por `clients/example/config/client_config.yaml` (`vault_root: /Users/tiagopereiraramos/vault/brm-framework-vault`). Ou seja, o cliente `example` depende de um caminho pessoal — ver Open Questions em [PRD.md](PRD.md).

## 9. Definition of Done

- [ ] `uv run pytest -q` passa (exceto, no máximo, os 2 testes dependentes de rede, declarados)
- [ ] `uv run ruff check .` passa
- [ ] Painel: `npm run lint` e `npm run build` passam quando `dashboard/` mudou (ou a falha de §8 é citada)
- [ ] Teste novo cobre o caminho feliz **e** a negação de qualquer decisão de segurança alterada
- [ ] Fluxo crítico afetado verificado (automatizado ou manual) e registrado
- [ ] Sem erros bloqueantes; `git diff` revisado contra mudanças não intencionais
- [ ] Checks que não puderam rodar estão listados no relato da entrega
