# 🚀 Spec Architecture v2.1: BRM Framework (Revisão Pós-Postmortem/Red Team/Blue Team)

> **Status:** Revisão crítica da v2.0 — ajustada para viabilidade com time de 2 pessoas
> **Mudança principal:** de "SaaS multi-tenant + engine on-prem completo dia 1" para **roadmap faseado com hardening de segurança desde a Fase 1**
> **Compatibilidade LLM:** Claude-first via MCP (agnosticismo vem de graça pelo protocolo, não precisa ser testado contra N modelos)

---

## 0. 🎯 O que mudou e por quê

A v2.0 é uma arquitetura correta para uma empresa de 20+ engenheiros com 15 clientes pagantes. Para BRM Solutions hoje (2 sócios, com o primeiro cliente piloto em operação, usando Claude como LLM), ela tem três problemas:

1. **Escopo excessivo para o estágio atual** — Control Plane multi-tenant, Rule Builder visual, RPA dispatcher serverless e vector store são investimentos de meses que atrasam o primeiro cliente pagando.
2. **Superfície de ataque desenhada sem controles equivalentes** — "hot-swap instantâneo para todos os clientes" e "GenericRestSkill preenchida pelo LLM" são recursos poderosos sem os freios (allowlist, sandbox, staging) que os tornam seguros.
3. **Promessa de "zero código / qualquer LLM"** resolve um problema que vocês ainda não têm (múltiplos clientes divergentes, múltiplos LLMs) e atrasa resolver o que vocês têm agora (entregar valor rápido e com segurança para 1-3 clientes).

A revisão abaixo mantém a visão de longo prazo (ela é boa), mas reordena o que se constrói primeiro e adiciona os controles que faltavam.

---

## 1. 🏗️ Visão Geral (mantida, com ajuste de ênfase)

Os 3 pilares continuam válidos:
1. **Camada 1 (Fonte Oficial):** sistemas legados, ERPs, tribunais — dado não sai daqui.
2. **Camada 2 (Vault):** conhecimento curado (teses, modelos, checklists).
3. **Camada 3 (Agente):** LLM orquestrando ferramentas via MCP.

**Ajuste:** a Camada 3 hoje é **Claude, ponto**. O protocolo MCP já garante portabilidade futura — não gastem ciclo de engenharia testando Hermes/Llama antes de terem um segundo LLM realmente exigido por contrato.

### 1.1. 🧠 Camada 2 (Vault) plugável: pgvector + Obsidian opcional

A Camada 2 não deve ser "pgvector" — deve ser um **contrato** (`VaultProvider`) com `search(query)`, `add_note(content, metadata)` e `get_related(note_id)`, seguindo o mesmo padrão de `RuleProvider`/`SkillProvider` da seção 2.1. Isso permite oferecer Obsidian como opção sem reescrever o resto do engine, porque "Obsidian" não é uma tecnologia de servidor — é apenas uma convenção de arquivos (`.md` com wikilinks `[[nota]]` e frontmatter YAML). O motor só precisa de um adapter que leia/escreva nessa convenção.

Duas formas de implementar o adapter, com risco bem diferente:

- **Opção A — pgvector como fonte de verdade, Obsidian como vitrine (recomendado para a Fase 1).** O índice vetorial continua sendo o `PgVectorVaultProvider` (default). Se o cliente quer Obsidian, o `ObsidianVaultProvider` gera um **espelho markdown** (export write-through) a partir do vault para o cliente navegar/ler normalmente no Obsidian. Edição manual do cliente no Obsidian não retroalimenta o agente automaticamente. Baixo risco: sem conflito de concorrência, funciona igual em cloud ou on-prem, não exige o engine no mesmo disco do cliente.
- **Opção B — Obsidian como fonte real, sincronização bidirecional (Fase 2/3).** O motor lê e escreve direto nos `.md` do vault do cliente, indexa localmente (SQLite-vec/Chroma em vez de Postgres) e faz *file-watching* para captar edições manuais. Mais fiel à ideia de "segundo cérebro do cliente", mas exige engine com acesso ao filesystem/sync do cliente, resolução de conflito de concorrência, e parsing de wikilinks se quiserem aproveitar o grafo de relacionamento.

**Recomendação:** implementar `VaultProvider` como interface já na Fase 1, com `PgVectorVaultProvider` como único adapter real. `ObsidianVaultProvider` (Opção A) entra assim que o primeiro cliente pedir — é pouco esforço porque já está atrás do contrato. Opção B só justifica o investimento quando houver demanda explícita por edição bidirecional. Como Obsidian é ferramenta de PKM genérica (não amarrada a nenhum setor), essa camada reforça o posicionamento de ferramenta agnóstica de segmento.

