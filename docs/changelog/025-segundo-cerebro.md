# 025 — Segundo cérebro: vault, SilverBullet e memória

**O quê:** implementa `docs/specs/025-segundo-cerebro.md`.

- `mcp_engine/vault/segundo_cerebro.py`: avaliação de conhecimento (`fonte`,
  `revisado_por`, `revisado_em`, vencida em 12 meses, área regulada sem revisor),
  `memoria_registrar` / `memoria_rascunhos` / `memoria_promover` (rascunho no `DraftStore`,
  promoção só por gestor, DLP, limites, memória nunca vira regra), registro de geradores
  de relatório e `gerar_relatorio_mensal` (template determinístico, idempotente, hash
  auditado).
- `mcp_engine/vault/escrita_motor.py`: escrita do motor em `sistema/` e `relatorios/`, com
  hash em `state.db` e restauração auditada de edição manual.
- Template do vault: `sistema/`, modelos (`_modelos/`), exemplos com frontmatter
  completo, `procedimentos/revisao-mensal-do-vault.md`; `vault_access.yaml` com zonas.
- Mapa de cobertura (skills com efeito sem política e política sem skill).
- Runbooks: `docs/runbooks/silverbullet-seguranca.md` (pesquisa na documentação oficial) e
  `silverbullet-operacao.md`.
- Testes: `tests/test_caderno_segundo_cerebro.py`.

**Achados do SilverBullet que mudaram o desenho**
- Multiusuário existe só no modo multi-space; o compose usa `SB_USER` (legado, credencial única).
- Acesso é por espaço, sem permissão por pasta nem somente leitura por subpasta: usar montagem
  `:ro` de `sistema/` e `relatorios/` no Docker.
- Quem escreve pode executar Space Lua no navegador dos outros; shell vem desligado.

**Pendências**
- Montagens `:ro` e migração para multi-space ficam com a frente do Docker.
- O ledger deve registrar seus geradores com `host.registrar_gerador_relatorio`.
