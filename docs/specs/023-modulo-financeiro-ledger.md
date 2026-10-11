# 023 — Módulo financeiro: ledger gerencial em SQLite (spec)

**Status:** implementada (2026-10-09), com pendências de integração no núcleo. Ver `docs/changelog/023-modulo-financeiro-ledger.md`.

**Origem:** planilhas de controle da clínica (`MODELO DRIVE SECRETÁRIAS.xlsx`
e `MODELO RELATÓRIO FINANCEIRO.xlsx`). Escopo gerencial, em regime de caixa,
sem plano de contas. Nota fiscal aqui é apenas **status**; a emissão é Fase 2.

## Armazenamento

- Módulo `mcp_engine/modules/financeiro/`, banco `data/<cliente>/ledger.sqlite`.
- SQLAlchemy; `PRAGMA journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout`.
- Tabelas `STRICT` e `CHECK` (valor, enumerações, `estorno_de`) para o banco
  recusar dado inválido sem código extra; o SQLite embarcado hoje é 3.46, que
  suporta `STRICT`. Confirmar a versão no Dockerfile.
- Valores em **inteiros de centavos**. Entrada de usuário passa por
  `parse_brl` ("R$ 1.234,56", "275", "275,5") e rejeita ambiguidade.
- Migrações numeradas em `modules/financeiro/migrations/*.sql`, aplicadas na
  inicialização, controladas por tabela `schema_version`.

## Tabelas

| Tabela | Campos principais |
|---|---|
| `unidade` | id, nome (Adultos, Infantil...) |
| `profissional` | id, nome, `external_ref` (Agenda), ativo |
| `paciente_ref` | id interno, nome de exibição, unidade, profissional, `valor_sessao_centavos`, `external_ref`, `status` (`ativo`, `a_confirmar`) |
| `meio_recebimento` | id, nome (Sicoob, Stone, Sicredi, Inter, Caixa), tipo (`banco`, `maquininha`, `dinheiro`), ativo |
| `taxa_meio` | meio_id, forma, parcelas, `taxa_bp` (pontos-base), `vigente_de`, `vigente_ate` |
| `comissao_regra` | profissional_id, escopo opcional (unidade, serviço, forma), `tipo` (`percentual`, `fixo`), `valor_bp` ou `valor_centavos`, `base` (`bruto`, `liquido`), `vigente_de`, `vigente_ate`, `criado_por` |
| `forma_pagamento` | id, nome (pix, débito, crédito, dinheiro, ted, acordo) |
| `categoria_despesa` | id, grupo (variada, fixa, imposto, folha), nome |
| `recebimento` | data_pagamento, paciente_id, forma, conta, `valor_centavos`, profissional_id, secretaria_id, unidade_id, `tipo` (sessao, pacote, acordo, juros, estorno), `nf_status` (emitida, sem_nf, pendente), `nf_numero`, `comprovante_ref`, `estorno_de`, observação, origem, `operacao_id` |
| `recebimento_sessao` | recebimento_id, data_sessao |
| `despesa` | data, descrição, categoria_id, forma, `valor_centavos`, recorrente, `estorno_de`, origem, `operacao_id` |
| `repasse` | recebimento_id, profissional_id, `comissao_regra_id`, `taxa_centavos`, `repasse_centavos`, `parte_clinica_centavos` (gravado no lançamento, imutável) |
| `fechamento_mensal` | mês, unidade (opcional), status, aprovado_por, hash |

`paciente_ref` guarda o mínimo (nome e vínculos). Nada clínico entra aqui.

## Imutabilidade

- Gatilhos SQLite proíbem `UPDATE` e `DELETE` em `recebimento`,
  `recebimento_sessao` e `despesa` (exceto o campo `comprovante_ref` e
  `nf_status/nf_numero`, que têm trilha própria na auditoria).
