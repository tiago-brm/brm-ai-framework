# Guia: Testar o BRM Engine com Seus Dados no Claude Desktop

Neste guia, você vai:
1. **Criar um cliente com seus dados reais** (não usar o `example` fixture)
2. **Subir o servidor MCP localmente** (roda em background no seu mac)
3. **Conectar o Claude Desktop** ao servidor
4. **Conversar com o Claude** e ver ele usando suas regras/allowlist/DLP

## Passo 1: Criar Seu Cliente com Dados Reais

Suponha que você tem uma empresa "acme" com:
- Uma skill de payment: `processar_pagamento` → `https://pagamento.acme.com/api`
- Uma regra: nega pagamentos > R$ 50.000
- Um allowlist: só `pagamento.acme.com` é permitido
- DLP: bloqueia CPF e CNPJ

Crie a pasta:

```bash
mkdir -p ~/brm-ai-framework-acme/clients/acme/config
cd ~/brm-ai-framework-acme
```

(ou use `/Users/seu_user/...` se preferir outro lugar)

## Passo 2: Preencha os Três Arquivos de Config

### `clients/acme/config/rules.yaml`

```yaml
schema_version: 1
client_id: acme

rules:
  - id: max-pagamento
    description: Nega pagamentos acima de R$ 50.000
    condition:
      ">":
        - var: valor
        - 50000
    effect: deny
    message: Valor do pagamento acima do limite (máx R$ 50.000)

  - id: requer-revisor
    description: Pagamentos para fornecedores externos requerem aprovação
    condition:
      "==":
        - var: tipo_fornecedor
        - externo
    effect: require_approval
    message: Pagamentos a fornecedores externos requerem aprovação de supervisor

  - id: ativo-expediente
    description: Processamento só durante horário comercial
    condition:
      and:
        - ">=":
            - var: hora
            - 9
        - "<":
            - var: hora
            - 18
    effect: allow
```

### `clients/acme/config/skills.yaml`

```yaml
schema_version: 1
client_id: acme

skills:
  - name: processar_pagamento
    description: Processa pagamento via plataforma ACM e
    method: POST
    url_template: https://pagamento.acme.com/api/pagar
    allowed_domains:
      - pagamento.acme.com
    parameters:
      valor:
        type: number
        required: true
      tipo_fornecedor:
        type: string
      documento_fornecedor:
        type: string
    timeout_seconds: 30.0

  - name: consultar_saldo
    description: Consulta saldo da conta
    method: GET
    url_template: https://pagamento.acme.com/api/saldo
    allowed_domains:
      - pagamento.acme.com
    timeout_seconds: 10.0
```

### `clients/acme/config/client_config.yaml`

Aqui é onde você coloca **seus dados reais**:

```yaml
client_id: acme
block_private_ip: true

domain_allowlist:
  - pagamento.acme.com
  - sistema-interno.acme.com
  # adicione mais domínios que suas skills usam

dlp_patterns:
  - pattern: '\d{3}\.\d{3}\.\d{3}-\d{2}'
    label: CPF
    action: deny

  - pattern: '\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}'
    label: CNPJ
    action: deny

  - pattern: '[Ss][Ee][Nn][Hh][Aa]:\s*\d+'
    label: Senha
    action: deny

  - pattern: '[Tt]oken[:\s]*[A-Za-z0-9_-]{32,}'
    label: Token API
    action: deny

  - pattern: 'cc:\s*\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}'
    label: Cartão de crédito
    action: deny
```

## Passo 3: Subir o Servidor MCP (vai rodar em background)

Abra um **novo terminal** (deixe ele aberto enquanto testa):

```bash
cd /Users/seu_user/brm-ai-framework

# Configure o cliente que quer usar
export CLIENT_ID=acme
export CONFIG_ROOT=$(pwd)/clients
export DATA_ROOT=$(pwd)/data

# Suba o servidor (vai ficar esperando JSON-RPC no stdin)
uv run python -m mcp_engine.core.mcp_server
```

Você vai ver:

```
Processing request of type InitializeRequest
# ... fica aqui esperando
```

**Deixe esse terminal aberto.** Não faça Ctrl+C — o servidor está rodando e escutando.

## Passo 4: Conectar o Claude Desktop ao Servidor

Abra o arquivo de configuração do Claude Desktop:

```bash
open ~/.claude/claude_desktop_config.json
```

Se o arquivo não existir, crie com este conteúdo (ajuste o caminho do seu usuário):

```json
{
  "mcpServers": {
    "brm-engine-acme": {
      "command": "uv",
      "args": [
        "run",
        "--cwd",
        "/Users/seu_user/brm-ai-framework",
        "python",
        "-m",
        "mcp_engine.core.mcp_server"
      ],
      "env": {
        "CLIENT_ID": "acme",
        "CONFIG_ROOT": "clients",
        "DATA_ROOT": "data"
      }
    }
  }
}
```

**Importante:**
- Substitua `/Users/seu_user/` pelo seu caminho real (ex: `/Users/joao/`)
- Deixe `"brm-engine-acme"` como o nome — vai aparecer na UI do Claude
- Os `env` precisam bater com os arquivos que você criou

Salve o arquivo (`Cmd+S`).

## Passo 5: Reiniciar o Claude Desktop

