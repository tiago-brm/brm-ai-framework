# 023 — Módulo financeiro: ledger gerencial em SQLite

**O quê:** implementa a spec `docs/specs/023-modulo-financeiro-ledger.md` como o módulo de
catálogo `financeiro` (`mcp_engine/modules/financeiro/`), habilitado por cliente e adotado
skill a skill com `usar: financeiro.<skill>`. Cliente de piloto gerado por
`scripts/novo_cliente.py clinica-piloto --modulos financeiro`.

## Recursos verificados (regra L-99)

- SQLite embarcado: 3.46.0 (Python 3.11.9). `STRICT` exige 3.37 ou mais; a imagem
  `python:3.11-slim-bookworm` do Dockerfile traz a biblioteca do Debian bookworm (3.40), que
  também suporta `STRICT`. Confirmar na imagem final com `sqlite3.sqlite_version`.
- Hypothesis 6.168.5 (já instalado) para os testes de propriedade.
- Sem dependência nova nesta parte (SQLite e `sqlite3` da biblioteca padrão; sem SQLAlchemy
  no módulo, porque o `ModuleHost` já entrega o banco e as tabelas `STRICT`/`CHECK`/gatilhos
  fazem a validação).

## Banco

- Arquivo `data/<cliente>/financeiro.sqlite` (o nome segue o contrato do `ModuleHost`:
  `<modulo>.sqlite`; a spec citava `ledger.sqlite`). Conexão própria do módulo
  (`db.abrir`): `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`, transações
  `BEGIN IMMEDIATE`. A migração roda pelo `ModuleHost`; `db.preparar_banco` faz o mesmo fora
  do motor (CLI e testes).
- Migrações: `001_base.sql` (cadastros, `recebimento`, `despesa`, `repasse`,
  `fechamento_mensal`, gatilhos), `002_sessoes_pacotes.sql` (sessão, ocorrência, modelo de
  pacote, pacote, cobrança prevista, `pendencia_operacional`), `003_importacao.sql` (lote e
  linha importada). Todas as tabelas são `STRICT` com `CHECK`; valores em centavos inteiros.
- Imutabilidade por gatilho: `UPDATE` e `DELETE` recusados em `recebimento`,
  `recebimento_sessao`, `despesa`, `repasse`, `fechamento_mensal`, `sessao_ocorrencia`,
  `cobranca_prevista`, `pacote` (exceto `status`) e nos parâmetros (`taxa_meio`,
  `comissao_regra`, `meio_recebimento`, `modelo_pacote`), que só aceitam encerrar a
  vigência uma vez. Em `recebimento` ficam livres `comprovante_ref`, `nf_status` e
  `nf_numero`; em `despesa`, `comprovante_ref`.
- Estorno: contra-lançamento com `estorno_de` e valor negativo; gatilho confere original do
  mesmo paciente e saldo estornável (F3). Mês fechado: gatilho recusa `INSERT` com
  `data_competencia` no mês fechado (F5); o estorno entra no mês corrente.
- Fechamento é anexar-somente: `fechado` e `reaberto` são linhas novas; vale a última por
  mês e unidade (view `fechamento_vigente`). O hash do conteúdo do mês é gravado e conferido
  por `verificar_integridade`, o que denuncia edição do `.sqlite` por fora.
- Acréscimos à spec: `repasse.data_pagamento` (cópia imutável para consulta mensal em menos
  de 200 ms), `repasse.limitado`, `profissional.email` (identifica o profissional que consulta
  o próprio repasse), `meio_recebimento` com vigência, meio `Caixa` semeado, `recebimento.servico`,
  `recebimento.parcelas`.

## Repasse

`lancamentos.calcular_repasse` grava um snapshot imutável por recebimento: taxa do meio
(pontos-base, vigente na data do pagamento), base `bruto` ou `liquido`, comissão
`percentual` (pontos-base) ou `fixo`, arredondamento meio para cima, **sobra de centavos para
a clínica** e comissão limitada ao líquido (marcador `limitado`). F6 vale por construção,
por `CHECK` e por gatilho. Resolução da regra: serviço (4) > forma (2) > unidade (1) >
profissional; empate ou ausência é erro explícito. Estorno de recebimento gera repasse
espelhado, e o último estorno zera os centavos exatamente. Juros não geram repasse.

## Parâmetros: contrato para a spec 030 (sem skill de escrita)

Funções em `mcp_engine/modules/financeiro/params.py`, chamadas pelo mecanismo de notas
aprovadas. Todas recebem o caminho do banco (`db.caminho_ledger(data_root, cliente)`), um
`dict` e `criado_por` (a pessoa que aprovou), abrem a própria transação e devolvem o id:

| Função | Chaves do `dict` |
|---|---|
| `materializar_comissao(path, dados, criado_por=)` | `profissional`, `tipo` (`percentual` ou `fixo`), `valor` ("40", "12,5%" ou "R$ 10,00"), `base` (`bruto`/`liquido`, padrão `liquido`), `vigente_de`; opcionais `servico`, `forma`, `unidade` |
| `materializar_taxa(...)` | `meio`, `forma`, `taxa` ("1,5"), `vigente_de`; opcional `parcelas` (1) |
| `materializar_meio(...)` | `nome`, `tipo` (`banco`, `maquininha`, `dinheiro`), `vigente_de`; opcional `substitui` (nome do meio que deixa de valer na véspera) |
| `materializar_modelo_pacote(...)` | `nome`, `modo_cobranca` (`antecipado`, `por_sessao`, `parcelado`, `mensal`), `n_sessoes`, `preco_total` e/ou `preco_unitario`, `vigente_de`; opcionais `n_parcelas`, `intervalo_dias`, `validade_dias`, `falta_consome`, `falta_cobrada`, `antecedencia_horas` |

