# AI Coding Agent Instructions

Instruções para qualquer agente de código neste repositório (Claude Code, Codex, Cursor…). `CLAUDE.md` importa este arquivo. Mantenha-as curtas: o detalhe vive em `docs/`.

## 1. Project Context

- **Project:** BRM Framework (BRM Engine) — pacote `mcp_engine`.
- **Purpose:** Motor MCP por cliente: o Claude age sobre sistemas e conhecimento do cliente, e o **motor** (não o modelo) aplica regras, RBAC, DLP, controle de acesso ao vault e auditoria.
- **Primary Users:** Operadores da BRM Solutions (`super_admin`), profissionais do cliente (`admin`) e apoio (`viewer`) que falam com o Claude.
- **Stack:** Python ≥ 3.11 (`uv`), MCP/FastMCP, FastAPI, Pydantic v2, SQLite/SQLAlchemy, React 19 + Vite + Tailwind 4 (painel).

## 2. Source of Truth

Antes de uma mudança relevante, leia os documentos pertinentes em `docs/`:

| Preciso saber… | Leia |
|---|---|
| O que construir, escopo, features `F-xx`, fases | `docs/PRD.md` |
| Como o sistema funciona, decisões `D-NNN`, regras de dependência | `docs/ARCHITECTURE.md` |
| Dados, schemas de YAML/SQLite/auditoria | `docs/DATABASE.md` |
| Segurança, lacunas conhecidas `L-NN`, checklist | `docs/SECURITY.md` |
| Estilo e convenções | `docs/CODE_STYLE.md` |
| Como verificar, comandos, DoD | `docs/TESTING.md` |
| Telas e jornadas | `docs/UX_FLOWS.md`, `docs/DESIGN_SYSTEM.md` |
| Contratos de tools MCP e REST | `docs/API.md` |

Não contradiga esses documentos sem antes **apontar o conflito**. Comentários no código que citam "spec NNN" apontam para `D-NNN` em `docs/ARCHITECTURE.md`.

## 3. As lentes BRM (inegociáveis)

1. **Segurança desde o dia 1.** Hardening é Fase 1, não "depois". Autorização no servidor, nunca no prompt.
2. **O modelo interpreta; o motor decide.** Fato de segurança (vínculo, matrícula, idade, frequência) vem de `derived_facts`, nunca de argumento do modelo.
3. **Regra é dado.** JSONLogic sandboxado. Proibido `eval`/`exec`/condição em string livre.
4. **Humano no circuito.** Saída de LLM nunca vai direto para produção: rascunho (`DraftStore`) → promoção nomeada → auditoria.
5. **Dado fica com o cliente.** Nada de nota rumo a LLM externo sem `check_vault_egress`; embeddings locais; sem sync do vault para nuvem sem spec própria.
6. **Isolamento por cliente.** Sem coluna `client_id` compartilhada, sem acesso cross-client. Cliente novo = só arquivos em `clients/<cliente>/`.
7. **Agnóstico de segmento.** Jurídico e clínica são *clientes*; texto de produto e docstrings de tools não devem prender o produto a um deles.
8. **Não antecipe a Fase 2.** Sem Control Plane, Rule Builder visual ou multi-tenant antes do gatilho de saída descrito no PRD.
9. **Falar a verdade sobre o estado.** Relate checks não executados, divergências e limitações. Nunca diga "passou" sem ter rodado.

## 4. General Rules

- Entenda o código existente antes de modificar; reutilize componentes e utilitários.
- Siga a arquitetura (`docs/ARCHITECTURE.md` §8), o estilo (`docs/CODE_STYLE.md`) e as convenções de dados (`docs/DATABASE.md`).
- **Não invente requisitos de negócio.** Dúvida de produto vira pergunta (ou item em *Open Questions* do PRD).
- Nunca exponha, registre ou comite segredos (`.env`, `users.db`, `data/`, tokens, chaves). Nunca coloque conteúdo de nota nem dado pessoal em log/auditoria.
- Evite dependências novas e mudanças não relacionadas à tarefa.
- Toda tool, rota ou evento novo passa por RBAC, regras/interceptor quando aplicável, auditoria e [docs/API.md](docs/API.md).