Feche completamente o Claude Desktop (Cmd+Q).

Abra de novo. Espere ele carregar uns 2-3 segundos.

Procure por um ícone de **engrenagem/ferramentas** no canto inferior direito. Clique. Você vai ver:

```
🔧 brm-engine-acme
  📋 call_skill
  📋 query_audit_log
```

Se não aparecer, verifique:
- Claude Desktop foi reiniciado? (não só minimizado)
- O arquivo `~/.claude/claude_desktop_config.json` está válido JSON? (use um JSON validator online)
- Os caminhos estão corretos? (copy-paste do `pwd`)

## Passo 6: Testar no Claude

Agora você conversa normalmente com o Claude, e ele consegue chamar suas ferramentas.

### Teste 1: Pagamento permitido

```
Processe um pagamento de R$ 10.000 para um fornecedor interno usando 
a skill processar_pagamento. Use valor=10000, tipo_fornecedor=interno.
```

**Esperado:** Claude vai chamar a tool, retornar `allowed: true` e o resultado simulado.

### Teste 2: Pagamento acima do limite

```
Processe um pagamento de R$ 100.000 para um fornecedor interno.
```

**Esperado:** Claude invoca a tool, recebe `allowed: false, reason: "Valor do pagamento acima do limite..."` — e explica para você que foi bloqueado pela regra.

### Teste 3: Pagamento para fornecedor externo

```
Processe um pagamento de R$ 20.000 para um fornecedor EXTERNO.
```

**Esperado:** Claude invoca a tool, recebe `allowed: false, reason: "human approval required"` e fica esperando por você (humano).

### Teste 4: CPF nos dados (DLP)

```
Processe um pagamento de R$ 5.000. O CPF do fornecedor é 123.456.789-00.
```

**Esperado:** Claude invoca a tool, recebe `allowed: false, reason: "DLP: found CPF"` — seus padrões sensíveis são detectados mesmo dentro dos argumentos.

### Teste 5: Auditoria

```
Quais pagamentos foram negados? Use query_audit_log com decision=denied.
```

**Esperado:** Claude invoca `query_audit_log`, retorna lista de eventos que foram bloqueados. Todos os testes anteriores aparecem aqui.

## Passo 7: Verificar Auditoria no Arquivo

Abra outro terminal e veja os eventos registrados:

```bash
tail -10 data/acme/audit.jsonl | jq .
```

(se não tiver `jq`, simplesmente `cat data/acme/audit.jsonl`)

Você vai ver JSON como:

```json
{
  "action": "processar_pagamento",
  "actor": "claude",
  "decision": "denied",
  "reason": "Valor do pagamento acima do limite",
  "rule_id": "max-pagamento",
  "occurred_at": "2026-08-26T...",
  ...
}
```

**Cada call_skill gera um evento** — você tem trilha auditável completa de quem fez quê, quando, e se foi permitido ou não.

## Resumo: Como Funciona

```
┌─────────────┐
│   Claude    │ (no seu navegador ou app Desktop)
│  Desktop    │
└──────┬──────┘
       │ JSON-RPC (stdio)
       ↓
┌─────────────────────────────────────┐
│  BRM Engine MCP (seu_mac:terminal)  │
│  - carrega config/acme/...          │
│  - valida regras (JSONLogic)        │
│  - faz DLP scanning                 │
│  - registra auditoria               │
└──────┬────────────────────────────┬─┘
       │                            │
       ↓                            ↓
    rules.yaml              domain_allowlist
    skills.yaml             dlp_patterns
    client_config.yaml      → audit.jsonl
```

## Troubleshooting

### "Tool call failed" ou "Connection refused"

- [ ] Servidor ainda está rodando no terminal? (`uv run python -m mcp_engine.core.mcp_server`)
- [ ] `CLIENT_ID` bate com a pasta? (`ls clients/acme/config/`)
- [ ] `CONFIG_ROOT` está certo? (`pwd` no terminal do servidor vs. `claude_desktop_config.json`)

### Servidor sobe mas Claude não vê as tools

- [ ] Reiniciou o Claude Desktop completamente? (Cmd+Q, depois abra de novo)
- [ ] Esperou 2-3 segundos após abrir? (leva um tempo para conectar)
- [ ] JSON do config está válido? (procure colchetes/vírgulas soltas)

### Pagamento foi bloqueado, mas no audit não aparece

- [ ] `DATA_ROOT` existe? (`ls data/acme/`) 
- [ ] Arquivo tem permissão? (`ls -la data/acme/audit.jsonl`)
- [ ] Servidor ainda está rodando? (a gravação acontece no servidor, não no Claude)

## Próximo: Testar em Produção (Fase 2)

Quando a Fase 2 estiver pronta:

1. **HTTP real:** em vez de devolver `{"status": "ok"}`, realmente chama sua API
2. **Control Plane:** config vem de um servidor centralizado, não de arquivos locais
3. **Approval workflows:** quando `require_approval`, integra com sistema seu de aprovações
4. **VaultProvider:** busca vetorial de contexto — "achei um pagamento suspeito, deixa eu verificar no conhecimento da empresa"

Por agora, use a Fase 1 para **design das regras e DLP** — o mocking é perfeito para isso.