---

## 2. 🧩 Roadmap Faseado (a mudança central desta revisão)

### Fase 1 — MVP Seguro por Cliente (0–3 meses, cliente 1–3)
Objetivo: entregar valor rápido, com segurança desde o dia 1, sem construir SaaS.

- **Sem Control Plane centralizado ainda.** Regras e skills vivem em **arquivo de config versionado (YAML/JSON) por cliente**, no repositório do próprio deploy — não em painel visual.
- **Um MCP Engine por cliente**, deployado onde o cliente exigir (cloud isolada da BRM ou on-prem simples via Docker Compose).
- **Rule Engine desde já com JSONLogic sandboxado** (nunca `eval`/`simpleeval` de string livre) — mesmo em config estática, evita o hábito de código inseguro se infiltrar depois.
- **`GenericRestSkill` com allowlist de domínio obrigatória por cliente** e bloqueio de ranges de IP privado (proteção SSRF) desde a primeira versão — isto não é opcional nem na v1.
- **DLP básico mas real**: regex + lista de padrões (CPF, CNPJ, conta bancária) com mascaramento, documentado como "v1 — expandir para NER na Fase 2".
- **Log de auditoria append-only** desde o início (pode ser tão simples quanto arquivo JSONL com hash do registro anterior) — muito mais barato de fazer certo agora do que retrofit depois de um incidente.
- **Human-in-the-loop manual**: toda ação com impacto financeiro ou envio externo passa por confirmação explícita no chat, sem exceção nesta fase.

**Métrica de saída da Fase 1:** 2-3 clientes pagando, com pelo menos um pedindo explicitamente "quero mudar uma regra sem te chamar" — esse é o sinal real de que o Control Plane self-service vale o investimento.

### 2.1. 🧭 Como iniciar a Fase 1 sem se perder (e sem fechar a porta das fases seguintes)

O risco prático de um roadmap faseado é começar simples demais e depois precisar reescrever tudo quando a Fase 2 chegar. A saída é: **implementação simples por dentro, mas atrás de interfaces que já preveem a evolução.** Trocar "arquivo YAML" por "API do Control Plane" depois vira troca de adapter, não reescrita de arquitetura.

Ordem recomendada de execução:

1. **Defina os contratos antes do código.** Crie interfaces abstratas (Python `Protocol`/ABC): `RuleProvider`, `SkillProvider`, `AuditSink`. Hoje a implementação lê um YAML local — mas o `mcp_engine` nunca fala diretamente com "arquivo", ele fala com a interface. Na Fase 2, você troca a implementação por uma que chama a API do Control Plane sem tocar em uma linha do `rule_evaluator` ou do `interceptor`.
2. **Suba o monorepo mínimo, sem multi-tenant ainda.** `clients/<cliente>/config/` + `mcp_engine/` compartilhado + `audit/`. Resista à tentação de generalizar para "N clientes" agora — isso é over-engineering prematuro. Um cliente real primeiro; abstração de tenant só quando o segundo cliente existir de verdade.
3. **Implemente o `rule_evaluator` sandboxado primeiro.** Use uma lib de JSONLogic (ex.: `json-logic-py`) — nunca `eval` de string livre. Escreva as 3-5 Hard Rules reais do cliente 1 direto no `rules.yaml`. É pouco código, mas é o componente que mais dói corrigir depois se nascer inseguro.
4. **Construa os 3 controles inegociáveis no `interceptor` desde o dia 1.** Allowlist de domínio (bloqueia SSRF), DLP básico por regex/padrões conhecidos, e audit log append-only com hash do registro anterior. Não adie "depois eu boto segurança" — essa é a camada que o Red Team mais explora.
5. **Versione o schema das configs desde a v1.** Coloque `schema_version: 1` no `rules.yaml` e `skills.yaml`. Quando o Rule Builder visual existir na Fase 2, ele precisa gerar exatamente esse schema — versionar agora evita migração dolorosa de configs manuais depois.
6. **Deploy isolado real no cliente 1 antes de generalizar.** Docker Compose simples, testado de verdade no ambiente do cliente piloto (cloud ou on-prem, conforme o cliente exigir). Deixe o atrito real desse primeiro deploy indicar o que precisa de abstração — não adivinhe isso na mesa.
7. **Documente por escrito o gatilho de saída da Fase 1.** No README do projeto, anote o critério explícito (ex.: 2º ou 3º cliente pedindo self-service de regras). Isso evita migrar para o Control Plane cedo demais só porque parece "mais robusto" — a decisão fica ancorada em sinal real de demanda, não em ansiedade de arquitetura.

