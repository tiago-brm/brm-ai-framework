# Runbook: backup e restauração

Metas do piloto: RPO 24 h, RTO 1 h.

## Chaves (uma vez)
1. Em máquina de confiança, fora do servidor: `age-keygen -o brm-backup.key`.
2. A linha `# public key: age1...` vai para `BRM_BACKUP_AGE_RECIPIENT` no `.env`.
3. A chave privada (`brm-backup.key`) fica em cofre/gerenciador de senhas e em segundo local. Sem ela, o backup é irrecuperável. Nunca fica no servidor nem no repositório.

## Backup
- Manual: `docker compose --profile backup run --rm backup`
- Agendado (cron do host, diário): `0 2 * * * cd /caminho/brm && docker compose --profile backup run --rm backup`
- O script copia cada SQLite com `sqlite3 .backup` (consistente com o engine em execução), confere `PRAGMA integrity_check`, copia `audit.jsonl` e o vault, e cifra tudo com `age`. Sem `BRM_BACKUP_AGE_RECIPIENT` ele se recusa a rodar.
- Saída em `backups/diarios` (14 mais recentes) e `backups/mensais` (6 mais recentes). Segundo disco: defina `BACKUP_SEGUNDO_DESTINO` com a pasta do outro disco.

## Teste de restauração (rodar após cada mudança relevante e ao menos semanalmente)
`AGE_IDENTITY=/caminho/brm-backup.key scripts/testar_restauracao.sh [arquivo.age]`
Restaura num diretório descartável, verifica `integrity_check` de cada banco e a cadeia de hash da auditoria, e registra `backups/ULTIMO_TESTE_RESTAURACAO`. Requer `age` e Python do projeto no host (`PYTHON=.venv/bin/python`).

## Restauração real
1. `docker compose down`
2. Restaure num diretório vazio: `AGE_IDENTITY=... scripts/restore.sh backups/diarios/<arquivo>.age /tmp/restaurado`
3. Rode `.venv/bin/python scripts/verificar_restauracao.py /tmp/restaurado <cliente>`.
4. O volume real se chama `brm_brm_data` (prefixo do projeto Compose). Preserve o atual (não apague) e crie um novo: `docker volume create brm_data_novo`, copie `dados/*` para ele com um contêiner temporário (`docker run --rm -v brm_data_novo:/data -v /tmp/restaurado/<cliente>/dados:/origem:ro alpine sh -c 'mkdir -p /data/<cliente> && cp -a /origem/. /data/<cliente>/ && chown -R 10001:10001 /data'`), ajuste o volume no compose ou renomeie, e copie `vault/` para `clients/<cliente>/vault`.
5. `docker compose up -d` e confira `/health`.

## Disco cheio
`docker system df`; reduza retenção (`RETENCAO_DIARIOS`), mova backups antigos para o segundo disco. Nunca apague `audit.jsonl`.
