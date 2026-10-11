# 028 — Empacotamento Docker, backup e caminho para a nuvem

**O quê:** implementa a spec `docs/specs/028-empacotamento-docker-e-nuvem.md`.

- `Dockerfile` (multi-stage): `runtime` (Python 3.11 slim, uv, PyTorch CPU,
  modelo `paraphrase-multilingual-MiniLM-L12-v2` embutido no build, usuário
  `brm` uid 10001, `HF_HUB_OFFLINE=1`), `backup` (runtime + `sqlite3` e `age`)
  e `dashboard` (build Vite servido por nginx sem privilégios).
- `docker-compose.yml`: `engine` e `dashboard` (padrão), `silverbullet`
  (perfil `editor`, imagem `ghcr.io/silverbulletmd/silverbullet:2.12.0-slim`,
  credencial por `SB_USER`), `backup` (perfil `backup`), `proxy` Caddy (perfil
  `remoto`, `deploy/Caddyfile`, só esqueleto). Portas em `127.0.0.1`,
  `no-new-privileges`, `cap_drop: ALL`, raiz do engine somente leitura,
  configuração montada `:ro`, dados no volume nomeado (`brm_brm_data` no
  Compose, projeto `brm`).
- `mcp_engine/core/health.py` e gancho em `http_app.py`: `/health` roda
  `PRAGMA integrity_check` nos SQLite do cliente e responde 503 se algum
  falhar (sem detalhes além do nome do arquivo). Continua sem credencial.
- `BRM_DATA_DIR` em `user_store.py`: com a variável, `users.db` vai para
  `$BRM_DATA_DIR/<cliente>/users.db` e o arquivo legado em
  `config/users.db` é copiado uma única vez; sem a variável, nada muda.
  `SQLUserStore.db_path` expõe o caminho.
- `scripts/backup.sh` (sqlite3 `.backup`, `audit.jsonl`, vault, cifra `age`,
  retenção 14 diários e 6 mensais, segundo destino opcional; recusa rodar sem
  chave pública), `scripts/restore.sh` (nunca sobrescreve),
  `scripts/verificar_restauracao.py` (integridade dos bancos e cadeia de hash
  via `FileSystemAuditSink.iter_events`) e `scripts/testar_restauracao.sh`.
- `.env.example`, `.gitignore` (`.env`, `secrets/`, `backups/`), `.dockerignore`
  (nada de `.env`, `clients/`, `data/` na imagem).
- `docs/runbooks/`: subir local, backup e restauração, rotação de segredo,
  migração para a nuvem (checklist da spec).
- Testes: `tests/test_health_e_data_dir.py`, `tests/test_verificar_restauracao.py`.

## Verificado com Docker 28 (local, arm64)
Build das três imagens; engine saudável como uid 10001; escrita em `/app` e na
configuração recusada; `/health` 200; lista de 36 ferramentas por SSE com token
válido e 401 sem token; chamada de skill e `query_audit_log`; `kill -9` e
reinício saudável; imagem carrega o modelo com `--network none`; backup pelo
serviço `backup`, restauração em contêiner descartável e no host com cadeia de
auditoria e bancos íntegros; SilverBullet 2.12.0 sobe, `/.fs` responde 401 sem
credencial; `caddy validate` do Caddyfile; `docker compose config` com todos os
perfis; `docker compose down`.

## Pendências e desvios
- Proxy Caddy só validado em sintaxe (sem domínio/TLS real).
- Painel: a URL da API é escolhida no login (`localStorage`); para o painel em
  `127.0.0.1:8080` o `client_config.yaml` precisa listar essa origem em
  `cors_origins`. Origem única pelo proxy ainda não montada.
- `SB_USER` é a opção legada de credencial do SilverBullet (única disponível
  para modo single-space); o editor precisa de `SETUID/SETGID/CHOWN` e grava
  `.silverbullet.auth.json` no vault (ignorado no git e fora do backup).
- Bind mount do vault em Linux exige dono compatível com o uid 10001.
- Pacote do PyTorch CPU é fixado fora do `uv.lock` (versão do lock, índice CPU).
- Não feitos: `/status` com último backup/teste (God Mode), logs estruturados,
  teste de caos no meio de escrita, SQLCipher, "build em máquina limpa", CI de
  varredura de segredo, segundo disco real, agendamento do backup (cron do host
  documentado no runbook).