### Fase 2 — Control Plane Real (quando 3+ clientes exigirem self-service)
Agora sim, construir:
- Rule Builder visual (Low-Code) — só depois de ter 10-20 regras reais em produção para saber que formato de UI faz sentido.
- Skill Builder genérico para APIs REST — com o allowlist e approval workflow abaixo.
- Sincronização via Webhook/Redis, **mas com staging → canary → produção**, nunca "instantâneo para todos" como na v2.0 original.

**Controle novo obrigatório nesta fase:** toda mudança publicada no Control Plane passa por:
1. Validação de schema da regra/skill.
2. Deploy em ambiente de staging do tenant afetado.
3. Confirmação humana (do cliente ou da BRM) antes de promover para produção.
4. Capacidade de rollback com um clique/comando.

### Fase 3 — Escala Multi-Tenant e RPA Serverless
Só aqui entram Wasm/Lambda para RPA, vector store compartilhado com isolamento forte por tenant, e suporte formal a múltiplos LLMs — quando o volume de clientes justificar a complexidade operacional.

---

## 3. 🛡️ Hardening de Segurança (aplicável desde a Fase 1)

Isto substitui a seção 5 original ("Interceptador e DLP") por uma versão com controles concretos, não apenas nomes de componentes:

| Risco identificado (Red Team) | Controle (Blue Team) | Fase mínima |
|---|---|---|
| SSRF via `GenericRestSkill` preenchida pelo LLM | Allowlist de domínio por tenant + bloqueio de IP privado/loopback | Fase 1 |
| Execução de código arbitrário disfarçada de "regra" | JSONLogic puro (dados), nunca `eval` de string livre | Fase 1 |
| DLP regex bypassável | Regex + lista de padrões conhecidos, com plano documentado de evoluir para NER (Presidio) | Fase 1 (básico) → Fase 2 (NER) |
| Credenciais de sistemas legados do cliente (portais governamentais, ERPs on-premise, etc.) centralizadas na nuvem da BRM | Segredos em vault por tenant (nunca na mesma base que guarda config geral), nunca em texto claro | Fase 1 |
| Push instantâneo de regra ruim para todos os clientes | Staging → canary → produção, com rollback documentado | Fase 2 (quando existir Control Plane) |
| Falta de trilha auditável imutável | Log append-only, idealmente hash-chained, exportável | Fase 1 |
| Comunicação Engine ↔ Control Plane sem autenticação forte | mTLS ou JWT rotativo por tenant | Fase 2 |
| Ação de alto risco sem supervisão | Human-in-the-loop obrigatório acima de limiar configurável (valor, irreversibilidade, dado sensível) | Fase 1 |

---

## 4. 📂 Estrutura de Componentes (Fase 1, simplificada)

```text
brm-framework/
├── clients/
│   └── <cliente>/
│       ├── config/
│       │   ├── rules.yaml          # Hard Rules em JSONLogic, versionado em git
│       │   └── skills.yaml         # Definição de Skills REST (com domain allowlist)
│       └── docker-compose.yaml     # Deploy isolado por cliente
├── mcp_engine/                     # Motor compartilhado entre clientes (mesmo código, config por cliente)
│   ├── core/
│   │   ├── rule_evaluator.py       # JSONLogic sandboxado — SEM eval de string livre
│   │   ├── interceptor.py          # DLP + allowlist SSRF + log append-only
│   │   └── mcp_server.py           # Entrypoint FastMCP
│   ├── dynamic_skills/
│   │   └── rest_builder.py         # Skills REST com allowlist obrigatória
│   └── vault/
│       ├── provider.py             # VaultProvider — contrato (search, add_note, get_related)
│       ├── pgvector_adapter.py     # Adapter default — pgvector local, isolado por cliente
│       └── obsidian_adapter.py     # Adapter opcional — espelho .md write-through (Opção A)
└── audit/
    └── hash_chain.py                # Log de auditoria append-only
```

**Nota:** o `control_plane/` e `rpa_workers/` da v2.0 original entram apenas na Fase 2/3 — mantidos na visão de longo prazo, mas fora do MVP.

---

## 5. 🚀 Conclusão

A tese da v2.0 continua correta no horizonte de 2-3 anos: separar a volatilidade dos LLMs e dos sistemas da estabilidade das regras do negócio do cliente, usando MCP como ponte. O ajuste desta revisão é de **sequenciamento e segurança**: construir o motor de regras com sandbox real e allowlist desde o primeiro cliente, adiar o Control Plane visual até haver demanda comprovada, e nunca permitir push instantâneo sem staging — mesmo que isso pareça "menos mágico" na demo.

Isso dá à BRM Solutions velocidade real de implementação (a dor que motivou a spec) sem herdar, ainda no cliente 1, a superfície de ataque de uma plataforma multi-tenant de escala.
