# 007 — Plano de Teste Manual (Fase 1 completa)

**Ordem da spec:** Não é uma spec de implementação — as specs 001-006 já entregaram e testaram (via `pytest`) `contracts`, `rule_evaluator`, `interceptor`, `config_loader`, `audit/sink` e `mcp_server`. Este documento é um roteiro de **validação manual/E2E** para confirmar, fora dos testes automatizados, que o conjunto funciona como sistema — inclusive falando protocolo MCP de verdade. Checklist autocontido: pode ser seguido em qualquer sessão nova, sem depender de contexto de conversa anterior.

**Status:** executado em 2026-08-26 — todos os cenários passaram. Resultados reais registrados ao final de cada seção.

## Objetivo

Confirmar que a Fase 1 do BRM Engine está pronta para uso: um cliente (`example`) consegue chamar skills via MCP, ter suas chamadas avaliadas por regras (JSONLogic), interceptadas por allowlist/DLP, e auditadas em log hash-chained — tudo sem execução HTTP real (mockada, por design da Fase 1).

## Pré-requisitos

```bash
cd /Users/tiagopereiraramos/brm-ai-framework
uv sync
ls clients/example/config/   # esperado: rules.yaml  skills.yaml  client_config.yaml
```

### Fixture do cliente `example` (o que realmente existe)

Escrever cenário de teste sem conferir isto gera teste que chama skill inexistente. Estado atual:

| skills.yaml | rules.yaml | client_config.yaml |
|---|---|---|
| `enviar_email` → mail.example.com | `max-valor-consulta` (deny, `valor_consultado > 100000`) | allowlist: mail / erp-internal / sistema-registros .example.com |
| `consultar_erp` → erp-internal.example.com | `envio-externo-requer-aprovacao` (require_approval, `tipo_acao == enviar_para_sistema_externo`) | DLP: CPF (deny), Account (mask) |
| `registrar_documento` → sistema-registros.example.com | `acesso-permitido-horario` (allow, 8h–18h) | `block_private_ip: true` |

Todos os três domínios de skill estão na allowlist — **não existe skill do fixture que dispare negação por allowlist**. Para testar esse caminho é preciso um cliente próprio (ver Cenário 5).

### Nota sobre `PYTHONPATH`

O projeto não é instalado como pacote e não há `conftest.py` na raiz. `pytest` funciona porque insere a rootdir no `sys.path`; um script avulso em `/tmp` **não** herda isso. Scripts fora do repo exigem:

```bash
PYTHONPATH=/Users/tiagopereiraramos/brm-ai-framework uv run python /tmp/script.py
```

### Nota sobre `DATA_ROOT`

`data/` não está no `.gitignore`. Use `DATA_ROOT=/tmp/brm_*_data` nos testes para não sujar o working tree com audit logs de validação.

## Cenário 1 — Sanity check (testes automatizados + lint)

```bash
uv run ruff check .
uv run pytest -q
```

**Esperado:** `All checks passed!` e todos os testes `passed`. Se falhar, pare — os cenários seguintes pressupõem base verde.

> **Resultado:** ✅ `All checks passed!` · `93 passed in 0.39s`

## Cenário 2 — Engine isolado (sem MCP, direto em Python)

Valida `BRMEngine` sem depender do transporte MCP.

```bash
cat > /tmp/e2e_test.py << 'PYEOF'
from pathlib import Path
from mcp_engine.core.mcp_server import BRMEngine

engine = BRMEngine(
    client_id="example",
    config_root=Path("clients"),
    data_root=Path("/tmp/brm_e2e_data"),
)

print("1) skill permitida")
r = engine.call_skill("enviar_email", {"destinatario": "a@b.com"})
assert r["allowed"] is True, r

print("2) negada por regra")
r = engine.call_skill("consultar_erp", {"valor_consultado": 200000})
assert r["allowed"] is False and r["rule_id"] == "max-valor-consulta", r

print("3) requer aprovação humana")
r = engine.call_skill("enviar_email", {"tipo_acao": "enviar_para_sistema_externo"})
assert r["allowed"] is False and "approval" in r["reason"], r

print("4) skill permitida (outro domínio da allowlist)")
r = engine.call_skill("registrar_documento", {"titulo": "doc1"})
assert r["allowed"] is True, r

print("5) bloqueada por DLP (CPF ecoado na resposta mock)")
r = engine.call_skill("consultar_erp", {"cpf": "123.456.789-00"})
assert r["allowed"] is False and "DLP" in r["reason"], r

print("6) skill inexistente")
r = engine.call_skill("skill_que_nao_existe", {})
assert r["allowed"] is False and "not found" in r["reason"], r

print("7/8/9) query_audit_log: sem filtro, decision=denied, action")
assert engine.query_audit_log()["total"] >= 5
assert all(e["decision"] == "denied" for e in engine.query_audit_log(decision="denied")["events"])
assert all(e["action"] == "consultar_erp" for e in engine.query_audit_log(action="consultar_erp")["events"])

print("\n✅ Cenário 2 passou")
PYEOF

rm -rf /tmp/brm_e2e_data
PYTHONPATH=/Users/tiagopereiraramos/brm-ai-framework uv run python /tmp/e2e_test.py
```

