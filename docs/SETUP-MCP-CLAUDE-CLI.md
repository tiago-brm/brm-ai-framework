# Setup Rápido: Testar BRM Engine no Claude Code CLI

**Situação:** Você tem o `claude` CLI instalado e quer registrar seu servidor MCP para usar nas conversas (como estamos fazendo agora).

**Tempo:** 5 minutos.

## 1. Criar Cliente com Seus Dados

Crie uma pasta com sua configuração:

```bash
mkdir -p ~/meu-cliente-brm/clients/meaempresa/config
```

Preencha os 3 arquivos YAML com **seus dados reais:**

### `clients/meaempresa/config/rules.yaml`
```yaml
schema_version: 1
client_id: meaempresa

rules:
  - id: limite-saque
    description: Nega saques acima de R$ 10.000
    condition: { ">": [{"var": "valor_saque"}, 10000] }
    effect: deny
    message: Saque acima do limite diário
```

### `clients/meaempresa/config/skills.yaml`
```yaml
schema_version: 1
client_id: meaempresa

skills:
  - name: sacar_dinheiro
    description: Saca dinheiro
    method: POST
    url_template: https://api.meubancocrip.com/saque
    allowed_domains: [api.meubancocrip.com]
    timeout_seconds: 30.0
```

### `clients/meaempresa/config/client_config.yaml`
```yaml
client_id: meaempresa
block_private_ip: true

domain_allowlist:
  - api.meubancocrip.com

dlp_patterns:
  - pattern: '\d{3}\.\d{3}\.\d{3}-\d{2}'
    label: CPF
    action: deny
```

## 2. Registrar o Servidor MCP no Claude Code CLI

Abra um terminal e execute:

```bash
cd /Users/seu_user/brm-ai-framework

claude mcp add brm-engine -- \
  uv run python -m mcp_engine.core.mcp_server \
  -e CLIENT_ID=meaempresa \
  -e CONFIG_ROOT=$(pwd)/clients \
  -e DATA_ROOT=$(pwd)/data
```

**O que cada parte faz:**
- `claude mcp add brm-engine` — registra um servidor chamado "brm-engine" (aparece nas conversas)
- `uv run python -m mcp_engine.core.mcp_server` — comando que inicia o servidor
- `-e CLIENT_ID=meaempresa` — passa a variável de ambiente pro servidor (seu cliente)
- `-e CONFIG_ROOT=...` — onde estão os YAMLs
- `-e DATA_ROOT=...` — onde salvar os audit logs

**Confirmação:** você vai ver:
```
✓ MCP server "brm-engine" added successfully
```

## 3. Verificar que Funcionou

```bash
claude mcp get brm-engine
```

Deve mostrar:
```
✓ brm-engine (stdio)
  Status: Connected
  Tools: call_skill, query_audit_log
```

## 4. Testar no Claude Code CLI

Agora **nesta conversa mesma**, use o servidor:

```
Use a tool call_skill para sacar_dinheiro com valor_saque=5000.
```

O Claude vai invocar a ferramenta (você vai ver "Using tool: call_skill") e receber o resultado do seu servidor MCP rodando localmente.

Teste os cenários:

| Teste | Comando |
|---|---|
| **Permitido** | `sacar_dinheiro com valor_saque=5000` → `allowed=true` |
| **Negado por regra** | `sacar_dinheiro com valor_saque=20000` → `allowed=false, "Saque acima do limite"` |
| **Bloqueado por DLP** | `sacar_dinheiro com cpf=123.456.789-00` → `allowed=false, "DLP: found CPF"` |
| **Auditoria** | `query_audit_log com decision=denied` → mostra os saques bloqueados |

## 5. Verificar Auditoria

Os eventos são salvos em `data/meaempresa/audit.jsonl`:

```bash
tail data/meaempresa/audit.jsonl | python -m json.tool
```

Você vai ver cada chamada registrada:
- Quem: `"actor": "claude"`
- Quê: `"action": "sacar_dinheiro"`
- Quando: `"occurred_at": "2026-08-26T..."`
- Permitido?: `"decision": "denied"` ou `"allowed"`
- Por quê?: `"rule_id": "limite-saque"`

## 6. Atualizar Config (sem Restart)

Se editar os YAMLs, o servidor **não relê automaticamente** — você precisa:

```bash
# Remove o servidor antigo
claude mcp remove brm-engine

# Re-adiciona (vai ler os YAMLs novos)
claude mcp add brm-engine -- \
  uv run python -m mcp_engine.core.mcp_server \
  -e CLIENT_ID=meaempresa \
  -e CONFIG_ROOT=$(pwd)/clients \
  -e DATA_ROOT=$(pwd)/data
```

## Pronto! 

Agora qualquer conversa sua com o Claude vai poder chamar `call_skill` e `query_audit_log` — as regras e DLP dela executam localmente, os dados ficam no seu `data/`.

### Próximas ideias:

- **Versionar os YAMLs em git:** `git init`, commit rules/skills, histórico de mudanças
- **Monitorar audit log:** `watch -n 2 'tail data/meaempresa/audit.jsonl'`
- **Testar attack vectors:** tente passar uma query SQL em um argumento, veja se DLP pega
- **Multi-cliente:** crie outro `clients/outro_cliente/config/...` e faça `claude mcp add brm-engine-2 ...` com `CLIENT_ID=outro_cliente`

**Troubleshooting:**

```bash
# Ver todos os servidores registrados
claude mcp list

# Ver logs detalhados (se der erro)
claude mcp get brm-engine

# Remover se quiser começar de novo
claude mcp remove brm-engine
```
