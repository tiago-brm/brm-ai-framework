# 030 — Caderno (SilverBullet) como interface de configuração de tudo (spec)

**Status:** implementada (2026-10-09). Ver "Como ficou a implementação" no fim.

**Origem:** decisão do usuário (2026-10-09). O motor BRM é o harness de
tudo; o SilverBullet é o caderno de tudo. Pessoas escrevem e leem tudo no
caderno. O motor valida, exige aprovação, ativa e executa.

## Princípio

> O caderno é a interface; o motor é a autoridade. **Editar não é
> autorizar.** Autoridade vem da aprovação por uma pessoa identificada.

## Tipos de nota de configuração

| `tipo` | Vira | Aprovação |
|---|---|---|
| `politica`, `procedimento`, `glossario` | conhecimento indexado (sem efeito sobre execução) | não; mudança auditada por hash |
| `regra` | regra JSONLogic compilada em `compilado/rules.yaml` | sim (A3) |
| `gatilho` | gatilho da spec 031 | sim (A3) |
| `modelo_pacote` | modelo de pacote e forma de cobrança (spec 023) | sim (A3) |
| `comissao`, `taxa`, `meio` | linhas com vigência no ledger (spec 023) | sim (A3) |
| `skill` | definição de skill em `compilado/skills.yaml` | sim (A3) |

## Formato da nota

Prosa livre para humanos mais um bloco estruturado validado por esquema:

````markdown
---
tipo: comissao
id: comissao-wagner-sessao
vigente_de: 2026-11-01
---
Comissão da Wagner em sessões individuais.

```brm-config
profissional: wagner
servico: sessao_individual
tipo: percentual
valor_bp: 5000
base: liquido
```
````

O interpretador da spec 009 pode gerar o bloco a partir de prosa; o humano
confere antes de enviar para aprovação.

## Ciclo

```
editar no SilverBullet → watcher detecta → validar (esquema, lint,
simulação de impacto) → estado "pendente" → aprovação de outro perfil
→ ativação (compila/materializa) → hash aprovado gravado
```

- Falha de validação nunca ativa; o motivo vai para a página de estado.
- **Versão ativa continua valendo** enquanto uma edição está pendente ou
  inválida (falha segura). Nota alterada depois de aprovada aparece como
  "alterada, aguardando aprovação".
- Aprovar usa as operações da spec 022 (segregação de funções, expiração,
  auditoria por pessoa).
- Regra de segurança: **o que vale é o conteúdo aprovado**, guardado como
  snapshot em `state.db` (`config_versao`: caminho, conteúdo, hash,
  aprovado_por, aprovado_em). O arquivo editado depois não altera nada.

## Projeções (derivadas e reconstruíveis)

- `compilado/rules.yaml`, `skills.yaml`, tabelas de parâmetros com vigência:
  geradas a partir das versões aprovadas, nunca editadas à mão.
- `sistema/` (somente leitura no caderno, regenerado pelo motor):
  `estado.md` (o que está ativo, pendente, inválido), `matriz.md`,
  `mapa-de-cobertura.md`, `alertas.md`.
- `relatorios/`: números do ledger por template determinístico (spec 025).
- Tudo que o caderno mostra de dados vem do motor; o caderno não consulta o
  banco por conta própria.

## Quem escreve e lê

- Escrita no caderno: gestor e super_admin (credencial do SilverBullet).
  Não depende disso para a segurança, porque a autoria é livre e a
  autoridade está na aprovação.
- Demais perfis: leitura via chat (RBAC e zonas da spec 017).
- Se o SilverBullet suportar vários usuários, usar um por pessoa; confirmar
  na documentação atual (regra L-99). Caso contrário, credencial única e
  aprovação por pessoa no motor.
- **Confirmado em 2026-10-09:** o modo multi-space (padrão atual) tem uma conta
  por pessoa, mas o acesso é por espaço, não por pasta, e o modo de credencial
  única (`SB_USER`, usado no compose) é legado. Ver
  `docs/runbooks/silverbullet-seguranca.md`.

## Compatibilidade

Clientes atuais com `rules.yaml` e `skills.yaml` continuam funcionando.
`scripts/exportar_config_para_notas.py` gera as notas a partir dos arquivos
existentes, e a migração é opt-in por cliente.

## Red Team / Blue Team / Postmortem

| Risco (Red Team) | Controle (Blue Team) |
|---|---|
| Alguém edita uma regra para liberar tudo | Edição não ativa; precisa de aprovação por outra pessoa; diff e simulação de impacto exibidos |
| Nota com bloco malicioso (código, caminho, URL externa) | Esquema estrito; só campos conhecidos; sem execução de código; hosts conferidos pela allowlist |
| Aprovador aprova sem ler | Resumo gerado pelo motor com o diff e o impacto (quem passa a poder o quê) |
| Edição direta de `compilado/` ou do banco | Gerados e verificados por hash na subida; divergência bloqueia e alerta |
| Prompt injection na prosa da nota | A prosa nunca é interpretada em tempo de execução; só o bloco validado vale |
| Perda do caderno | Snapshots em `state.db` reconstroem tudo; backup (spec 028) |