Regras: mesmo escopo aberto é encerrado na véspera da nova vigência; vigência que não começa
depois da atual é recusada (a história não é reescrita); dado inválido levanta
`ErroFinanceiro`. Cadastros auxiliares (`garantir_unidade`, `garantir_profissional`,
`garantir_paciente`, `garantir_categoria`) estão no mesmo módulo.

## Skills (handlers nativos, autonomia declarada)

| Skill | Autonomia | Observação |
|---|---|---|
| `registrar_recebimento`, `registrar_despesa` | A2 | chave de idempotência = id da operação (`operacao_id` único) |
| `estornar_lancamento` | A3 (aprovador `admin`) | saldo estornável conferido no banco |
| `anexar_comprovante`, `atualizar_status_nf` | A2 | só nome de arquivo, sem caminho |
| `registrar_falta`, `registrar_pacote`, `resolver_pendencia` | A2 | |
| `fechar_mes` | A3 (aprovador `admin`) | recusa se `verificar_integridade` falhar |
| `reabrir_mes` | A2 + regra: só `super_admin` | acréscimo à spec, que citava a reabertura nas regras |
| `consultar_financeiro`, `listar_pendencias` | A0 | 19 consultas nomeadas, sem SQL livre |
| `exportar_planilha` | A1 | ver changelog 024 |

Consultas: `resumo_mensal`, `por_forma`, `por_conta`, `por_profissional`, `por_unidade`,
`sem_nf`, `despesas_por_categoria`, `resultado_mensal`, `ultimos_lancamentos`,
`repasse_por_profissional`, `taxas_por_meio`, `parte_clinica`, `a_receber`, `saldo_pacote`,
`faltas_por_periodo`, `pendencias`, `painel_financeiro`, `verificar_integridade`,
`simular_repasse`. Filtros por lista branca e parâmetros ligados. Perfil que não é `admin` nem
`super_admin` recebe agregados sem nome; repasse, parte da clínica, taxas, simulação e
integridade são do gestor, e o profissional (`viewer` com e-mail em `profissional.email`) vê
só o próprio repasse.

## Fatos derivados (alternativa dentro do handler)

`valor_atipico` (±50% do valor do paciente vezes o número de sessões), `retroativo_dias`
(limite 7), `mes_fechado`, `duplicidade_suspeita` e `paciente_a_confirmar` são calculados
pelo módulo e devolvidos em `fatos`. O motor não monta fatos de módulo para as regras (os
fatos derivados do núcleo vêm do vault), então: duplicidade exige `confirmar_duplicidade`
(e, para `viewer`, a regra `duplicidade-confirmada-exige-gestor` pede aprovação do gestor);
atípico, retroativo e paciente a confirmar, lançados por perfil que não é gestor, **não
gravam**: viram a pendência `lancamento_para_revisao` e a resposta traz `requer_gestor`;
mês fechado e F3 são recusados pelo banco.

## Desvios e pendências

- Fatos de módulo nas regras JSONLogic (a spec pede aprovação do gestor por regra para
  atípico e retroativo) exigem mudança no núcleo; ver o relatório da entrega.
- `HandlerContext` não traz quem aprovou. A operação executa como o proponente, então
  `fechamento_mensal.aprovado_por` guarda o proponente; precisa de `aprovado_por` no contexto.
- `painel_financeiro` devolve `operacoes_aguardando_aprovacao: null`: o módulo não enxerga o
  `state.db`. O `/health` ampliado com `verificar_integridade` é do núcleo.
- Sem cadastro novo de paciente pelo ledger: paciente desconhecido vira pendência ("cadastre
  na Agenda primeiro"); `paciente_ref` nasce pela importação (`a_confirmar`) ou pelo
  sincronizador da spec 026, que deve usar `sessoes.upsert_sessao`. As pendências
  `sessao_sem_desfecho` saem de `sessoes.gerar_pendencias_sem_desfecho`, a ser chamada pelo
  sincronizador.
- `por_sessao`: `sessoes.gerar_cobrancas_por_sessao(pacote)` cria a cobrança das sessões
  realizadas e deve ser chamada pelo sincronizador.

## Testes

`tests/test_financeiro.py` (unitários e adversariais: injeção de SQL em filtros, estorno
acima do saldo, mês fechado, duplicidade, perfil sem permissão, vigência, precedência,
troca de maquininha), `tests/test_financeiro_propriedades.py` (Hypothesis: F1 a F7, F9, F10),
`tests/test_financeiro_desempenho.py` (consulta mensal abaixo de 200 ms com 100 mil linhas,
inclusive com todas no mesmo mês; escritas e estornos concorrentes) e
`tests/test_financeiro_motor.py` (proposta, confirmação, aprovação e regras pelo motor com o
cliente gerado).
