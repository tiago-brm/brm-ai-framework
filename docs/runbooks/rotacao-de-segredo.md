# Runbook: rotação de segredos

Tipos de segredo e onde vivem: tokens de usuário (hash em `users.db`), `SB_USER` do editor, chave do `age`, tokens de integração (ex.: `NOTION_TOKEN`) no `.env`.

## Token de usuário (suspeita de vazamento)
Use a ferramenta/rota de rotação de token (super_admin) ou `SQLUserStore.rotate_token(email)`. O token antigo deixa de valer na hora; entregue o novo por canal seguro. A rotação fica na auditoria.

## Credencial do editor (`SB_USER`)
Altere no `.env` e rode `docker compose --profile editor up -d --force-recreate silverbullet`.

## Token de integração
Gere novo no provedor, atualize `.env`, `docker compose up -d --force-recreate engine`, revogue o antigo no provedor.

## Chave do backup (age)
Gere par novo (`age-keygen`), troque `BRM_BACKUP_AGE_RECIPIENT`, faça um backup e teste a restauração com a chave nova. Mantenha a chave antiga enquanto existirem backups cifrados com ela (ou refaça os backups).

## Incidente de segurança
Rotacione tudo acima, faça backup imediato, confira a cadeia de auditoria (`scripts/verificar_restauracao.py` sobre o backup) e preserve os logs (`docker compose logs engine`).
Segredo na imagem: nada é copiado para a imagem (`.dockerignore` exclui `.env`, `secrets/`, `clients/` e `data/`); confira com `docker history --no-trunc brm-engine:dev` antes de publicar.
