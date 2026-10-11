# Security Guidelines

> Segurança é requisito de produto aqui, não etapa final: o motor existe para que o modelo **não** decida o que é permitido. Controles básicos entram desde a Fase 1 (retrofit depois de um incidente é muito mais caro).
> Estado verificado em 2026-10-11. As lacunas da §10 foram **reproduzidas em código**, não presumidas.

## 1. Authentication

- **Provider:** próprio — `users.db` por cliente (ver [DATABASE.md](DATABASE.md)). Não há SSO/OAuth.
- **stdio (local):** identidade lida **uma vez** na subida: `MCP_USER_EMAIL`, senão `MCP_API_TOKEN`. Um processo = uma sessão = um usuário.
- **HTTP/SSE (remoto) e painel:** `Authorization: Bearer <token>`, resolvido **a cada request** pelo middleware.
- **Sem credencial, header malformado ou token desconhecido:** perfil `anonymous` com papel `viewer` — nunca `admin`. (`viewer` só enxerga tools sem restrição; as sensíveis têm linha no seed.)
- O painel pede URL do engine + token e guarda ambos no `localStorage` do navegador (L-10).

## 2. Authorization

**Papéis:** `viewer` < `admin` < `super_admin` (por cliente, não cross-client).

| Camada | Mecanismo | Onde |
|---|---|---|
| Tool | `tool_permissions` (editável em runtime pelo `super_admin`); `tools/list` filtrado + `tools/call` revalidado, negativa auditada | `RBACFastMCP` |
| Ação | Regras JSONLogic enxergam `actor`, `actor_role` e argumentos | `rules.yaml` |
| Fato de segurança | `derived_facts` calculados pelo motor; valor informado pelo modelo é descartado e auditado | `BRMEngine.call_skill` |
| Leitura de nota | `vault_access.yaml` (allow / metadata_only / deny) | `vault/access.py` |
| Escrita no vault | `write_roles` (padrão `admin`, `super_admin`) | `vault_add_note` |
| Painel/REST | `Role.SUPER_ADMIN` em **todas** as rotas `/api/*` | `api_router.py` |
| Atribuir `super_admin` | Só quem já é `super_admin` | `criar_usuario`, `atualizar_perfil_usuario` |

Princípios: **autorização é aplicada no servidor, nunca no prompt**; `tools/call` não confia no `tools/list`; defesa em profundidade (ex.: `deletar_banco` reconfere o perfil por dentro).

## 3. Secrets

- Nunca hardcode credenciais; nunca commite segredo de produção; nunca exponha segredo ao cliente (navegador/modelo).
- Use variáveis de ambiente (`.env` está no `.gitignore`) ou o armazenamento de segredos da plataforma.
- Credenciais de sistemas do cliente (portais, ERPs) ficam em cofre **por tenant**, nunca na base de configuração geral.
- Rotacione qualquer credencial que tenha vazado (prompt, log, screenshot, repositório).
- Não cole segredos em prompts para o agente de código.

## 4. Environment Variables

