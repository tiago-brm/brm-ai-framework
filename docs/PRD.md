# Product Requirements Document

> **Fonte de verdade de "o que estamos construindo".** Atualize quando requisitos, features, fluxos ou escopo mudarem.
> Legenda de status: ✅ implementado · 🟡 parcial · ⏳ planejado.
> Estado verificado em 2026-10-11 contra o código (`uv run pytest`: 278 passed, 1 skipped, 2 failed — os 2 que falham precisam baixar o modelo de embeddings e não rodam sem rede; ver [TESTING.md](TESTING.md)).

## 1. Product Overview

- **Product Name:** BRM Framework (motor: *BRM Engine*, pacote `mcp_engine`)
- **One-Line Description:** Motor de agentes MCP que deixa o cliente da BRM Solutions usar o Claude em linguagem natural sobre seus sistemas e seu conhecimento, com regras de negócio, DLP, controle de acesso e auditoria aplicados pelo motor, nunca pelo modelo.
- **Product Type:** Plataforma B2B implantada **por cliente** (engine + painel de administração).
- **Primary Platform:** Servidor MCP local (stdio, para Claude Desktop / Claude Code) e remoto (HTTP/SSE via FastAPI) + painel web (React) para o `super_admin`.

## 2. Problem

- **Que problema resolvemos?** Organizações com dado sensível e processo regulado (escritórios jurídicos e contábeis, clínicas, escolas) querem usar IA no dia a dia, mas não podem deixar um LLM agir livremente: ele não sabe quem pode ver o prontuário, qual valor exige aprovação, nem o que não pode sair da máquina do cliente.
- **Por que importa?** Sigilo profissional e LGPD (ex.: Res. CFP 001/2009 e 06/2019 no cliente de psicologia). Uma ação indevida ou um vazamento custa mais do que o ganho de produtividade.
- **Como o usuário resolve hoje?** *(Hipótese a validar com os clientes-piloto)* Usa chat de IA avulso, sem governança, colando dados à mão; ou continua em processo manual. Integrações sob medida custam meses por cliente.

## 3. Goal

- **Primary Goal:** Entregar valor rápido **e seguro** para 1–3 clientes reais (Fase 1), com hardening desde o primeiro deploy.
- **Secondary Goals:**
  - Fazer o cliente falar com o sistema em linguagem natural, sem conhecer nomes de skill nem argumentos.
  - Tornar o conhecimento do cliente (vault) uma fonte consultável e governada, não uma cópia solta.
  - Deixar o caminho aberto para self-service de regras (Fase 2) **só quando houver demanda real**, sem construí-lo antes.
  - Manter o produto agnóstico de segmento: jurídico e clínica são *clientes*, não o produto.

## 4. Target Users

### Operador BRM (`super_admin`)
- **Descrição:** Os dois sócios da BRM Solutions e o responsável técnico do cliente, quando houver.
- **Needs:** Onboardar cliente rápido; ver tudo (usuários, permissões, tools, regras, vault, auditoria); provar governança numa demonstração; promover regras/documentos propostos por IA.
- **Pain points:** Trocar de perfil durante apresentação é frágil no Claude Desktop; editar YAML à mão; falta de visão única do que o motor expõe.

### Profissional do cliente (`admin`) — ex.: sócio, psicólogo responsável
- **Needs:** Consultar e agir sobre os casos sob sua responsabilidade, em linguagem natural; gerar documentos padronizados.
- **Pain points:** Burocracia repetitiva; medo de errar uma regra normativa.

### Apoio/leitura (`viewer`) — ex.: recepção, estagiário, aluno
- **Needs:** Tarefas operacionais limitadas (ex.: declaração de comparecimento, FAQ, aulas).
- **Pain points:** Pedir para alguém com acesso; receber negativas sem saber o próximo passo.

> Perfis são por cliente e vivem em `users.db` (ver [DATABASE.md](DATABASE.md)). `anonymous` (sem credencial) é tratado como `viewer`.

## 5. User Stories

- Como **profissional**, quero pedir "resuma as últimas sessões do paciente com ansiedade de avaliação" para não precisar saber o ID nem o nome da skill.
- Como **recepção**, quero que o sistema recuse abrir um prontuário sem vínculo e me diga a quem pedir (`proximo_passo`), para não violar sigilo nem ficar travada.
- Como **operador BRM**, quero ver como cada perfil seria tratado num cenário (simulador e matriz) para demonstrar e validar a governança antes de produção.
- Como **operador BRM**, quero que uma regra descrita em uma nota vire *rascunho* de regra, para revisar e só então promover.
- Como **operador BRM**, quero ligar/desligar o acesso de um perfil a uma tool sem deploy.
- Como **dono do dado**, quero que nada saia do ambiente do cliente sem passar por allowlist de domínio e DLP, e que cada ação deixe trilha à prova de adulteração silenciosa.