- Estorno e correção são novos registros com `estorno_de` e valor negativo.
- Mês fechado: novos lançamentos com `data_competencia` no mês fechado são
  recusados; ajustes entram no mês corrente com referência ao original.

## Skills (handlers nativos, spec 021)

| Skill | Efeito | Confirmação |
|---|---|---|
| `registrar_recebimento` | grava `recebimento` (+ sessões) | sim |
| `registrar_despesa` | grava `despesa` | sim |
| `estornar_lancamento` | contra-lançamento | sim, aprovação do gestor |
| `anexar_comprovante` | define `comprovante_ref` | sim |
| `atualizar_status_nf` | `nf_status`, `nf_numero` | sim |
| `consultar_financeiro` | consulta parametrizada (abaixo) | não |
| `fechar_mes` | cria `fechamento_mensal` | sim, aprovação do gestor |
| (materialização) | `comissao_regra`, `taxa_meio` e `meio_recebimento` nascem de **notas de configuração aprovadas** (spec 030), com vigência; não há skill de escrita direta |

`consultar_financeiro(consulta, periodo, filtros)` aceita só consultas
nomeadas, sem SQL livre: `resumo_mensal`, `por_forma`, `por_conta`,
`por_profissional`, `por_unidade`, `sem_nf`, `despesas_por_categoria`,
`resultado_mensal`, `ultimos_lancamentos`. Resultado em centavos mais texto
formatado. Para perfis sem permissão de ver pacientes, as consultas devolvem
agregados sem nome.

## Fatos derivados para as regras

`valor_atipico` (fora de ±X% do `valor_sessao` do paciente),
`retroativo_dias`, `mes_fechado`, `duplicidade_suspeita` (mesmo paciente,
valor, data e forma), `paciente_a_confirmar`. Nunca vêm do chamador.

## Regras padrão (`clinica-psi`)

- Secretária registra recebimento e despesa; valor atípico ou retroativo
  acima de N dias exige aprovação do gestor.
- Estorno e fechamento de mês só com aprovação do gestor.
- Reabrir mês: apenas super_admin, auditado.

## Testes previstos

- `parse_brl` com casos válidos e ambíguos.
- Gatilhos impedem alteração e exclusão; estorno cria registro negativo.
- Mês fechado recusa lançamento retroativo.
- Cada consulta nomeada bate com um cálculo independente em Python.
- Perfil sem permissão de paciente recebe agregados.
- Duplicidade suspeita aciona a regra.
- Concorrência: duas escritas simultâneas não corrompem.

## Fora de escopo

Contas a receber derivadas da agenda, cobrança, conciliação bancária, emissão
de NFS-e, plano de contas.

## Perguntas em aberto

Resolvidas em 2026-10-09: base da comissão (bruto ou líquido), percentuais e
escopo (por profissional, com serviço e forma opcionais) são **parâmetros
por regra**, sem padrão fixo no código. O piloto preenche os valores da
clínica; o módulo continua agnóstico.

## Red Team / Blue Team / Postmortem

| Risco (Red Team) | Controle (Blue Team) |
|---|---|
| Secretária lança, estorna e some com o rastro | Imutabilidade por gatilho, estorno só com aprovação, auditoria por pessoa |
| Lançamento duplicado ou valor 10x (digitação) | `duplicidade_suspeita`, `valor_atipico` e confirmação com resumo |
| Injeção de SQL via consulta livre | Só consultas nomeadas e parametrizadas, sem SQL do modelo |
| Edição do arquivo `.sqlite` por fora | Hash do fechamento mensal registrado na auditoria; backup versionado |
| Paciente identificado em consulta de perfil sem permissão | Agregados por padrão |
| Alterar mês já fechado | Fechamento bloqueia retroativo; ajuste entra no mês corrente |

**Postmortem antecipado.** O desastre típico é o ledger e a planilha
divergirem e ninguém saber qual vale. Regra: durante o piloto, a planilha é
conferência e o ledger é a fonte; todo mês há reconciliação por total e por
forma, com divergência explicada por escrito antes do fechamento.

