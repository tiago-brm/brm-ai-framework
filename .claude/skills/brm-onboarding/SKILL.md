---
name: brm-onboarding
description: >
  Onboarding de um novo cliente (escritório) no BRM Framework — configuração,
  guardrails (DLP/allowlist), hard rules/skills via agente interpretador, e
  documentos padronizados via gerador de documentos, ambos com draft +
  aprovação humana antes de valer. Dispare quando o usuário disser "criar
  cliente novo no BRM", "onboardar escritório", "configurar guardrails pro
  cliente X", "criar hard rule", "gerar documento padrão pro vault", "gerar
  modelo de petição/contrato/notificação", ou qualquer variante de configurar
  um cliente novo ou popular o vault dele com regras e documentos.
version: 1.0.0
---

# brm-onboarding — Onboarding de Cliente no BRM Framework

Empacota o fluxo (hoje manual) de configurar um escritório novo no motor:
config base → guardrails → vault → regras/skills → documentos padronizados.
Cada etapa de geração por IA (regras, skills, documentos) sempre passa por
**draft → revisão humana → promoção explícita** — nunca escreve direto em
produção. Ver `docs/spec.md` para a arquitetura completa.

## Passo 1 — Config base do cliente

Criar `clients/<cliente>/config/{rules.yaml, skills.yaml, client_config.yaml}`.
Referência completa com exemplos: `docs/guia-teste-claude-desktop.md` (passos 1-2).

```yaml
# client_config.yaml — mínimo pra subir
client_id: <cliente>
block_private_ip: true
domain_allowlist: []      # preenchido no passo 2
dlp_patterns: []          # preenchido no passo 2
vault_root: /caminho/absoluto/pro/vault/obsidian   # opcional; se omitido,
                                                     # usa clients/<cliente>/vault/
```

`rules.yaml` e `skills.yaml` podem começar vazios (`rules: []` / `skills: []`)
— populados no passo 4.

## Passo 2 — Guardrails (não-negociável)

Antes de qualquer regra de negócio, definir em `client_config.yaml`:

- **`domain_allowlist`** — todo domínio que uma skill vai chamar. Nada fora
  da lista passa, é checado em runtime pelo interceptor (SSRF-safe).
- **`dlp_patterns`** — padrões sensíveis do cliente (CPF, CNPJ, senha, token,
  cartão). `action: deny` bloqueia; `action: mask` redige mas deixa passar
  (skills) — no caminho vault→LLM (gerador de regra/documento), qualquer
  match bloqueia, `mask` incluso, sem exceção.

Isso é o que protege as etapas seguintes — nunca pular.

## Passo 3 — Vault

Convenção de pastas (não é imposta pelo código, mas mantém consistência
entre clientes):

```
vault/
├── teses/              # teses jurídicas consolidadas
├── modelos/             # modelos de petição/contrato — ver passo 5
├── config_drafts/       # notas que viram HardRule/Skill — ver passo 4
└── <outras pastas do cliente>
```

Se o cliente já tem um vault Obsidian real, apontar `vault_root` no
`client_config.yaml` (passo 1) — se o caminho não existir, o engine falha
alto em vez de criar um vault vazio silenciosamente (erro de digitação vira
sinal, não bug invisível).

## Passo 4 — Hard rules e skills (agente interpretador)

Para uma regra de negócio ou integração nova:

1. Escrever uma nota em `config_drafts/<nome>.md` descrevendo em português o
   que deve ser permitido, bloqueado, ou exigir aprovação (regra) — ou qual
   chamada HTTP fazer (skill).
2. Chamar a tool `vault_interpret_and_propose(note_id)` — gera um draft
   (JSONLogic puro pra regra, nunca código) e **não** escreve em
   `rules.yaml`/`skills.yaml` ainda.
3. Revisar com `vault_list_drafts(kind="rule"|"skill", status="pending")`.
4. Só depois de revisão humana: `vault_promote_draft(draft_id, kind)` —
   agora sim entra em produção.

Se a nota for vaga, perigosa, ou impossível de converter, o agente rejeita
em vez de inventar uma regra "razoável" — isso é proposital.

## Passo 5 — Documentos padronizados pro vault (gerador de documentos)

Para um modelo de petição, contestação, contrato, notificação, checklist etc.:

1. Chamar `vault_generate_document(instrucoes)` com o pedido em português —
   não precisa de nota pré-existente, é direto por instrução. Ex.:
   `"gere um modelo de notificação extrajudicial de cobrança de honorários,
   prazo de 5 dias úteis"`.
2. O agente nunca inventa jurisprudência/lei/número de processo específico —
   usa `[verificar]` como placeholder quando não tem certeza.
3. Revisar com `vault_list_drafts(kind="document", status="pending")`.
4. Só depois de revisão humana: `vault_promote_document(draft_id)` — agora
   sim vira uma nota real no vault, buscável por `vault_search`.

## Pré-requisito técnico (ambos os agentes)

Precisa de `ANTHROPIC_API_KEY` **ou** `OPENROUTER_API_KEY` no `.env` do
projeto — o motor escolhe automaticamente qual usar (agnóstico de
fornecedor, ver `mcp_engine/vault/_agent_common.py`). Sem nenhuma das duas,
`vault_interpret_and_propose` e `vault_generate_document` falham alto com
mensagem clara — nunca silenciosamente.

## Checklist de pronto

- [ ] `clients/<cliente>/config/*.yaml` criados e válidos
- [ ] `domain_allowlist` cobre todo domínio real que as skills vão chamar
- [ ] `dlp_patterns` cobre os dados sensíveis do cliente (mínimo: CPF/CNPJ)
- [ ] Vault apontado ou criado, com `config_drafts/` existindo
- [ ] Pelo menos uma regra e um documento gerados e promovidos como teste
- [ ] `uv run pytest -q` verde antes de considerar o onboarding concluído
