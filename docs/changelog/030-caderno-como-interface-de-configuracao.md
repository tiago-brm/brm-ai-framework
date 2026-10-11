# 030 — Caderno como interface de configuração

**O quê:** implementa `docs/specs/030-caderno-como-interface-de-configuracao.md`.

- `mcp_engine/core/config_notas.py`: registro de tipos (`politica`, `procedimento`,
  `glossario` sem aprovação; `regra`, `skill`, `gatilho` com aprovação), ponto de extensão
  `TipoConfig`/`Extensoes` para módulos (esquema estrito, `materializar` idempotente),
  bloco `brm-config` em YAML seguro (sem tags, âncoras nem aliases), validação (esquema,
  lint JSONLogic, hosts pela allowlist, esquemas de URL e `../` proibidos, tamanho),
  snapshots (`config_versao` em `state.db`) e projeções determinísticas.
- `mcp_engine/core/config_notas_servico.py`: `Caderno` com `propor`, `reverter`,
  ativação pela operação nativa `ativar_configuracao` (A3, aprovação de outra pessoa pelo
  mecanismo da spec 022), impacto legível (diff do bloco e "quem passa a poder o quê" por
  cenário), verificação de integridade na subida e reconstrução
  (`python -m mcp_engine.core.config_notas_servico verificar|reconstruir <cliente>`).
- `config_notas_paginas.py`: `sistema/estado.md`, `matriz.md`, `mapa-de-cobertura.md`,
  `alertas.md`, determinísticas e restauradas se editadas à mão.
- Motor: `config_via_notas` e `config_aprovacoes_por_hora` em `ClientConfig`; ferramentas
  `config_estado`, `config_propor`, `config_reverter`, `config_rascunho`; gancho do watcher
  (`on_external_change`); `avaliar_skill(ruleset=...)`; `ModuleHost.registrar_tipo_config`.
- Scripts: `exportar_config_para_notas.py` (preserva a ordem das regras) e
  `gerar_mapa_do_sistema.py`.
- Testes: `tests/test_config_notas.py`, `test_caderno.py`, `test_caderno_scripts.py`
  (inclui propriedades C1, C2 e C3, adulteração do compilado e do snapshot, injeção na
  prosa, auto-aprovação recusada).

**Desvios e pendências**
- Clientes com `rbac_default: deny` precisam conceder as ferramentas novas
  (`scripts/migrar_permissoes.py` ou painel).
- Skill nova só vira tool após reiniciar o servidor.
- Remover a nota não desativa a configuração (alerta apenas); desativar com aprovação fica
  para depois. `gatilho` tem esquema provisório até a spec 031.
- Em modo por notas, `rules.yaml`/`skills.yaml` são ignorados.