## 5. Planning

Antes de uma tarefa não trivial:

1. Leia a documentação relevante e o código afetado.
2. Identifique arquivos, dependências, mudanças de schema/API e testes necessários.
3. Verifique o impacto em segurança (`docs/SECURITY.md` §9) e se toca alguma lacuna `L-NN`.
4. Escreva um plano curto. Se o escopo for ambíguo ou conflitar com o PRD, **pergunte antes**.
5. Mudança de comportamento de produto → atualize/crie a feature `F-xx` com critérios de aceite **antes** de implementar.

## 6. Implementation

- Mudanças focadas; preserve comportamento existente; sem reescritas desnecessárias.
- Dependa de `Protocol`s e injete por construtor; teste com fakes.
- Registre tools **só** em `build_server()`; identidade **só** via `get_current_user()`.
- Código novo parece com o vizinho (idioma, comentários que explicam o *porquê*).
- Se uma decisão técnica mudar, registre `D-NNN` novo em `docs/ARCHITECTURE.md` §9 (não crie spec/changelog por passo).

## 7. Validation

Depois de implementar, rode (ver `docs/TESTING.md` §7):

```bash
uv run ruff check .
uv run pytest -q          # 2 testes de embeddings precisam de rede; relate se não puderem rodar
# se dashboard/ mudou:
cd dashboard && npm run lint && npm run build
```

Revise `git diff` em busca de mudanças não intencionais. Para decisão de segurança alterada, cubra o caminho feliz **e** a negação em teste. Verifique o fluxo crítico afetado (`docs/UX_FLOWS.md`).

## 8. Documentation

Atualize no mesmo commit quando mudar: comportamento de produto (`PRD`), arquitetura/decisão (`ARCHITECTURE`), schema/auditoria (`DATABASE`), tool/rota (`API`), regra de segurança/lacuna (`SECURITY`), convenção (`CODE_STYLE`), comandos/baseline (`TESTING`), tela/fluxo (`UX_FLOWS`/`DESIGN_SYSTEM`). Não duplique regra entre documentos: linke. Doc desatualizada é bug.

## 9. Conflict Handling

Se requisitos, documentos e código conflitarem: **não adivinhe**. Descreva o conflito (qual documento, qual trecho de código) e peça esclarecimento. Se encontrar uma lacuna de segurança nova, registre-a em `docs/SECURITY.md` §10 e avise; não a esconda nem a "conserte de passagem" fora do escopo.

## 10. Completion Criteria

Uma tarefa só está concluída quando:

- Requisitos e critérios de aceite estão satisfeitos
- Testes e lint passam (ou o que não pôde rodar está declarado)
- Nenhum arquivo não intencional foi alterado; nenhum segredo foi tocado
- Regras de segurança respeitadas; nada de LLM chega à produção sem promoção humana
- Documentação atualizada quando necessário
- O relato final diz: o que mudou, o que foi testado, o que **não** foi testado e as limitações restantes

## 11. Comandos úteis

```bash
uv sync                                                         # dependências
CLIENT_ID=<cliente> uv run python -m mcp_engine.core.mcp_server # servidor MCP stdio (MCP_USER_EMAIL=<perfil>)
CLIENT_ID=<cliente> uv run uvicorn mcp_engine.core.http_app:app --port 8000   # HTTP/SSE + /api
uv run python -m mcp_engine.core.user_store                     # seed de usuários DEMO (nunca em cliente real)
uv run python scripts/preflight_demo.py                         # confere as demos
cd dashboard && npm run dev                                     # painel em :5173
```
Variáveis: `CLIENT_ID` (padrão `example`), `CONFIG_ROOT` (`clients`), `DATA_ROOT` (`data`), `MCP_USER_EMAIL`/`MCP_API_TOKEN`.
