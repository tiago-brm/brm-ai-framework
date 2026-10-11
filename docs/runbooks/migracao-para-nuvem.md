# Runbook: migração para a nuvem

As mesmas imagens sobem na nuvem. O que muda:

| Local | Nuvem |
|---|---|
| Volume `brm_data` | Disco gerenciado (bloco, não NFS/SMB) montado em `/data` |
| `.env` / `secrets/` | Gerenciador de segredos do provedor, injetado como variável de ambiente |
| Portas em 127.0.0.1 | Balanceador com TLS (ou perfil `remoto` com Caddy, `deploy/Caddyfile`) |
| Backup em disco local | Armazenamento de objetos cifrado (o arquivo já sai cifrado com `age`) |

SQLite continua válido para um cliente por instância.

## Checklist de prontidão (todos marcados antes de migrar)
- [ ] Suíte de testes verde (`.venv/bin/python -m pytest -q`)
- [ ] E2E por HTTP/SSE passando contra a imagem
- [ ] Restauração testada (`scripts/testar_restauracao.sh`) e recente
- [ ] Avaliação de intenção acima do limite (spec 029)
- [ ] Decisão de criptografia em repouso registrada (disco cifrado do provedor; SQLCipher só se exigido)
- [ ] Termos de privacidade do plano do LLM revisados
- [ ] Um mês de piloto reconciliado
- [ ] `docker compose` rodado do zero em máquina limpa, com restore, sem internet para o engine
- [ ] Metas RPO 24 h / RTO 1 h revistas
- [ ] Imagens versionadas (tag `BRM_VERSION`), varredura de vulnerabilidades e `docker history` sem segredos

## Passos
1. Publique as imagens em registro privado com tag de versão.
2. Provisione disco gerenciado e secret store; crie o `.env` equivalente.
3. Restaure o último backup no disco novo (ver `backup-e-restauracao.md`).
4. Suba `engine` e `dashboard` atrás do TLS; confira `/health`.
5. Ajuste `cors_origins` para o domínio final; DNS; rotacione todos os tokens (ver `rotacao-de-segredo.md`).
6. Agende o backup e o teste de restauração na nuvem.
