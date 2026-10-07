# 017 — Controle de acesso por nota no vault

**O quê:** implementa a spec `docs/specs/017-acesso-por-nota-no-vault.md`
(aprovada em 2026-10-07, com as duas recomendações das perguntas em
aberto: `default: deny` nos clientes de saúde e vault aberto no `example`;
nome do paciente só na ficha de identificação se o cliente aprovar).

- `mcp_engine/vault/access.py` (novo): `filter_notes`, que avalia as
  políticas em JSONLogic com os fatos `actor`, `actor_role`, `note.*` e
  `vinculo`. A primeira política que casar decide; sem política, vale o
  `default`. `metadata_only` devolve só os campos de `expose`, sem conteúdo,
  links nem tags, e o título vira o `paciente_id`. Política com erro de
  avaliação não casa, então o `default` se aplica.
- `mcp_engine/core/models.py`: `VaultPolicy`, `VaultAccess`, `VaultRead` e
  `SkillDefinition.vault_read`.
- `mcp_engine/core/config_loader.py`: `FileSystemVaultAccessProvider`. Sem
  `vault_access.yaml` o vault continua aberto, como antes.
- `mcp_engine/core/mcp_server.py`
  - `vault_search`, `vault_list_notes` e `vault_get_related` filtram pelo
    perfil logado. A busca pede mais notas ao provider (até 5x o limite, no
    máximo 50) para que as ocultadas não consumam o limite.
  - Cada leitura registra um evento com a identidade real de quem pediu,
    quantas notas foram devolvidas e ocultadas e as políticas aplicadas,
    sem conteúdo. Políticas com `audit` somam `acesso_privilegiado`.
  - `vault_add_note` exige perfil em `write_roles` (padrão: admin e
    super_admin).
  - Leitura mediada por skill: `vault_read` declara tipos de nota, a skill
    faz a busca depois que as regras passam e devolve os trechos em
    `vault`. É assim que `buscar_aulas` (aulas) e `responder_lead_whatsapp`
    (FAQ) servem perfis que não leem essas notas diretamente.
  - A leitura interna dos fatos derivados continua sem filtro, porque lê só
    o frontmatter.
- Clientes `instituto-afw` e `clinica-psi`: `vault_access.yaml`. Aulas e
  cadastro de alunos ficam só para a equipe; prontuário e resumo só com
  vínculo; registro documental só para o responsável; a recepção e outros
  psicólogos recebem a ficha (`paciente_id`, `unidade`,
  `psicologo_responsavel`).
- Demos: `scripts/demo_clinica.py` não filtra mais notas por conta própria;
  `scripts/demo_casos_de_uso.py` ganhou a cena "a mesma busca, quatro
  respostas" e lê a carteira com o perfil da responsável técnica.

## O que ainda não existe

- `vault_interpret_and_propose`, `vault_list_drafts` e as tools de
  promoção não passam pelo filtro de leitura. Operam sobre `config_drafts/`
  e continuam restritas por RBAC de tool.
- Política por campo dentro da mesma nota.
- Nome do paciente na ficha de identificação: a configuração atual expõe
  só ID, unidade e psicólogo.
- Criptografia do vault em repouso e NER no DLP.

**Validação:** `tests/test_vault_access.py` (12 testes: matriz de perfis,
`metadata_only`, `default: deny`, sobrebusca, auditoria, escrita e leitura
mediada); suíte completa com 265 testes e `ruff check .` limpos.