**Esperado:** todas as asserções passam e `✅ Cenário 2 passou` aparece.

> **Resultado:** ✅ 9/9 passos. 6 eventos auditados: 2 `allowed`, 3 `denied`, 1 `pending_approval` — confirma que cada `call_skill` gera exatamente um evento, inclusive nos caminhos que saem antes do interceptor.

## Cenário 3 — Integridade do audit log (hash-chain)

```bash
tail -3 /tmp/brm_e2e_data/example/audit.jsonl

PYTHONPATH=/Users/tiagopereiraramos/brm-ai-framework uv run python -c "
from audit.sink import FileSystemAuditSink
from pathlib import Path
events = list(FileSystemAuditSink(Path('/tmp/brm_e2e_data')).iter_events('example'))
print(f'{len(events)} eventos lidos sem quebra na cadeia')
"
```

Agora **prove que a detecção funciona** corrompendo uma linha do meio:

```bash
cp /tmp/brm_e2e_data/example/audit.jsonl /tmp/brm_e2e_data/example/audit.jsonl.bak
sed -i '' '3s/.*/{"corrupted": true}/' /tmp/brm_e2e_data/example/audit.jsonl

PYTHONPATH=/Users/tiagopereiraramos/brm-ai-framework uv run python -c "
from audit.sink import FileSystemAuditSink, AuditChainError
from pathlib import Path
try:
    list(FileSystemAuditSink(Path('/tmp/brm_e2e_data')).iter_events('example'))
    print('FALHA: corrupção não detectada')
except AuditChainError as e:
    print(f'OK, detectada: {e}')
"
mv /tmp/brm_e2e_data/example/audit.jsonl.bak /tmp/brm_e2e_data/example/audit.jsonl
```

**Esperado:** leitura limpa antes, `AuditChainError` depois. Um log que lê sem erro após edição manual seria bug grave.

> **Resultado:** ✅ 6 eventos íntegros. Após corromper a linha 3: `audit chain broken for 'example' at line 3: expected previous_hash='06bd2fd7...', found None`.

## Cenário 4 — Servidor MCP real (handshake + JSON-RPC via stdio)

Este é o teste forte: sobe o servidor FastMCP de verdade e conversa com ele pelo protocolo. Cobre o que o Cenário 2 não cobre — registro de tools, serialização do retorno, transporte stdio.

```bash
cat > /tmp/mcp_client_test.py << 'PYEOF'
import asyncio, json, os
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT = "/Users/tiagopereiraramos/brm-ai-framework"

async def main() -> None:
    params = StdioServerParameters(
        command="uv",
        args=["run", "python", "-m", "mcp_engine.core.mcp_server"],
        cwd=PROJECT,
        env={**os.environ, "CLIENT_ID": "example", "CONFIG_ROOT": "clients",
             "DATA_ROOT": "/tmp/brm_mcp_data", "PYTHONPATH": PROJECT},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"handshake OK — servidor: {init.serverInfo.name}")

            names = [t.name for t in (await session.list_tools()).tools]
            print(f"tools: {names}")
            assert "call_skill" in names and "query_audit_log" in names

            async def call(tool, args):
                r = await session.call_tool(tool, args)
                return json.loads(r.content[0].text)

            out = await call("call_skill", {"skill_name": "enviar_email",
                                            "arguments": {"destinatario": "a@b.com"}})
            assert out["allowed"] is True
            print(f"1) enviar_email          -> allowed={out['allowed']}")

            out = await call("call_skill", {"skill_name": "consultar_erp",
                                            "arguments": {"valor_consultado": 200000}})
            assert out["allowed"] is False
            print(f"2) valor acima do limite -> {out['reason']}")

            out = await call("call_skill", {"skill_name": "enviar_email",
                                            "arguments": {"tipo_acao": "enviar_para_sistema_externo"}})
            assert out["allowed"] is False
            print(f"3) envio externo         -> {out['reason']}")

            out = await call("call_skill", {"skill_name": "consultar_erp",
                                            "arguments": {"cpf": "123.456.789-00"}})
            assert out["allowed"] is False
            print(f"4) CPF na resposta (DLP) -> {out['reason']}")

            print(f"5) audit total           -> {(await call('query_audit_log', {}))['total']}")
            print(f"6) filtro denied         -> {(await call('query_audit_log', {'decision': 'denied'}))['total']}")
            print(f"7) filtro action         -> {(await call('query_audit_log', {'action': 'enviar_email'}))['total']}")

    print("\n✅ Servidor MCP validado ponta a ponta via JSON-RPC")

asyncio.run(main())
PYEOF

rm -rf /tmp/brm_mcp_data
uv run python /tmp/mcp_client_test.py
```

