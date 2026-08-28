# 009 — Agente Interpretador (Obsidian → HardRule/Skill draft) (spec)

**Ordem da spec:** Fase 2, **segunda fase, depois de 008 shipped + agno-framework skill atualizada para v3**. O usuário descreveu a visão: ler notas em markdown do Obsidian do cliente e gerar propostas de HardRules ou Skills que o cliente possa depois promover para a config ativa. Isto é o "segundo cérebro" operacional — não só armazenar conhecimento, mas extrair **regras executáveis** dele.

**Status desta spec:** não é para implementar agora. É para **deixar registrada a visão, os riscos de segurança, e as guardrails não-negociáveis** antes de qualquer código ser escrito. Muita gente quer AI que "gere regras", isso é exatamente onde a superfície de ataque é enorme — a spec.md Red Team table chama isto: "Execução de código arbitrário disfarçada de regra". Essa spec evita que isso vire "oh, esqueci de pedir aprovação do cliente".

## Escopo deste passo

Um **agente Agno** que:
1. Roda por demanda (chamado pelo cliente via Claude, ou via endpoint do Control Plane na Fase 3)
2. Lê uma nota do vault criada especificamente pra isso (e.g. `config_drafts/nova_regra.md`)
3. Interpreta o markdown como descrição de **uma HardRule ou uma Skill**
4. Gera um JSON válido (regra em JSONLogic, ou skill com url_template)
5. **Salva como draft** (não escreve em `rules.yaml` / `skills.yaml` direto)
6. Cliente revisa o draft no Obsidian ou via UI de staging
7. Apenas **após aprovação humana explícita** o draft é promovido para config ativa
8. Toda tentativa de geração (sucesso e erro) é auditada

**Segurança em primeiro lugar:** isto é um vetor de injeção direta de lógica de negócio. As guardrails abaixo não são "future improvement" — são pré-requisito de pronto.

## Pré-requisitos

### 1. Spec 008 (VaultProvider) deve estar shipped
O agente precisa de `vault.get_related()` para contextualizar — se uma nova regra sobre "transferências internacionais" é proposta, ele precisa achar notas sobre "compliance internacional" etc.

### 2. Agno-framework skill atualizado para v3
Hoje (data deste spec) a skill documenta Agno 2.x; v3 (já ao vivo em PyPI) tem:
- Model shorthand: `model="anthropic:claude-sonnet-4-5"` vs. classe `Claude(id=...)`
- AgentOS com JWT RBAC, multi-tenant isolation, Interfaces (Slack/Telegram/etc.)
- MCP Server em `docs.agno.com/mcp` para input/context via MCP
- Scheduled background jobs

A skill precisa ser atualizada de modo a documentar:
- Ambas as sintaxes (shorthand E classe) com exemplos
- Uso de `response_model=Pydantic` para gerar regra/skill validado
- Tools de "leitura" (via MCP) que o agente pode chamar pro vault
- `requires_confirmation=True` para tools sensíveis (gerar regra, promover draft)

**Tarefa:** antes de iniciar código de 009, executar `/agno` (ou ler `docs.agno.com/sdk/introduction` + `docs.agno.com/agents/overview` + `docs.agno.com/tools/overview`) e atualizar `~/.claude/skills/agno-framework/SKILL.md` + referências associadas com exemplos v3 reais, exatamente como documentado in loco.

### 3. Estrutura de draft proposto

```text
clients/<cliente>/config/
├── rules.yaml                      # config ATIVA (versionada, produção)
├── skills.yaml
├── rules.draft.jsonl              # NOVO — draft de regras geradas, append-only
└── skills.draft.jsonl             # NOVO — draft de skills geradas, append-only
```

Cada linha é um JSON: `{"draft_id", "from_note_id", "generated_at", "content": {...}, "status": "pending|approved|rejected", "reviewed_by": "user123", "reviewed_at": ...}`

Ou em YAML simples (menos parsing necessário):
```yaml
drafts:
  - id: "draft-uuid-1"
    from_note: "config_drafts/nova_regra_prescricao.md"
    type: "rule"          # rule ou skill
    generated_at: "2026-08-27T..."
    generated_by: "claude"
    content: { ... JSONLogic completo ... }
    status: "pending"     # pending → approved → active
    review: ...
```

## Detalhes de implementação

### Agente Agno (`mcp_engine/vault/interpreter_agent.py`)