## 6. Core Features

> IDs `F-xx` são estáveis; use-os em commits, testes e conversas com o agente.

### F-01 — Governança de ações (skills + regras) ✅
- **Purpose:** Toda ação do cliente é uma *skill* declarada em `skills.yaml`; antes de executar, o motor avalia as regras (`rules.yaml`, JSONLogic sandboxado).
- **User Flow:** Pedido em linguagem natural → tool da skill → regras → allow / deny / require_approval → interceptor → resposta.
- **Inputs:** `skill_name`, argumentos tipados, identidade (`actor`, `actor_role`), fatos derivados.
- **Outputs:** `{allowed, reason, rule_id, proximo_passo, result, payload_digest}`.
- **Acceptance Criteria:**
  - Regras são dado (JSONLogic), nunca código; condição malformada levanta `RuleEvaluationError` com o `rule_id`.
  - Primeira regra que casa decide; sem regra casada, o efeito é `allow` (ver [SECURITY.md](SECURITY.md) §Lacunas).
  - `deny` e `require_approval` nunca executam a skill e sempre são auditados.
- **Edge Cases:** skill inexistente (negada e auditada); regra referenciando fato ausente; `YAML` com chave desconhecida (falha na carga).

### F-02 — Interceptor: allowlist SSRF + DLP ✅
- **Purpose:** Bloquear destino fora da allowlist/IP privado e dado sensível na resposta.
- **Acceptance Criteria:** `domain_allowlist` por `fnmatch` case-insensitive; `block_private_ip` padrão `true`; DLP `deny` bloqueia, `mask` reporta e é redigido para perfis não-admin; todo evento vai ao audit.
- **Edge Cases:** hostname vazio; IP literal privado; padrão regex inválido.
- **Nota de estado:** a execução HTTP das skills é **simulada por design** na Fase 1 (resposta mock); só `enviar_email` dispara efeito real, e apenas com SMTP/Resend configurado.

### F-03 — Auditoria append-only com hash-chain ✅
- **Purpose:** Trilha por cliente, exportável e com detecção de adulteração.
- **Acceptance Criteria:** `audit.jsonl` por cliente, cada linha com `previous_hash`; `iter_events` levanta `AuditChainError` na primeira quebra; filtros por data/ação/decisão.

### F-04 — RBAC por tool, dois transportes ✅
- **Purpose:** Saber *quem* chama e filtrar/validar tools por perfil, em stdio (env var) e HTTP/SSE (Bearer).
- **Acceptance Criteria:** `tools/list` filtrado e `tools/call` revalidado (negativa auditada); `super_admin` com bypass incondicional; permissões editáveis em runtime (`tool_permissions`); só `super_admin` atribui `super_admin`.
- **Edge Cases:** token desconhecido ou header malformado → `anonymous` (`viewer`); tool sem nenhuma linha de permissão é **aberta a todos os perfis**.

### F-05 — Vault plugável (Obsidian e Notion) ✅
- **Purpose:** Conhecimento curado do cliente, consultável por busca híbrida (FTS5 + vetorial, fusão RRF) com embeddings **locais**.
- **Acceptance Criteria:** contrato `VaultProvider`; Obsidian bidirecional (lê/escreve `.md`, wikilinks, frontmatter) com detecção de conflito (cópia `<nota>.md-brm-conflict-<timestamp>`, sem extensão `.md`, logo fora do índice); Notion como espelho sobre o mesmo índice; índice reconstruível, fora do vault; reindex antes de cada busca.
- **Edge Cases:** `vault_root` explícito inexistente → erro (não cria); Notion sem token/raiz → `NotionConfigError`; nota não parseável → ignorada no reindex.

### F-06 — Acesso por nota no vault (`vault_access.yaml`) ✅
- **Purpose:** Governar o que a *leitura* devolve, nota a nota.
- **Acceptance Criteria:** política `allow` / `metadata_only` (campos em `expose`) / `deny`; primeira política que casa decide, senão `default` (recomendado `deny`); notas negadas somem do total sem sinalizar; escrita restrita a `write_roles`; acesso privilegiado auditado; busca registra contagem e políticas, nunca conteúdo.
- **Edge Cases:** política com JSONLogic que falha → trata como "não casou"; sem `vault_access.yaml` → vault sem filtro (cliente `example`).