**Esperado:** handshake, dois tools listados, e os mesmos vereditos do Cenário 2 — agora atravessando o protocolo.

> **Resultado:** ✅ handshake com `brm-engine`, tools `['call_skill', 'query_audit_log']`, 4 chamadas com vereditos corretos, filtros de audit retornando 4 / 2 / 2. As linhas `Processing request of type CallToolRequest` no stderr são log normal do FastMCP.

## Cenário 5 — Negação por allowlist (exige cliente próprio)

O fixture `example` não cobre este caminho (todo skill aponta para domínio permitido). Crie um cliente de teste com skill fora da allowlist:

```bash
mkdir -p /tmp/brm_alt/clients/teste/config
cat > /tmp/brm_alt/clients/teste/config/rules.yaml << 'EOF'
schema_version: 1
client_id: teste
rules: []
EOF
cat > /tmp/brm_alt/clients/teste/config/skills.yaml << 'EOF'
schema_version: 1
client_id: teste
skills:
  - name: exfil
    description: Chama domínio não autorizado
    method: GET
    url_template: https://evil.com/exfil
    allowed_domains: [evil.com]
EOF
cat > /tmp/brm_alt/clients/teste/config/client_config.yaml << 'EOF'
client_id: teste
block_private_ip: true
domain_allowlist:
  - api.interno.teste.com
EOF

PYTHONPATH=/Users/tiagopereiraramos/brm-ai-framework uv run python -c "
from pathlib import Path
from mcp_engine.core.mcp_server import BRMEngine
e = BRMEngine('teste', Path('/tmp/brm_alt/clients'), Path('/tmp/brm_alt/data'))
r = e.call_skill('exfil', {})
print(r['allowed'], '|', r['reason'])
assert r['allowed'] is False and 'allowlist' in r['reason']
print('OK — allowlist bloqueou')
"
```

**Esperado:** `False | domain not in allowlist: evil.com`. Note que `allowed_domains` da skill **não** autoriza nada — quem manda é a allowlist do `client_config.yaml`. Isso é proposital: o cliente é a autoridade, não a definição da skill.

> **Resultado:** coberto por `tests/test_mcp_server.py::test_call_skill_denied_by_allowlist`; executar aqui quando quiser validação manual.

## Cenário 6 — Integração com Claude Desktop

`~/.claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "brm-engine": {
      "command": "uv",
      "args": ["run", "--cwd", "/Users/tiagopereiraramos/brm-ai-framework",
               "python", "-m", "mcp_engine.core.mcp_server"],
      "env": {
        "CLIENT_ID": "example",
        "CONFIG_ROOT": "clients",
        "DATA_ROOT": "data",
        "PYTHONPATH": "/Users/tiagopereiraramos/brm-ai-framework"
      }
    }
  }
}
```

Reinicie o Claude Desktop, confirme `brm-engine` no ícone de ferramentas, e peça:

```
Chame call_skill para 'enviar_email' com destinatario a@b.com.
Depois 'consultar_erp' com valor_consultado=200000.
Depois use query_audit_log para listar os eventos negados.
```

**Esperado:** os mesmos vereditos dos cenários anteriores, agora com o LLM escolhendo os argumentos.

> **Resultado:** pendente — requer GUI, não roda por terminal.

## Checklist de "Fase 1 validada"

- [x] Cenário 1 — `ruff check` e `pytest` verdes (93 testes)
- [x] Cenário 2 — 9/9 passos do engine isolado
- [x] Cenário 3 — hash-chain íntegra **e** corrupção detectada
- [x] Cenário 4 — servidor MCP real responde JSON-RPC
- [ ] Cenário 5 — negação por allowlist (coberto em pytest; manual pendente)
- [ ] Cenário 6 — Claude Desktop (requer GUI)

Cenários 1–4 confirmam a Fase 1 ponta a ponta por terminal. 5 e 6 são complementares.

## Limitações conhecidas (por design da Fase 1)

- **Nenhum HTTP real acontece.** O "resultado" é `{"status": "ok", "skill": ..., "arguments": ...}` — argumentos ecoados. É isso que torna o teste de DLP significativo (um CPF passado como argumento volta na resposta e é capturado), mas nenhum cenário prova integração com API externa. Isso é Fase 2.
- **`actor` é sempre `"claude"`,** hardcoded. Não há autenticação: quem fala com o servidor é considerado confiável.
- **Config é lida uma vez, na construção do engine.** Editar YAML com servidor no ar não tem efeito até reiniciar.

## Fora de escopo deste plano

- Teste de carga/concorrência — sem HTTP real e sem multi-tenant sob carga, não é preocupação da Fase 1.
- Teste adversarial de segurança (bypass de allowlist via IDN homográfico, encoding, redirect) — merece plano dedicado; `tests/test_interceptor.py` já cobre lookalike de sufixo e IP privado.
- Performance do audit log com milhões de eventos — spec 005 já registra como decisão consciente de não otimizar ainda.
