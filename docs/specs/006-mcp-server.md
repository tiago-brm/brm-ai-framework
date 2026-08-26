# 006 — MCP Server Entrypoint (spec)

**Ordem da spec (docs/spec.md §4, "mcp_server.py — Entrypoint FastMCP"):** Orquestra todos os componentes construídos até aqui (config_loader, rule_evaluator, interceptor, audit) em um servidor MCP que o LLM chama para executar skills sob controle de regras.

## Escopo deste passo

Implementar `mcp_engine/core/mcp_server.py` com:

- `BRMEngine` — classe que carrega config de um cliente e expõe dois **tools MCP**:
  1. `call_skill(skill_name, arguments)` → valida com RuleSet, passa por interceptor (allowlist SSRF + DLP), executa skill, registra em audit
  2. `query_audit_log(filters?)` → itera audit, filtra por período/ação/decision, retorna eventos

- Dependência nova: `mcp>=0.8.0` (Anthropic's MCP SDK)

## Detalhes de implementação

### `BRMEngine`

```python
class BRMEngine:
    def __init__(
        self,
        client_id: str,
        config_root: Path,
        data_root: Path,
        vault_provider: VaultProvider | None = None,
    ) -> None:
        self.client_id = client_id
        self._config_root = Path(config_root)
        self._data_root = Path(data_root)
        
        # Load config na inicialização (recarrega a cada nova instância)
        self._rule_provider = FileSystemRuleProvider(config_root)
        self._skill_provider = FileSystemSkillProvider(config_root)
        self._audit_sink = FileSystemAuditSink(data_root)
        self._vault_provider = vault_provider
        
        # Eagerly load to catch config errors early
        self._ruleset = self._rule_provider.get_rules(client_id)
        self._skillset = self._skill_provider.get_skills(client_id)
```

### Tool 1: `call_skill`

Entrada:
- `skill_name: str` — nome da skill a executar (ex.: `send_email`)
- `arguments: dict[str, Any]` — parâmetros (ex.: `{"destinatario": "..."}`)

Fluxo:
1. Procura a skill no `_skillset` por nome; se não encontra → erro 404
2. Constrói `facts` com contexto da requisição:
   ```python
   facts = {
       "skill_name": skill_name,
       "actor": "claude",  # ou quem fez a chamada, depois
       "origem_sistema": "mcp",
       **arguments,
   }
   ```
3. `evaluate(_ruleset, facts)` → `RuleDecision`
   - Se `effect == DENY` → retorna `{"allowed": False, "reason": decision.message}`
   - Se `effect == REQUIRE_APPROVAL` → retorna `{"allowed": False, "reason": "human approval required", "rule_id": decision.rule_id}`
   - Se `effect == ALLOW` → segue
4. Monta `ClientConfig` com allowlist/DLP/ip_block do cliente (onde vem? ver abaixo)
5. Monta `SkillCall` (url montada com argumentos, method, etc.)
6. `intercept(skill_call, client_config, audit_sink)` → `InterceptDecision`
   - Se `allowed == False` → registra em audit e retorna erro
   - Se `allowed == True` → segue
7. **Executa o HTTP request** (ou simula; ver Decisões)
8. Registra evento de sucesso em audit
9. **Retorna resultado ao LLM**

Saída:
```python
{
    "allowed": bool,
    "reason": str | None,
    "result": str,  # response body se sucesso
    "payload_digest": str | None,  # hash da resposta (pra audit)
}
```

### Tool 2: `query_audit_log`

Entrada (todos opcionais):
- `start_date: str | None` — ISO 8601 (ex.: "2024-08-26T00:00:00Z")
- `end_date: str | None`
- `action: str | None` — filtro por `AuditEvent.action`
- `decision: str | None` — "allowed" | "denied" | "pending_approval"

Fluxo:
1. `iter_events(client_id)` do audit sink
2. Filtra por data/action/decision se fornecido
3. Retorna lista (com limite? ver Decisões)

Saída:
```python
{
    "total": int,
    "events": [
        {
            "occurred_at": str,
            "actor": str,
            "action": str,
            "decision": str,
            "rule_id": str | None,
        },
        ...
    ],
}
```

### Como integrar com FastMCP

A classe `BRMEngine` não é diretamente um servidor FastMCP — é um orquestrador. O `mcp_server.py` cria uma instância de `FastMCP`, registra os dois tools acima, e a inicia.

```python
from mcp.server.fastmcp import FastMCP

engine = BRMEngine(
    client_id=os.getenv("CLIENT_ID", "example"),
    config_root=Path(os.getenv("CONFIG_ROOT", "clients")),
    data_root=Path(os.getenv("DATA_ROOT", "data")),
)

server = FastMCP("brm-engine")

@server.tool()
def call_skill(skill_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return engine.call_skill(skill_name, arguments)

@server.tool()
def query_audit_log(
    start_date: str | None = None,
    end_date: str | None = None,
    action: str | None = None,
    decision: str | None = None,
) -> dict[str, Any]:
    return engine.query_audit_log(
        start_date=start_date,
        end_date=end_date,
        action=action,
        decision=decision,
    )

if __name__ == "__main__":
    server.run()
```

## Decisões

- **HTTP request é mockado/documentado como "TODO pra Fase 2".** A spec 003 do interceptor já valida URL contra allowlist; executar HTTP real exigiria `httpx` ou `requests` + tratamento de timeout/retry/TLS — é um passo separado. Para Fase 1, `call_skill` simula sucesso ou retorna erro de allowlist/DLP. Teste-fácil é importante nesta fase.

- **`ClientConfig` hardcoded no engine.** Hoje (Fase 1) o cliente tem uma config estática (allowlist, DLP patterns, block_private_ip). Vem de `clients/<cliente>/config/client_config.yaml` (novo arquivo a inventariar — apenas domain allowlist + DLP patterns + IP block flag, sem dependência de rule_evaluator). Ler quando engine inicializa.

- **Skills são só "definição" até Fase 2.** Hoje eles definem `url_template`, `method`, `parameters`, `timeout` — dados. Não há construtor de regras que gere os skills dinâmicamente (aquela seria a "REST skill builder" genérica). Fase 2.

- **Limite de eventos retornado do audit?** Por ora, retorna tudo (pode ficar grande). Se cliente tiver milhões de eventos, reavaliar com paginação.

- **Sem validação de JWT/mTLS nesta fase.** O servidor fala JSON-RPC direto com o LLM via MCP; o LLM é trusted (é Claude rodando o cliente BRM). Autenticação entre componentes (engine ↔ control plane) entra na Fase 2.

## Fora de escopo (fica para depois)

- Executar HTTP real (skill invocation end-to-end) — Fase 2
- Control Plane (ler config de API centralizada em vez de arquivos) — Fase 2
- RPA workers (executar skills complexas via Lambda/Wasm) — Fase 3
- VaultProvider integration (búsca vetorial pra enriquecer contexto do LLM) — Fase 2

## Critério de pronto

- `uv add mcp>=0.8.0` limpo
- `BRMEngine` implementado com dois tools (`call_skill`, `query_audit_log`)
- Mock HTTP (simula sucesso em URL permitida, nega em allowlist)
- `tests/test_mcp_server.py` com casos: skill permitida (simula), skill negada por regra, skill negada por allowlist, DLP bloqueou, audit query filtra por data/ação
- `mcp_engine/core/mcp_server.py` com função `main()` que dispara `FastMCP`
- `clients/example/config/client_config.yaml` com allowlist + DLP patterns de exemplo
- `uv run ruff check .` limpo
- `uv run pytest -q` com novos testes passando