## God Mode / L-99

**God Mode.**

- Consulta `painel_financeiro`: visão única do mês (entradas, saídas,
  resultado, por unidade e por forma, sem NF, pendências de importação,
  operações aguardando aprovação, situação do fechamento).
- `verificar_integridade`: confere os invariantes abaixo e relata qualquer
  violação. Pode rodar sob demanda e no `/health` ampliado.

**L-99.**

- Invariantes: (F1) soma por forma = total do mês; (F2) soma por unidade =
  total do mês; (F3) cada estorno referencia um original do mesmo paciente,
  com valor absoluto menor ou igual ao saldo; (F4) nenhum valor em ponto
  flutuante; (F5) lançamento em mês fechado só por ajuste; (F6) `repasse + parte_clinica = valor - taxa`, sem sobra nem falta de centavo; (F7) para cada recebimento existe no máximo uma `comissao_regra` vigente aplicável, e parâmetros nunca são editados, só substituídos por nova vigência.
- Testes de propriedade (Hypothesis) para F1 a F5 com lançamentos
  aleatórios, estornos e fechamentos.
- Orçamento: consulta mensal em menos de 200 ms com 100 mil linhas.
- Modos de falha: competência versus data de pagamento definida
  (usa `data_pagamento`, regime de caixa); fuso fixado no do cliente.
- Runbook: reabrir mês, corrigir paciente duplicado, restaurar backup.

## Repasse (comissão) e meios de recebimento parametrizáveis

Decisão do usuário (2026-10-09): existe repasse com **comissões
diferenciadas**, e tudo isso deve ser parametrizável, inclusive a troca de
maquininha (Stone ou outra).

- **Nada fixo no código.** A fonte são notas `tipo: comissao|taxa|meio` no
  caderno, aprovadas pelo gestor (spec 030). Comissão e taxas materializam em
  tabelas com vigência
  (`vigente_de`, `vigente_ate`). Alterar um parâmetro cria uma nova linha e
  encerra a anterior; a história nunca é reescrita.
- **Resolução da regra** de comissão, da mais específica para a geral:
  profissional + serviço + forma, profissional + serviço, profissional +
  forma, profissional. Empate ou ausência vira erro explícito, nunca um
  valor padrão silencioso.
- **Cálculo no lançamento:** taxa do meio (pela forma e parcelas) abate o
  bruto quando `base = liquido`; comissão por percentual (pontos-base, inteiro)
  ou valor fixo. Arredondamento em centavos, meio para cima; a **sobra de
  centavos fica na parte da clínica**, garantindo F6.
- **Snapshot imutável:** a tabela `repasse` guarda a regra usada e os
  valores calculados. Mudança de parâmetro depois não altera lançamentos
  antigos. Correção retroativa é feita por estorno e novo lançamento.
- **Troca de maquininha:** A troca de maquininha (nota `tipo: meio` aprovada) marca o meio antigo com
  `vigente_ate` e ativa o novo. O importador usa o meio vigente na data do
  pagamento (spec 024), então planilhas antigas continuam mapeando para a
  Stone e as novas para a máquina nova.
- **Consultas novas** em `consultar_financeiro`: `repasse_por_profissional`,
  `taxas_por_meio`, `parte_clinica`. Profissional só vê o próprio repasse.
- **Simulação:** `simular_repasse(periodo)` recalcula com os parâmetros
  atuais sem gravar, para o gestor comparar antes de mudar uma comissão.
- **Testes:** vigência (lançamento antes, no dia e depois da troca),
  precedência de regras, F6 por teste de propriedade com valores aleatórios,
  troca de maquininha no meio do mês.
