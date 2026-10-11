# 028 — Empacotamento Docker, backup e caminho para a nuvem (spec)

**Status:** implementada (2026-10-09), com pendências: ver `docs/changelog/028-empacotamento-docker-e-nuvem.md`.

**Origem:** decisão do usuário (2026-10-09): construir e testar no Docker
local e migrar para a nuvem quando estiver pronto. O repositório ainda não
tem `Dockerfile` nem `docker-compose`.

## Imagens e serviços

| Serviço | Conteúdo | Perfil |
|---|---|---|
| `engine` | BRM MCP + API (FastAPI), `CLIENT_ID` por ambiente, usuário não root | padrão |
| `dashboard` | build estático do painel servido por servidor web | padrão |
| `silverbullet` | editor do vault (spec 025) | `editor` |
| `backup` | cópia consistente e cifrada dos dados | `backup` |
| `proxy` | Caddy com TLS para acesso remoto (só quando houver acesso remoto) | `remoto` |

- Imagem do `engine` com `uv`, Python 3.11 e o **modelo de embeddings
  embutido no build**, para rodar sem internet.
- Healthcheck em `/health`. Portas ligadas a `127.0.0.1`.
- O dashboard e o engine compartilham origem pelo proxy para evitar CORS amplo.

## Volumes

| Volume | Conteúdo |
|---|---|
| `brm_data` (nomeado) | `ledger.sqlite`, `state.db`, `users.db`, `audit.jsonl`, índice do vault |
| vault (bind) | `clients/<id>/vault`, compartilhado com o editor |
| config (bind, somente leitura) | `clients/<id>/config` |
| secrets (bind, 0600) | `secrets.yaml` ou Docker secrets |

`users.db` sai da pasta de configuração para o volume de dados
(variável `BRM_DATA_DIR`), para a configuração poder ser somente leitura.
**SQLite fica em volume nomeado**, nunca em NFS/SMB nem em pasta sincronizada.

## Backup e restauração

- `scripts/backup.sh`: `sqlite3 .backup` para cada banco, cópia do
  `audit.jsonl` e do vault, empacotado e cifrado com `age`.
- Retenção: 14 diários e 6 mensais. Destino local e um segundo disco.
- `scripts/restore.sh` e **teste de restauração automatizado**: restaura em
  contêiner descartável, verifica a cadeia de hash da auditoria e os totais
  do ledger.

## Criptografia em repouso

Mínimo: disco do host cifrado (FileVault/LUKS). Opcional: SQLCipher para o
ledger. Decisão registrada antes da nuvem (checklist abaixo).

## Caminho para a nuvem

Mesmas imagens. Troca: volume por disco gerenciado, SecretStore por
gerenciador de segredos do provedor, proxy por balanceador com TLS, backup
para armazenamento de objetos cifrado. SQLite continua válido para um
cliente por instância; Postgres só entra com o control plane (Fase 2).

**Checklist de prontidão:** suíte verde, E2E por HTTP/SSE, restauração
testada, avaliação de intenção acima do limite (029), decisão de criptografia,
revisão dos termos de privacidade do plano do LLM, e um mês de piloto
reconciliado.

## Red Team / Blue Team / Postmortem

| Risco (Red Team) | Controle (Blue Team) |
|---|---|
| Contêiner como root; escape ou escrita na configuração | Usuário não root, configuração somente leitura, `no-new-privileges` |
| Segredo na imagem ou no repositório | `.env` e `secrets/` no `.gitignore`; verificação no CI |
| Porta do engine ou do editor exposta na rede | Bind em loopback; exposição só pelo proxy com TLS |
| Backup sem cifra ou nunca testado | `age` e restauração automatizada |
| Corrupção do SQLite por bind mount em sistema de arquivos de rede | Volume nomeado; healthcheck faz `PRAGMA integrity_check` |

**Postmortem antecipado.** "Funcionou no meu Mac" não prova a nuvem. Rodar o
compose do zero em máquina limpa e executar o restore fazem parte do
critério de pronto.

## Testes previstos

- `docker compose config` válido para cada perfil.
- Build reprodutível; imagem sobe sem acesso à internet.
- Smoke: `/health`, listagem de ferramentas e um lançamento ponta a ponta.
- Restore: dados e cadeia de auditoria íntegros após recuperação.

## Fora de escopo

Orquestração (Kubernetes), alta disponibilidade, multi-região.

## God Mode / L-99

**God Mode.** Observabilidade ponta a ponta: logs estruturados com
`trace_id`, endpoint `/status` com versão, integridade do ledger, cadeia de
auditoria, data do último backup e do último teste de restauração, exibido
no painel.

**L-99.**

- Metas: RPO de 24 h e RTO de 1 h para o piloto (revistas antes da nuvem).
- Teste de caos: encerrar o contêiner no meio de uma escrita e verificar
  integridade e cadeia na subida.
- Upgrades com reversão: migração de banco só avança após backup automático.
- Runbooks: restauração, rotação de segredo, disco cheio, incidente de
  segurança, migração para a nuvem.