```python
from agno.agent import Agent
from agno.models.anthropic import Claude
from pydantic import BaseModel, Field

class GeneratedRule(BaseModel):
    id: str = Field(description="Unique rule ID, kebab-case")
    description: str = Field(description="Descrição da regra em português")
    condition: dict = Field(description="JSONLogic condition completo")
    effect: Literal["allow", "deny", "require_approval"]
    message: str | None = Field(description="Mensagem de feedback se bloqueada")

class GeneratedSkill(BaseModel):
    name: str
    description: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    url_template: str
    allowed_domains: list[str]
    parameters: dict = Field(default_factory=dict)

class InterpreterResult(BaseModel):
    success: bool
    type: Literal["rule", "skill"] | None
    content: GeneratedRule | GeneratedSkill | None
    error: str | None

interpreter = Agent(
    name="Vault Interpreter",
    model=Claude(id="claude-sonnet-4-5"),
    instructions="""Você é um especialista em redigir regras de negócio para um sistema de gestão (BRM).
Quando receber uma descrição de uma regra ou skill em markdown:

1. Extraia a intenção (o que deve ser permitido/bloqueado/aprovado)
2. Estruture como JSONLogic puro (nunca eval, nunca Python)
3. Gere um ID único e descritivo
4. Valide que a regra faz sentido jurídico/operacional
5. Retorne um objeto estruturado (JSON validado contra Pydantic schema)

Regras inválidas (impossíveis de converter, muito vagas, riscos óbvios): rejeite com mensagem clara.
""",
    response_model=InterpreterResult,
    tools=[VaultTools(vault_provider), ...],  # tools pra ler notas relacionadas, contexto
)

def interpret_draft(client_id: str, note_id: str, vault: VaultProvider) -> InterpreterResult:
    """Cria draft de regra/skill a partir de uma nota."""
    note = vault.get_note(client_id, note_id)
    if not note:
        return InterpreterResult(success=False, error=f"Note {note_id} not found")
    
    related = vault.get_related(client_id, note_id)
    context = f"Notas relacionadas: {', '.join(n.title for n in related)}"
    
    prompt = f"""A partir desta nota no vault do cliente:

# {note.title}
{note.content}

{context}

Gere uma regra ou skill. Retorne objeto estruturado.
"""
    
    result = interpreter.run(prompt)
    
    # Registra attempt em audit — sucesso ou erro, quem pediu
    AuditEvent(
        client_id=client_id,
        action="vault_generate_draft",
        decision=ALLOWED if result.success else DENIED,
        details={"from_note": note_id, "type": result.type, "error": result.error},
        actor="interpreter_agent",
    )
    
    return result
```

### Fluxo de aprovação + promoção

```python
def propose_draft(client_id: str, note_id: str, vault: VaultProvider, config_loader: ConfigLoader) -> dict:
    """Step 1: Gera draft, não salva ainda."""
    result = interpret_draft(client_id, note_id, vault)
    if not result.success:
        return {"ok": False, "error": result.error}
    
    draft_id = uuid.uuid4().hex[:8]
    draft = {
        "id": draft_id,
        "from_note": note_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "type": result.type,
        "content": result.content.model_dump(),
        "status": "pending",
    }
    
    # Salva em draft file (append)
    draft_file = config_loader.config_root / client_id / "config" / "rules.draft.jsonl"
    with open(draft_file, "a") as f:
        f.write(json.dumps(draft) + "\n")
    
    return {"ok": True, "draft_id": draft_id, "preview": draft}

@tool(requires_confirmation=True)  # Agno forces human click
def promote_draft(client_id: str, draft_id: str, config_loader: ConfigLoader) -> dict:
    """Step 2: Humano revisa + clica "promote"."""
    ruleset = config_loader.get_rules(client_id)
    draft = load_draft(client_id, draft_id)  # busca no .draft.jsonl
    
    # Valida schema
    try:
        if draft["type"] == "rule":
            rule = Rule.model_validate(draft["content"])
            new_ruleset = RuleSet(
                schema_version=1,
                client_id=client_id,
                rules=ruleset.rules + (rule,),
            )
        # ... similar pra skill
    except ValidationError as e:
        return {"ok": False, "error": f"Draft inválido: {e}"}
    
    # Escreve em rules.yaml (ou skill.yaml) — promove draft → active
    config_loader.save_rules(new_ruleset)
    
    # Marca draft como "approved"
    mark_draft_approved(client_id, draft_id)
    
    # Audita
    AuditEvent(..., action="vault_promote_draft", decision=ALLOWED, ...)
    
    return {"ok": True, "message": f"Regra ativada em produção."}
```

### Integração com `BRMEngine`

Novo tool MCP:
```python
@server.tool()
def vault_interpret_and_propose(note_id: str) -> dict:
    """Propõe draft de regra/skill a partir de nota."""
    result = propose_draft(engine.client_id, note_id, engine.vault_provider, engine.config_loader)
    return result

@server.tool()
def vault_promote_draft(draft_id: str) -> dict:
    """Promove draft aprovado para config ativa."""
    # Pede confirmação via Agno decorator @tool(requires_confirmation=True)
    result = promote_draft(engine.client_id, draft_id, engine.config_loader)
    return result
```

## Guardrails de segurança (não-negociáveis)

**🚨 Nenhuma geração automática → produção. Nunca.**

