# 005 — Audit Sink com Hash-Chain (spec)

**Ordem da spec (docs/spec.md §3, "Falta de trilha auditável imutável"):** "Log de auditoria append-only, idealmente hash-chained, exportável". Os models (`AuditEvent`) e o `Protocol` (`AuditSink`) já existem desde o passo 001; este passo entrega a primeira implementação concreta de `FileSystemAuditSink` que persiste eventos em disco com encadeamento de hash.

## Escopo deste passo

Implementar `audit/sink.py` com:

- `FileSystemAuditSink(data_root: Path)` — `AuditSink` que persiste eventos em JSONL com hash-chaining
- Estrutura de arquivo: `<data_root>/<client_id>/audit.jsonl` (um evento por linha, JSON serializado)
- Cada evento inclui um `previous_hash: str | None` que referencia o SHA256 do evento anterior
- Primeiro evento do cliente tem `previous_hash: None`
- Linha 2+ tem `previous_hash` = SHA256(evento linha 1, em bytes UTF-8)
- Detecta corrupção: se alguém editar/apagar uma linha do meio, a cadeia quebra e `iter_events()` detecta no desserialize

## Detalhes de implementação

### Serialização

Cada linha é um JSON simples com estrutura `AuditEvent` + campo extra `previous_hash`:

```json
{"schema_version": 1, "client_id": "acme", "occurred_at": "2024-08-26T15:30:00Z", "actor": "claude", "action": "consultar_valor", "decision": "allowed", "rule_id": null, "payload_digest": "abc123...", "details": {}, "previous_hash": null}
{"schema_version": 1, "client_id": "acme", "occurred_at": "2024-08-26T15:31:00Z", "actor": "claude", "action": "enviar_email", "decision": "denied", "rule_id": null, "payload_digest": "def456...", "details": {}, "previous_hash": "sha256hash(linha1)"}
```

Evitar:
- Não serializar `previous_hash` DENTRO do modelo `AuditEvent` (ele não faz parte do contrato)
- Adicionar `previous_hash` apenas na hora de escrever em disco

### `FileSystemAuditSink`

```python
class FileSystemAuditSink:
    def __init__(self, data_root: Path) -> None:
        self._data_root = Path(data_root)
    
    def record(self, event: AuditEvent) -> None:
        # Monta caminho: data_root / client_id / "audit.jsonl"
        # Lê última linha (se existir) e extrai seu hash SHA256 (do JSON serializado, sem previous_hash)
        # Serializa event como JSON
        # Adiciona "previous_hash": <hash_da_linha_anterior_ou_null>
        # Append a linha ao arquivo (com newline)
    
    def iter_events(self, client_id: str) -> Iterable[AuditEvent]:
        # Lê arquivo linha por linha
        # Para cada linha:
        #   - Parse JSON
        #   - Extrai previous_hash
        #   - Calcula hash esperado (SHA256 da linha anterior, sem previous_hash)
        #   - Se previous_hash divergir do hash calculado → levanta AuditChainError
        #   - Remove previous_hash, reconstrói AuditEvent limpo, yield
```

### `AuditChainError`

```python
class AuditChainError(Exception):
    def __init__(self, client_id: str, line_number: int, expected_hash: str, found_hash: str | None) -> None:
        super().__init__(
            f"audit chain broken for {client_id!r} at line {line_number}: "
            f"expected previous_hash={expected_hash!r}, found {found_hash!r}"
        )
        self.client_id = client_id
        self.line_number = line_number
```

## Decisões

- **Hash-chain não previne, detecta corrupção.** Não é defesa contra ataque ativo (seria necessário criptografia assimétrica / Merkle tree + key material seguro). É apenas "se alguém mexer no arquivo, a cadeia quebra e a leitura avisa". Suficiente pra Fase 1: o evento liga um alarme de "alguém mexeu no audit log", o cliente investiga.

- **Hash é do evento SERIALIZADO (JSON bytes UTF-8), não do objeto Python.** Evita ambiguidade: se reconstrói o JSON do arquivo, tira `previous_hash`, hasheia — tem que bater. Garante que "arquivo é fonte de verdade", não "objeto Python deserializado é fonte".

- **Arquivo JSONL (JSON Lines), não um grande array JSON.** Cada evento é uma linha. Mais barato de append e simples de debugar: `tail audit.jsonl` funciona.

- **Sem cache em memória.** Cada chamada a `iter_events()` relê o arquivo do disco (mesmo racional de spec 004). Se o arquivo for grande, isso pode ficar lento — reavaliar quando profiling real mostrar gargalo (não aconteceu ainda).

- **Cria diretório de dados se não existir**, similar a spec 004 com `config_root`. Não quebra se `data_root` é novo.

- **`previous_hash` em `iter_events()` é calculado on-the-fly**, não armazenado no model. O `AuditEvent` que você recebe é sempre sem esse campo. Isso mantém o contrato limpo e permite que mude a estratégia de hash no futuro sem quebrar o deserialize.

## Fora de escopo (fica para depois)

- Assinatura criptográfica real (RSA, ECDSA) — seria Fase 2, quando demanda de "prova forense" vier de cliente real.
- Compressão/rotação de logs por período — quando arquivo ficar grande (milhões de linhas).
- Exportação em formato auditável (PDF, com qrcode) — quando cliente pedir.

## Critério de pronto

- `uv add` nenhuma dependência nova (usa `json`, `hashlib`, `pathlib`, tudo stdlib)
- `FileSystemAuditSink` implementado, `isinstance(..., AuditSink)` satisfaz o Protocol
- `tests/test_audit_sink.py` com casos: record válido, append sem quebra, iter_events reproduz, hash-chain válida, chain quebrada é detectada, cria diretório se não existir, múltiplos clientes não se misturam
- `uv run ruff check .` limpo
- `uv run pytest -q` com novos testes passando
