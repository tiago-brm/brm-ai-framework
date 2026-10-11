# Runbook: subir o BRM localmente com Docker

Pré-requisitos: Docker 28+ com Compose, pasta `clients/<cliente>/config` e (para o editor) `clients/<cliente>/vault`.

1. `cp .env.example .env` e ajuste `CLIENT_ID`. Nunca versione o `.env`.
2. `mkdir -p clients/$CLIENT_ID/vault` (se a pasta não existir, o Docker a cria como root).
3. `docker compose build` (a primeira vez baixa o modelo de embeddings; depois a imagem roda sem internet).
4. `docker compose up -d` sobe `engine` (127.0.0.1:8000) e `dashboard` (127.0.0.1:8080).
5. Confira: `curl -s http://127.0.0.1:8000/health` deve devolver `{"status":"ok"}`. Se devolver 503, algum SQLite falhou no `PRAGMA integrity_check`: restaure o último backup (ver `backup-e-restauracao.md`).
6. Painel: abra http://127.0.0.1:8080 e informe a URL da API `http://127.0.0.1:8000`. Inclua `http://127.0.0.1:8080` e `http://localhost:8080` em `cors_origins` no `client_config.yaml` do cliente.
7. Editor do vault (opcional): defina `SB_USER=usuario:senha` no `.env` e rode `docker compose --profile editor up -d silverbullet`; acesso em http://127.0.0.1:3000. A credencial do editor é só para gestor/super_admin (o editor não passa pelo RBAC do MCP).

Primeiro usuário: `users.db` fica no volume `brm_data` (`/data/<cliente>/users.db`). Se já existir um `clients/<id>/config/users.db`, ele é copiado uma única vez para o volume na primeira subida. Para um cliente novo, crie o super_admin com:
`docker compose run --rm --no-deps engine python -c "from pathlib import Path; from mcp_engine.core.auth import Role; from mcp_engine.core.user_store import SQLUserStore; import os; print(SQLUserStore(Path('/app/clients'), os.environ['CLIENT_ID']).create_user('voce@dominio.com', Role.SUPER_ADMIN)[1])"` (o token impresso aparece uma única vez).

Parar: `docker compose down` (os dados ficam no volume `brm_data`; `down -v` APAGA os dados, nunca use sem backup).

Regras de segurança: portas só em 127.0.0.1; SQLite só em volume nomeado, nunca em pasta sincronizada (iCloud, Dropbox) nem NFS/SMB; configuração montada somente leitura.
