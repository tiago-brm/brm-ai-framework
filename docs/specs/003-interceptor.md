# 003 — Interceptor (spec)

**Ordem da spec (docs/spec.md §3, Hardening de Segurança, item 4):** construir os
3 controles inegociáveis no `interceptor` desde o dia 1. Allowlist de domínio
(bloqueia SSRF), DLP básico por regex/padrões conhecidos, e audit log
append-only com hash-chained. "Não adie 'depois eu boto segurança' — essa é a
camada que o Red Team mais explora."

## Escopo deste passo

Implementar `mcp_engine/core/interceptor.py` com função `intercept(skill_call, client_config, audit_sink) -> InterceptDecision` e modelos em `models.py`.

### Modelos novos em `models.py`

- `DLPPattern(_StrictModel)`:
  - `pattern: str` — regex compilável (ex: CPF `\d{3}\.\d{3}\.\d{3}-\d{2}`)
  - `label: str` — nome legível (ex: "CPF")
  - `action: Literal["mask", "deny"]` — mascarar ou bloquear

- `ClientConfig(_StrictModel)`:
  - `client_id: str`
  - `domain_allowlist: tuple[str, ...]` — FQDN/wildcard (ex: `("api.example.com", "*.partner.co")`)
  - `block_private_ip: bool = True` — bloqueia 127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, ::1
  - `dlp_patterns: tuple[DLPPattern, ...] = ()` — patterns a aplicar em resp

- `InterceptDecision(_StrictModel)`:
  - `allowed: bool`
  - `reason: str | None = None` — por que foi bloqueado (ex: "domain not in allowlist: api.evil.com")
  - `dlp_matches: dict[str, list[str]] = {}` — padrões encontrados na resposta, ex: `{"CPF": ["123.456.789-00"]}`

### Função `intercept()`

```python
def intercept(
    skill_call: SkillCall,  # novo model em models.py
    client_config: ClientConfig,
    audit_sink: AuditSink,
) -> InterceptDecision:
    """
    Intercepta execução de skill e aplica 3 controles:
    1. Domain allowlist + IP privado
    2. DLP na resposta
    3. Registra em audit log (append-only, hash-chained)
    """
```

**Controle 1 — Domain allowlist + SSRF**:
- Parseia `skill_call.url` → extrai FQDN
- Testa contra `client_config.domain_allowlist` (fnmatch, case-insensitive)
- Se bloqueado: retorna `InterceptDecision(allowed=False, reason="domain not in allowlist: ...")`
- Se `block_private_ip`: testa se IP é privado (usar `ipaddress.ip_address()`)

**Controle 2 — DLP básico**:
- Compila cada pattern em `client_config.dlp_patterns` (ou usa cache se já compilado)
- Busca matches em `skill_call.response_text` (resposta é string; assinar como `response_text`)
- Se action é "deny" e há match: `InterceptDecision(allowed=False, reason="DLP: found [label]")`
- Se action é "mask": guarda os matches em `dlp_matches` mas NÃO bloqueia (apenas reporta)
- Retorna decision com matches encontrados

**Controle 3 — Audit log**:
- Cria `AuditEvent(client_id, occurred_at, actor, action, decision, rule_id, payload_digest, details)`
- `payload_digest` = SHA256 da resposta (ou None se permitido sem DLP)
- `details["domain"]` = FQDN testado
- `details["dlp_matches"]` = dict de padrões encontrados
- Chama `audit_sink.record(event)`

### Novo model `SkillCall`

Em `models.py`:

```python
class SkillCall(_StrictModel):
    skill_name: str
    url: str
    method: HttpMethod
    actor: str = "unknown"  # quem disparou (LLM name, user ID, MCP client ID)
    request_body: dict[str, Any] = Field(default_factory=dict)
    response_text: str = ""
    response_status: int | None = None
```

## Decisões

- **Allowlist por wildcard (`fnmatch`), case-insensitive.** Simples de configurar,
  não requer regex ou parsing complexo. Clientes escrevem
  `("api.example.com", "*.partner.co", "backup.internal.example.com")` no YAML.

- **IP privado bloqueado por padrão (`block_private_ip: bool = True`).** SSRF mais
  perigoso é localhost/interno — 127.0.0.1, 192.168.x.x. Clientes com demanda
  legítima de chamar interno (ex: legacy system no mesma rede) desligam o flag
  mas sabem exatamente o que estão fazendo.

- **DLP com action "mask" vs "deny".** Alguns patterns (PII) devem bloquear (deny),
  outros (número de conta sanitizado) apenas registrar (mask). Fase 1 suporta
  ambas; Fase 2 evolui para NER (Presidio) que detecta contexto.

- **Hash do payload, não do inteiro request.** AuditEvent.payload_digest é SHA256
  de `response_text` — chave para hash-chain. O request em si não entra no audit
  log por enquanto (será necessário para forense, mas para MVP é noise).

- **`actor` do AuditEvent vem de `skill_call` — quem disparou a skill.** Será
  preenchido com nome do LLM, user ID, ou MCP client ID dependendo de quem chamou.

## Fora de escopo (fica para depois)

- Presidio / NER para DLP — Phase 2 quando volume de clientes justificar.
- Hash-chain com arquivo imutável (append-only, assinado) — Fase 2/3.
- Presença de `rest_builder.py` que força allowlist na criação de Skills REST — fica em skills.yaml config; builder é pós-MVP.
- mTLS / JWT rotativo — Phase 2.
- Integração com `Control Plane` — Phase 2 (hoje apenas yaml local).

## Critério de pronto

- `ClientConfig` com domain_allowlist, block_private_ip, dlp_patterns
- `intercept(skill_call, client_config, audit_sink) -> InterceptDecision` implementado
- Domain allowlist com fnmatch + IP privado bloqueado
- DLP regex com action mask/deny
- Audit log com SHA256 payload_digest
- `tests/test_interceptor.py` com casos: domain bloqueado, domain permitido,
  IP privado bloqueado, DLP deny/mask, audit log record chamado
- `uv run ruff check .` limpo
- `uv run pytest -q` com novos testes passando