| Risco (Red Team) | Controle (Blue Team) | Como implementa |
|---|---|---|
| LLM injeta JSON inválido como "regra" | Validação Pydantic obrigatória. Draft rejeitado se falha schema. | `result.content: GeneratedRule \| GeneratedSkill` typed, parse falha se inválido |
| LLM gera regra que nega tudo (DoS funcional) | Humano revisa antes de promover. Staging obrigatório. | `promote_draft` requer `@tool(requires_confirmation=True)` |
| LLM gera regra que contorna controle (SSRF por skill). | Draft só gera propostas, cliente valida intenção de negócio. Engine ainda valida domain_allowlist em runtime. | Duplo: cliente aprova intenção, engine aplica allowlist na execução |
| Nota corrupta → agente gera lixo → promove pra produção | Verificação de nota (`content_hash` íntegro) antes de interpretar. | Audit hash-chain (spec 005) já cobre; interpretar nota só se hash confere |
| Histórico de drafts perdido (auditoria fraca) | Cada draft é registrado (nome, nota origem, timestamp, conteúdo, status). Cada promoção é auditada. | `.draft.jsonl` append-only (como audit), `AuditEvent` para cada ação |
| Interpretador oferece "gerar regra pra mim" sem cliente pedir | Tool MCP sempre requer input humano (nota específica a interpretar). Não há "auto-geração contínua". | Fluxo sempre responde-a-prompt do cliente, nunca proativo |

**Além disso:**

1. **JSONLogic-only output:** o agente NUNCA pode gerar `code: "..."` ou qualquer Python. Apenas JSONLogic `condition` que o `rule_evaluator` já sabe desintoxicar.

2. **Sem acesso de escrita direto:** o interpretador pode **ler** qualquer nota (contexto), mas só pode **escrever** drafts (arquivo separado). Escrever em `rules.yaml` vivo requer uma chamada explícita (`promote_draft`) + confirmação humana.

3. **Auditoria completa:** cada tentativa de `interpret_draft`, cada `propose_draft`, cada `promote_draft` é um `AuditEvent` — ator é sempre "interpreter_agent", decision é ALLOWED/DENIED, details incluem o resultado ou erro. Cliente pode depois fazer audit trail de "onde essa regra vem?".

4. **Rollback simples:** se uma regra promovida for ruim, cliente remove a linha do `rules.yaml` (ou git revert se versionado). Diff é claro porque draft teve `from_note_id` — fácil rastrear origem e reverter.

## Pontos que precisam da sua decisão

1. **Qual agente LLM?** A spec assume Claude (agno.models.anthropic.Claude), porque é o que você usa. Mas Agno v3 já suporta OpenAI/Gemini direto. Decisão: pagar pela interpretação com seu crédito do Claude, ou deixar aberto pro cliente escolher modelo?

2. **Contexto de related notes: quantas?** `vault.get_related()` retorna backlinks. O agente precisa desse contexto, mas se há 100 notas linkadas, o prompt fica gigante. Limite de quantas notas de contexto carregar (sugestão: top 5 por relevância, quando tivermos search semântica)?

3. **Uma nota → uma regra/skill, ou múltiplas?** A spec assume 1 draft por execução. Se a nota descreve "3 regras diferentes", o agente gera array de drafts (múltiplas linhas em `.draft.jsonl`) ou rejeita com "divida em 3 notas"?

4. **Como cliente aciona isto?** Via Claude direto ("interprete o draft em config_drafts/nova_regra.md")? Via endpoint do Control Plane (Fase 3)? Ambos? Agora, ou só quando Control Plane existir?

## Fora de escopo (fica para depois)

- Interpretador de outras linguagens (não-markdown) — só .md
- Múltiplas tentativas / refinement loop (agentic reasking) — cliente oferece feedback, agente tenta de novo — é bom, mas é fase 3
- Cache de drafts para "esse padrão já foi aprovado" — legal, mas requer histórico de 50+ drafts primeiro
- Integração com CI/CD (draft → staging test → produção, não manual) — Fase 3, quando houver pipeline

## Critério de pronto (quando implementar)

- Spec 008 já está shipping / shipped
- `agno-framework` skill atualizada com exemplos v3 reais
- `mcp_engine/vault/interpreter_agent.py` com Agno Agent + `interpret_draft()` + `propose_draft()`
- `promote_draft()` com `@tool(requires_confirmation=True)` para force humano validar
- `.draft.jsonl` criados como append-only + versionados em git
- `AuditEvent` para cada ação (`vault_generate_draft`, `vault_promote_draft`, `vault_promote_error`)
- Tests:
  - `test_interpret_valid_note` — nota bem descrita → regra válida
  - `test_interpret_invalid_note` — nota vaga/impossível → rejeição clara
  - `test_draft_schema_validation` — draft com JSON inválido falha antes de salvar
  - `test_promote_draft_requires_confirmation` — promoção pede OK humano (Agno decorator)
  - `test_audit_trail_complete` — cada ação deixa rastro auditável
- `docs/changelog/009-interpreter-agent.md` escrito após implementação

## Post-scriptum: "Segundo cérebro" em produção

Esta spec descreve como **usar conhecimento curado pra gerar regras executáveis**. O complemento natural é o **agente consultor** que, numa conversa, pergunta "deixa eu conferir sua base de conhecimento" e busca contexto via `vault.search()` antes de propor ação. Isso é um terceiro tool (`vault_consult` ou integração com `call_skill`) — separado desta spec, mas faz parte da visão "segundo cérebro operacional".