**Públicas (sem segredo):** `CLIENT_ID`, `CONFIG_ROOT`, `DATA_ROOT`, `BRM_FROM_EMAIL`, `BRM_FROM_NAME`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_SSL`, `SMTP_SENDER`. O painel não usa variáveis de build.

**Privadas (segredo):** `MCP_API_TOKEN`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `NOTION_TOKEN`, `RESEND_BRM_API_KEY`, `SMTP_USER`, `SMTP_PASS`. `MCP_USER_EMAIL` identifica, mas trate como dado pessoal.

## 5. API Security

- **Validação de entrada:** Pydantic em toda borda (REST e tools tipadas); config com `extra="forbid"`. Regras são dado, nunca código (`eval`/`exec` proibidos).
- **SSRF:** `domain_allowlist` obrigatória e não vazia por cliente (`fnmatch`, case-insensitive) + `block_private_ip=True`. Cada skill declara `allowed_domains`; o interceptor valida o destino em runtime, mesmo para skills promovidas de rascunho.
- **Rate limiting:** **não há** (L-06).
- **CORS:** `allow_origins=["*"]` (L-04) — aceitável apenas local-first.
- **Authentication/Authorization:** ver §1–2.
- **Respostas de erro:** tools devolvem `{allowed:false, reason}` em português claro; evitar stack trace e detalhe interno nas mensagens.

## 6. Data Protection

- **Dado sensível:** prontuários, hipóteses clínicas, CID, CPF, telefone, dados de alunos e processos.
- **Permanência:** o dado fica na fonte e na máquina do cliente. Embeddings são **locais**. Sync do vault para a nuvem da BRM está **fora de escopo** (mudaria o modelo de ameaça e exigiria DLP no caminho).
- **DLP no retorno de skill:** `deny` bloqueia; `mask` é redigido (`[REDACTED:<label>]`) para quem não é `admin`/`super_admin`.
- **DLP de saída para LLM** (`check_vault_egress`): **qualquer** padrão, `mask` incluso, bloqueia o envio de nota/instrução ao agente interpretador/gerador. Não existe "redige e continua" nesse caminho.
- **Criptografia em repouso:** não implementada (fora de escopo declarado).
- **Retenção/exclusão (LGPD):** não definida — decisão aberta em [PRD.md](PRD.md).

## 7. Logging

**Nunca logar:** senhas, tokens, chaves de API, dado pessoal sensível, conteúdo de nota.
**Logar (auditoria):** autenticação negada (RBAC), ações e decisões, regra aplicada, rascunhos gerados/promovidos, acessos privilegiados, divergência de fato, simulações, erros, eventos de segurança.

- A auditoria guarda `payload_digest` (SHA-256), contagens e nomes de política — não o conteúdo. 
- Hash-chain **detecta** adulteração; não a impede (L-09).
- O projeto não usa biblioteca de logging de aplicação; a trilha de auditoria é o log de negócio.

## 8. File Uploads

Não há upload via API. A superfície equivalente é a **escrita no vault**: `vault_add_note` (restrita a `write_roles`), edição direta do cliente no Obsidian e o gerador de documentos.

- **Tipos:** apenas `.md` entram no índice; cópias `*.md-brm-conflict-*` / `*.md-brm-unparseable-*` ficam fora.
- **Conflito:** se o arquivo mudou desde a última indexação, o engine **não** sobrescreve: guarda a versão dele ao lado e mantém a do cliente (auditado).
- **Notas não parseáveis:** salvas como cópia antes de qualquer reescrita.
- **Caminho:** `note_id` é caminho relativo ao `vault_root`; revisar contra *path traversal* antes de aceitar `note_id` externo em tool nova.
- **Limites de tamanho:** não definidos.

## 9. Security Checklist

Para toda mudança:

- [ ] Autenticação e autorização no servidor para qualquer tool/rota nova
- [ ] Tool nova tem linha em `tool_permissions` quando não deve ser aberta a `viewer`/`anonymous`
- [ ] Validação de entrada no servidor (Pydantic)
- [ ] Nenhum fato de segurança vem do modelo (usar `derived_facts`)
- [ ] Segredos fora do código, dos logs e dos prompts
- [ ] Dado sensível protegido (política de vault, DLP, sem conteúdo no log)
- [ ] Rate limiting considerado
- [ ] Escrita no vault/rascunhos auditada e sem sobrescrita silenciosa
- [ ] Erros não expõem detalhe sensível
- [ ] Dependências revisadas (`uv.lock`, `package-lock.json`)
- [ ] Nada gerado por LLM chega à produção sem promoção humana nomeada

## 10. Lacunas conhecidas

> Verificadas em código em 2026-10-11. Prioridade: **P0** antes de dado real; **P1** antes de exposição fora de localhost; **P2** dívida assumida. L-01, L-02 e L-05 (P0) foram resolvidas; não há P0 aberta.

| ID | P | Lacuna | Evidência / efeito | Mitigação proposta |
|---|---|---|---|---|
| L-01 | ✅ resolvida | Resolvida: seed por tool restringe promoção, geração, escrita no vault e auditoria a `admin`/`super_admin`; bancos existentes recebem as linhas ausentes sem sobrescrever escolhas do `super_admin`. Resta avaliar *deny-by-default* para tool sem linha. |  |  |
| L-02 | ✅ resolvida | Resolvida: `approved_by` = identidade logada (`reviewed_by` e auditoria). |  |  |
| L-03 | P1 | Token de acesso em **texto puro** em `users.db`; sem expiração. | `SELECT api_token FROM users` devolve o token. Decisão D-011 assumida como dívida. | Guardar hash (ex.: SHA-256 com pepper ou argon2), exibir o token uma única vez, adicionar expiração/rotação. |
| L-04 | P1 | CORS `*` e API REST autenticada só por Bearer. | `http_app.create_app`. | Restringir origens ao painel antes de expor fora de localhost. |
| L-05 | ✅ resolvida | Resolvida: a auditoria grava só rótulo → contagem (`{"CPF": 2}`), também nos eventos `*_egress_check`. O simulador ainda devolve os valores ao `super_admin` na resposta, sem persistir. |  |  |
| L-06 | P1 | Sem rate limiting/limite de tamanho em REST, tools ou escrita no vault; Notion sem backoff (~3 req/s). | — | Limite por token/IP no FastAPI; fila/backoff no `NotionNoteSource`. |
| L-07 | P2 | `notion_token` pode ficar em `client_config.yaml` (versionado). O exemplo usa `NOTION_TOKEN` no `.env`. | `NotionVaultProvider` aceita ambos. | Proibir o campo no YAML (ler só do ambiente). |
| L-08 | P1 | Efeito padrão das regras é `allow`: o motor é *deny-list*. Esquecer uma regra libera a ação. | `rule_evaluator.DEFAULT_EFFECT = ALLOW`. | Opção `default_effect: deny` por cliente (obrigatória em saúde). |
| L-09 | P2 | Hash-chain é *tamper-evident*, não *tamper-proof*; `record()` não revalida a cadeia e há um escritor por arquivo, sem trava entre processos. | `audit/sink.py`. | Assinatura/âncora externa (Fase 2); lock de arquivo. |
| L-10 | P2 | Token do painel no `localStorage`. | `dashboard/src/api.ts`. | Aceitável local-first; revisar com exposição pública (cookie `HttpOnly`/CSP). |
| L-11 | P2 | Tools de demonstração (`consultar_dados`, `deletar_banco`) registradas em todo servidor. | `build_server`. | Registrar só em ambiente demo. |
| L-12 | P2 | `consultar_processo_datajud` audita com ator fixo `claude`; `vault_*` de rascunho idem. | `DEFAULT_ACTOR`. | Usar a identidade logada (`_actor()`). |
| L-13 | P2 | `atualizar_permissao` aceita `tool_name` inexistente. | Sem validação contra o catálogo. | Validar contra as tools registradas. |
| L-14 | P1 | A matriz de permissões do painel mostra `negado` para tool **sem linha**, que na verdade está **aberta** a todos os perfis; o operador acredita ter bloqueado algo que está liberado. | `Permissoes.tsx`: `permMap.get(key) ?? false`. | Exibir "aberta (sem restrição)" para tool sem linhas e corrigir L-01. |

## 11. Threat model (Red Team → Blue Team)

| Risco | Controle | Fase | Status |
|---|---|---|---|
| SSRF via skill preenchida pelo LLM | Allowlist de domínio + bloqueio de IP privado | 1 | ✅ |
| Execução de código disfarçada de regra | JSONLogic puro; saída de LLM validada por Pydantic | 1 | ✅ |
| LLM informa fato de segurança favorável | `derived_facts` calculados no motor; divergência auditada | 1 | ✅ |
| Vazamento por leitura do vault | `vault_access.yaml`; `metadata_only`; auditoria sem conteúdo | 1 | ✅ |
| DLP regex contornável | Regex + `mask`/`deny`; plano de NER (Presidio) | 1→2 | 🟡 |
| Dado sensível vai para LLM externo | `check_vault_egress` estrito; embeddings locais | 1 | ✅ |
| Geração automática chega à produção | Rascunho + promoção por `admin`/`super_admin`, identidade auditada | 1 | ✅ |
| Falta de trilha imutável | Hash-chain append-only exportável | 1 | 🟡 detecta (L-09) |
| Credenciais de legados na nuvem BRM | Segredos por tenant, nunca no repo | 1 | ⏳ sem cofre ainda |
| Push instantâneo de regra ruim para todos | *Staging → canary → produção* | 2 | ⏳ (sem Control Plane) |
| Engine ↔ Control Plane sem auth forte | mTLS ou JWT rotativo | 2 | ⏳ |
| Ação de alto risco sem supervisão | `require_approval` | 1 | ✅ (aprovação ainda manual no chat) |
| Troca de perfil/abuso do simulador | Só `super_admin`; evento `simulacao:*`; `simulador: false` | 1 | ✅ |

## 12. Respostas a incidente (mínimo)

1. **Credencial vazada:** revogar/rotacionar; recriar usuário com novo token; revisar `audit.jsonl` do período.
2. **Cadeia de auditoria quebrada (`AuditChainError`):** preservar o arquivo, comparar com backup/git do host, investigar quem tinha acesso ao `DATA_ROOT`.
3. **Regra ruim promovida:** `git revert` em `rules.yaml` (o `from_note_id` do rascunho dá a origem) e reiniciar o servidor.
4. **Vazamento de nota:** revisar `vault_access.yaml`, a auditoria `vault_*` e o cliente responsável; avisar o cliente conforme o contrato/LGPD.
