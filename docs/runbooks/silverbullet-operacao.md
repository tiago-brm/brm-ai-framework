# SilverBullet e caderno: operação do dia a dia

Spec 030 (caderno como interface de configuração) e spec 025 (segundo cérebro).

## Fluxo normal de uma configuração

1. Gestor escreve a nota no caderno (`regra`, `skill`, parâmetro de módulo) com bloco `brm-config`.
2. `config_estado` mostra o estado; `config_propor <caminho>` valida e abre a operação.
3. Outra pessoa (gestor) lê o resumo com o impacto e roda `aprovar_operacao`.
4. A versão fica em `state.db` (`config_versao`) e o motor regenera `data/<cliente>/compilado/`.

## Nota inválida bloqueando

- A versão aprovada anterior continua valendo. Veja o motivo em `sistema/estado.md` ou
  `sistema/alertas.md`, corrija a nota e rode `config_propor` de novo.
- Para sair do estado inválido sem corrigir, volte a nota ao texto da versão ativa ou use
  `config_reverter <caminho>` (exige aprovação).

## Reverter para a versão anterior

`config_reverter <caminho>` abre uma operação com o conteúdo da versão anterior. Depois
de aprovada, o arquivo do caderno continua com o texto novo e aparece como "alterada,
aguardando aprovação": ajuste o texto da nota para ficar igual ao que está ativo.

## Compilado divergente (a subida falha)

A mensagem `compilado/ diverge das versões aprovadas` indica edição manual ou corrupção
de `data/<cliente>/compilado/`. O evento `config_compilado_divergente` fica na auditoria.

```
PYTHONPATH=. uv run python -m mcp_engine.core.config_notas_servico verificar <cliente>
PYTHONPATH=. uv run python -m mcp_engine.core.config_notas_servico reconstruir <cliente>
```

A reconstrução é idempotente e vem só dos snapshots aprovados. Se a verificação falhar
por "snapshot adulterado" ou "sem aprovação válida", o `state.db` foi alterado: restaure
o backup (`backup-e-restauracao.md`) e investigue a auditoria.

## Perda do SilverBullet ou do vault

- As **versões ativas** estão em `state.db`, independentes do caderno. O sistema segue
  funcionando com o comportamento aprovado.
- Restaure o vault do backup (`vault/`). Notas de configuração ausentes aparecem como
  "removida do caderno (versão ativa mantida)". Para recriar o texto de uma nota a partir
  do snapshot, leia a tabela `config_versao` (coluna `conteudo`).
- Páginas de `sistema/` e `relatorios/` são regeneradas (`gerar_mapa_do_sistema.py` e
  `gerar_relatorio_mensal`).

## Editor fora do ar

O chat continua lendo o vault e aprovando operações. Nada depende do editor para valer.
Suba de novo com o perfil `editor` do compose.

## Restaurar uma nota

Notas de conhecimento: restaure do backup do vault; a mudança aparece na auditoria como
`conhecimento_alterado` (hash anterior e novo). Notas de configuração: o conteúdo
aprovado está em `config_versao`.

## Revisão mensal

Siga `vault/procedimentos/revisao-mensal-do-vault.md` (template): alertas, revisões
vencidas (12 meses), memórias expiradas, mapa de cobertura, configurações pendentes.

## Migrar um cliente atual para o modo por notas

1. `PYTHONPATH=. uv run python scripts/exportar_config_para_notas.py <cliente>` (não altera `rules.yaml`).
2. Em `client_config.yaml`: `config_via_notas: true`.
3. Suba o motor, proponha e aprove cada nota (uma pessoa propõe, outra aprova). Até a
   aprovação, **nenhuma** regra ou skill vale no modo por notas (a ordem das regras é
   preservada pelo campo `ordem`).
4. Mantenha `rules.yaml` e `skills.yaml` como referência; no modo por notas eles são ignorados.