**Postmortem antecipado.** Aprovação em lote vira carimbo. O painel mostra
o impacto em linguagem simples e limita aprovações por hora; mudanças que
ampliam acesso ou dinheiro exigem aprovação individual.

## God Mode / L-99

**God Mode.** `sistema/estado.md` é a visão única de tudo que está ativo
no cliente, com autor, aprovador e data. Qualquer comportamento do sistema
é rastreável até uma nota aprovada.

**L-99.**

- Invariantes: (C1) o comportamento ativo é função apenas das versões
  aprovadas; (C2) reconstruir tudo a partir dos snapshots produz o mesmo
  resultado (teste de determinismo); (C3) nenhuma nota ativa sem aprovador
  identificado.
- Orçamento: validar e simular uma nota em menos de 1 s.
- Runbook: nota inválida bloqueando, reverter para versão anterior,
  reconstruir projeções, perda do SilverBullet.

## Testes previstos

- Nota válida sem aprovação não altera comportamento.
- Aprovação ativa e é auditada com a pessoa; auto-aprovação é recusada.
- Edição posterior mantém a versão aprovada ativa.
- Reconstrução das projeções é idêntica à anterior.
- Bloco inválido ou com campo desconhecido é rejeitado com mensagem clara.
- Adulteração de `compilado/` é detectada na subida.

## Fora de escopo

Edição visual de regras, execução de scripts no caderno, sincronização
bidirecional com Notion.

## Como ficou a implementação

**Entregue:** `mcp_engine/core/config_notas.py` (tipos, esquema estrito, validação,
snapshots, projeções), `config_notas_servico.py` (ciclo: propor, aprovar, ativar,
reverter, integridade), `config_notas_paginas.py` (`sistema/`), operação nativa
`ativar_configuracao` (A3) sobre as operações da spec 022, tabela `config_versao` em
`state.db`, modo `config_via_notas` por cliente, `scripts/exportar_config_para_notas.py`
e `scripts/gerar_mapa_do_sistema.py`. Ferramentas MCP: `config_estado`, `config_propor`,
`config_reverter`, `config_rascunho`.

**Decisões e desvios em relação ao texto acima**

- **Quem escreveu a nota não é conhecido.** O arquivo não registra autor e o editor pode
  ter credencial única; por isso o watcher só atualiza estado e páginas. Quem **propõe**
  (`config_propor`) e quem **aprova** são pessoas identificadas e diferentes (segregação da
  spec 022). Auto-aprovação é recusada.
- **O conteúdo aprovado viaja na operação**, com hash conferido; a ativação não relê o
  arquivo. O texto completo da nota (prosa e bloco) é o snapshot.
- **Ordem das regras.** O avaliador usa a primeira regra que casa. No modo por notas a
  ordem é `ordem` (frontmatter, padrão 100), depois deny, require_approval e allow, depois
  `id`. Uma nota `allow` nunca passa à frente de um `deny` de mesma ordem. A exportação
  preserva a ordem original com `ordem`.
- **Modo por notas é exclusivo:** o motor carrega só `compilado/`; `rules.yaml` e
  `skills.yaml` são ignorados. Enquanto nada é aprovado, não há regras nem skills ativas.
  `vault_promote_draft` de regra/skill fica desativado nesse modo.
- **Tipos de módulo:** `TipoConfig` (nome, esquema estrito, `materializar(versões_ativas)`
  idempotente, lint, resumo). O módulo registra por `host.registrar_tipo_config` em
  `install`. Registro é por cliente, não global. A materialização roda antes do snapshot;
  falha dela não ativa.
- **Gatilho** tem um esquema mínimo provisório (`GatilhoBloco`) até a spec 031, que o
  substitui via `registrar_tipo(..., substituir=True)`.
- **Impacto de regra** é calculado reavaliando os cenários do simulador para cada usuário
  antes e depois; sem cenários cadastrados, o resumo avisa que o impacto não foi calculado.
  Skill nova só aparece como tool após reiniciar o servidor.
- **Limite de aprovações por hora** por aprovador: `config_aprovacoes_por_hora` (padrão 30).
- **Verificação por hash** compara o compilado em disco com o recalculado dos snapshots,
  e cada snapshot com a operação que o aprovou (aprovador diferente do proponente, hash
  igual). Reiniciar após queda no meio da ativação repara sozinho; qualquer outra
  divergência bloqueia a subida.
- **Conhecimento** (`politica`, `procedimento`, `glossario`): hash por nota em `state.db`;
  mudança gera o evento `conhecimento_alterado`.
- Desativar uma configuração (remover do caderno) **não** a desativa: o motor mantém a
  versão ativa e alerta. Uma desativação com aprovação fica para uma próxima rodada.