- **Red Team:** alterar comissão para beneficiar alguém e apagar o rastro.
  **Blue Team:** parâmetro só muda por aprovação do gestor, com vigência e
  auditoria por pessoa; profissional não vê o repasse de outro.
  **Postmortem antecipado:** comissão combinada de boca e nunca
  parametrizada gera divergência de pagamento; a tabela de comissão é pré-requisito
  do piloto e fica registrada no vault (`politicas/`).

## Sessões, faltas, pacotes e cobrança prevista

Decisões do usuário (2026-10-09): a recepção cadastra paciente e agenda no
front da Agenda; **falta é registrada no ledger** (a Agenda poderá ter o
status depois); o pacote e a forma de cobrança (inclusive por sessão) são
**parametrizáveis**.

### Tabelas adicionais

| Tabela | Campos principais |
|---|---|
| `sessao` | `agenda_id` único, paciente_id, profissional_id, serviço (referência), `inicio_utc`, `status_agenda`, `valor_previsto_centavos`, `valor_previsto_origem` (`paciente`, `pacote`, `servico`), `sincronizado_em` (espelho mínimo, spec 026) |
| `sessao_ocorrencia` | sessao_id, `tipo` (`falta`, `falta_justificada`, `cancelamento_tardio`), motivo, registrado_por, registrado_em, `estorno_de`, `operacao_id` (somente anexar) |
| `modelo_pacote` | projeção de notas `tipo: modelo_pacote` (spec 030): nome, `modo_cobranca`, número de sessões, preço total ou unitário, validade em dias, política de falta, vigência |
| `pacote` | paciente_id, modelo_id, preço acordado, sessões totais, início, validade, status |
| `cobranca_prevista` | pacote_id, sessao_id (opcional), vencimento, `valor_centavos`, origem |
| `pendencia_operacional` | tipo, referência, criada_em, status, resolvida_por |

`recebimento_sessao` ganha `sessao_id` opcional; `recebimento` ganha
`pacote_id` e `cobranca_prevista_id` opcionais.

### Pacote e forma de cobrança (parametrizáveis)

- O **modelo** define a forma de cobrança: `antecipado`, `por_sessao`,
  `parcelado` (N parcelas com datas) e `mensal` (valor fixo, sessões
  incluídas). Valores iniciais a confirmar com a clínica; novos modelos
  entram por nota aprovada, sem código.
- O **pacote** é a instância para um paciente, com preço acordado e exceções.
- A **cobrança prevista** é gerada pelo modelo: total no início
  (`antecipado`), uma por sessão realizada (`por_sessao`), parcelas nas datas
  (`parcelado`), ou mensal.
- **A receber** é derivado: previsto menos recebido vinculado. Não há módulo
  separado de contas a receber nesta fase.
- **Política de falta no modelo:** se a falta consome a sessão, se é cobrada
  e a antecedência mínima do cancelamento sem cobrança.
- Valor previsto: precedência pacote, depois valor acordado do paciente,
  depois preço do serviço na Agenda. Divergência entre eles gera aviso, não
  bloqueio.

### Faltas e desfechos

- **Fonte única por desfecho:** *realizada* vem só da Agenda (`COMPLETED`);
  *falta* é registrada aqui (`registrar_falta`, com confirmação) até a Agenda
  ter o status. O ledger não grava "realizada".
- Falta e sessão concluída na Agenda para a mesma sessão geram pendência
  `conflito_desfecho`, resolvida por gestor.
- Pendência `sessao_sem_desfecho` para sessão passada ainda não concluída
  nem cancelada nem com falta (spec 026).

### Skills e consultas novas

`registrar_pacote` (A2), `registrar_falta` (A2), `resolver_pendencia` (A2).
Consultas: `a_receber`, `saldo_pacote`, `faltas_por_periodo`, `pendencias`.

### Invariantes adicionais

(F8) uma sessão não é realizada e falta ao mesmo tempo sem pendência aberta;
(F9) sessões consumidas de um pacote não passam do total; (F10) `a_receber`
nunca é negativo, e excesso de recebimento aparece como crédito explícito.