### F-07 — Fatos derivados ✅
- **Purpose:** Fatos de segurança (vínculo com paciente, menor de idade, matrícula) são calculados pelo motor a partir do vault, **nunca informados pelo modelo**.
- **Acceptance Criteria:** valor derivado vence o argumento informado; divergência vira evento `<skill>:fato_divergente`; sem nota → `if_missing`.

### F-08 — Uso em linguagem natural ✅
- **Purpose:** O cliente de chat enxerga uma tool tipada por skill, com descrição e exemplos, e instruções do cliente (`context`).
- **Acceptance Criteria:** parâmetros derivados não aparecem na tool; respostas negadas/pendentes trazem `proximo_passo`; se houver mais de um candidato a pessoa/caso, o modelo pergunta antes de agir (instrução do servidor).

### F-09 — Agente interpretador: nota → rascunho de regra/skill ✅
- **Purpose:** Transformar uma nota em português em rascunho (`rules.draft.jsonl` / `skills.draft.jsonl`), validado por Pydantic e JSONLogic.
- **Acceptance Criteria:** nunca escreve direto em produção; DLP de saída **estrito** (qualquer padrão, inclusive `mask`, bloqueia o envio da nota ao LLM); promoção é chamada separada e nomeada; tudo auditado.
- **Edge Cases:** sem credenciais LLM (`ANTHROPIC_API_KEY` ou `OPENROUTER_API_KEY`) → erro só quando o agente é usado.
- **Estado:** o gate humano é *procedural*, mas agora exige `admin`/`super_admin` e registra a identidade de quem aprovou (L-01/L-02 resolvidas).

### F-10 — Gerador de documentos padronizados ✅
- **Purpose:** Redigir modelo (declaração, petição, contrato, checklist) como rascunho; promoção grava no vault.

### F-11 — Painel `super_admin` 🟡
- **Purpose:** Ver e administrar tudo o que o motor expõe.
- **Telas:** Visão, Usuários, Matriz (permissões), Tools, Regras, Vault, Auditoria, Simular, Governança.
- **Acceptance Criteria:** toda rota `/api/*` exige `super_admin`; login por URL + token Bearer.
- **Estado:** 🟡 **o `build` falha em clone limpo** — `dashboard/src/lib/` está no `.gitignore` raiz (`lib/`) e nunca foi versionado (ver [TESTING.md](TESTING.md)). Regras e vault são somente leitura no painel.

### F-12 — Simulador e matriz de governança ✅
- **Purpose:** Executar como outro perfil (sem efeito externo) e cruzar cenários × perfis.
- **Acceptance Criteria:** identidade simulada vale só na requisição; cada execução grava `simulacao:*` com `executado_por` e `como`; matriz não escreve na auditoria; desligável por cliente (`simulador: false`).

### F-13 — Consulta DataJud (CNJ) ✅
- Consulta processo pelo número único, resolve o tribunal, grava nota no vault. Falha segura: tribunal errado resulta em "não encontrado", nunca em processo diferente.

### F-14 — E-mail real (`enviar_email`) 🟡
- Envio via Resend ou SMTP apenas se configurado e **depois** do gate de allowlist/DLP. Fora isso, simulado.

## 7. Non-Goals

Intencionalmente **fora** desta versão (Fase 1):

- Control Plane centralizado, multi-tenant, e Rule Builder visual.
- Acesso cross-client: `super_admin` é por cliente.
- RPA serverless (Wasm/Lambda) e vector store compartilhado.
- Suporte/testes contra múltiplos LLMs (o protocolo MCP já dá portabilidade).
- "Hot-swap" de regra para todos os clientes sem staging.
- Edição de regras/vault pelo painel; chat dentro do painel.
- Sync do vault para a nuvem da BRM (mudaria o modelo de ameaça; exigiria spec própria).
- Reconhecimento de nomes próprios no DLP (NER), criptografia do vault em repouso.

## 8. MVP Scope

**Must have (Fase 1 — ✅ salvo indicação):** F-01 a F-08, F-12, F-13; hardening de segurança; deploy isolado por cliente.
**Must have pendente:** corrigir o build do painel (F-11) e revisar as lacunas P1 restantes de [SECURITY.md](SECURITY.md) antes de dado real de cliente.
**Nice to have:** F-09/F-10 com gate humano real; F-14 em produção; rate limit no provider Notion.
**Future (Fase 2/3):**
- Fase 2 — Control Plane: Rule/Skill Builder, sincronização com *staging → canary → produção*, mTLS/JWT rotativo, NER no DLP. Só após o gatilho de saída abaixo.
- Fase 3 — Escala: RPA serverless, vector store multi-tenant, múltiplos LLMs.

**Gatilho de saída da Fase 1:** 2–3 clientes pagando **e** pelo menos um pedindo explicitamente "mudar uma regra sem chamar a BRM". Antes disso, não migrar para o Control Plane.

## 9. Success Metrics

> Não há instrumentação hoje; estas são as métricas propostas e como medir com o que já existe.

**Primárias**
- Nº de clientes pagando (gatilho: 2–3) — comercial.
- % de ações negadas/pendentes que chegam ao usuário com `reason` + `proximo_passo` — `audit.jsonl` + testes.
- Zero nota entregue fora da política (`vault_access`) em teste de matriz perfil × tipo de nota — `tests/test_vault_access.py`.

**Secundárias**
- Tempo para onboardar um cliente novo (config → primeira skill governada).
- Nº de rascunhos promovidos vs. rejeitados (`*.draft.jsonl`).
- Eventos `fato_divergente` (modelo tentando informar fato de segurança).

## 10. Constraints

- **Technical:** Python ≥ 3.11; um engine por cliente, código compartilhado e config por cliente; dado permanece na fonte do cliente; embeddings locais (`sentence-transformers`, ~470 MB, torch); Claude-first via MCP; sem Control Plane.
- **Business:** Time de 2 pessoas; sigilo profissional e LGPD; cada decisão ancorada em demanda real, não em antecipação arquitetural.
- **Design:** Painel é ferramenta de administração desktop-first ([DESIGN_SYSTEM.md](DESIGN_SYSTEM.md)).
- **Timeline:** Fase 1 prevista para 0–3 meses a partir de ago/2026 (`spec` original); datas por cliente não estão registradas no repositório.

## 11. Assumptions

- O LLM é confiável apenas como *intérprete de linguagem*; qualquer fato de segurança vem do motor.
- Cada cliente tem seu próprio deploy (cloud isolada BRM ou on-prem via Docker Compose — este último ainda não existe no repositório).
- O vault do cliente é curado por humanos; o engine não é a fonte de verdade do conteúdo.
- Exemplos jurídicos (`example`) e de saúde (`clinica-psi`, `instituto-afw`) são dados de demonstração.

## 12. Open Questions

- **Quem aprova promoções?** Hoje `admin` e `super_admin` promovem e a identidade fica registrada. Deve ser só `super_admin`?
- **Efeito padrão das regras:** manter `allow` sem regra casada (deny-list) ou migrar para `deny` por padrão em clientes de saúde?
- **Execução HTTP real das skills:** quando e para qual cliente deixa de ser simulada? Exige timeout/retry/TLS/segredos por tenant.
- **Deploy por cliente:** existe `docker-compose.yaml` por cliente? (previsto na spec, ausente no repo.)
- **`default: deny` vs `allow`** em `vault_access` para clientes existentes (recomendação anterior: `deny` em saúde, `allow` no `example`).
- **A recepção pode achar paciente por nome?** Só `metadata_only` com nome completo se o cliente aprovar (dado identificável).
- **Fonte do vault do cliente `example`:** hoje aponta para um caminho local e uma página Notion pessoais — substituir por fixture neutra?
- **Documentos legais/regulatórios por segmento:** quem valida os textos normativos citados nas regras de cada cliente?

## 13. Definition of Done

A feature/produto está completa quando:

- [ ] Critérios de aceite da feature `F-xx` satisfeitos e testados
- [ ] Fluxos críticos de [UX_FLOWS.md](UX_FLOWS.md) funcionam
- [ ] `uv run ruff check .` e `uv run pytest -q` verdes; `npm run build` e `npm run lint` verdes quando o painel mudar
- [ ] Checklist de [SECURITY.md](SECURITY.md) §9 satisfeito e nenhuma lacuna nova introduzida
- [ ] Documentação atualizada ([API.md](API.md), [DATABASE.md](DATABASE.md), [ARCHITECTURE.md](ARCHITECTURE.md) quando aplicável)
- [ ] Checks não executados e limitações restantes registrados no relato da entrega
