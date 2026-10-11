# 024 — Importador e exportador de planilhas (spec)

**Status:** implementada (2026-10-09), com pendências menores. Ver `docs/changelog/024-importador-exportador-planilhas.md`.

**Origem:** as planilhas da clínica são a fonte de verdade hoje. Para migrar
sem ruptura, o ledger (spec 023) precisa receber o histórico e devolver o
mesmo layout, de modo que a planilha vire relatório.

## Importador

`mcp_engine/modules/financeiro/importers/` e `scripts/importar_planilha.py`.

- **Dry-run por padrão.** `--aplicar` grava. O relatório de importação lista
  inseridos, duplicados, pendências e erros.
- **Idempotência:** chave = SHA-256 do arquivo + aba + linha + conteúdo.
  Reimportar não duplica.
- **Layout "secretárias"** (aba `Planilha1`): cabeçalho livre
  (`ADRIA ... FEVEREIRO 2026 - ADULTOS`) que fornece secretária, mês/ano e
  unidade; colunas `DATA, PACIENTE, FORMA PGMT, DATA SESSÕES, VALOR, NF,
  PSICOLOGO(A), NOME COMPROVANTE`; observações soltas fora das colunas viram
  `observacao` e geram pendência quando sugerem estorno.
- **Layout "financeiro"** (aba `SAÍDAS`): blocos `GASTOS VARIADOS`,
  `DESPESAS FIXAS`, `IMPOSTOS`, `FOLHA DE PAGAMENTO` viram categorias. Linhas
  sem valor são ignoradas. A aba `ENTRADAS` do modelo é vazia e não é lida.

### Mapeamento de forma de pagamento

| Planilha | forma | conta | tipo |
|---|---|---|---|
| PIX SICOOB / STONE / SICREDI / INTER | pix | adquirente | sessao |
| DEB / CRED | débito / crédito | meio vigente na data (configurável) | sessao |
| DINH | dinheiro | Caixa | sessao |
| TED SICOOB | ted | Sicoob | sessao |
| ACORDO | acordo | n/a | acordo |

Tabela editável em `clients/<id>/config/importacao.yaml`, com **vigência por
entrada** (`vigente_de`, `vigente_ate`): a máquina de cartão usada em
DEB/CRED é a vigente na data do pagamento, o que permite a troca de
maquininha sem reescrever o histórico (spec 023). Valor desconhecido vira
pendência, nunca palpite. O repasse é calculado na importação pelas regras
vigentes na data de cada lançamento.

### Parser da coluna "data sessões"

Gramática: data única (célula de data), lista com dia e mês no fim
(`03,10,17,24/02`), listas com meses misturados
(`02,23/2,02/03,09/03`), datas com ano (`03/12/2025,21,28/01/2026`) e o
sufixo `+ JUROS DE ATRASO` (marca `tipo=juros`, valor total preservado).

- Ano inferido a partir de `data_pagamento`; virada de ano tratada.
- Cada interpretação recebe `confianca`. Abaixo de um limiar, ou com data
  futura improvável (ex.: sessão 60+ dias depois do pagamento), vai para
  `importacao_pendencia` com a sugestão.
- NF numérica vira `nf_status=emitida` com `nf_numero`; `S/NF` vira
  `sem_nf`. NF repetida em linhas distintas gera aviso, não erro.
- Paciente: casamento por nome normalizado. Nome novo cria `paciente_ref`
  com `status=a_confirmar`.

### Pendências

Tabela `importacao_pendencia` (linha, motivo, sugestão, status). Ferramentas
`listar_pendencias` e `resolver_pendencia` (spec 022: com confirmação).
Pendência não bloqueia o restante da importação.

## Exportador

- `exportar_planilha(tipo, periodo, unidade)` gera `.xlsx` em
  `data/<cliente>/exports/` com o mesmo layout das planilhas atuais.
- O relatório financeiro mensal sai com **TOTAL ENTRADAS calculado** e
  RESULTADO correto (o modelo atual não tem a fórmula de entradas).
- Exportação é auditada; arquivo não contém dado além do que o perfil pode ver.

## Dados de teste

Fixtures anonimizadas derivadas dos dois modelos (nomes genéricos, valores
sintéticos). Os `.xlsx` originais da clínica **não** entram no repositório.

## Testes previstos

- Parser: todos os formatos de exemplo da coluna de sessões, incluindo
  ambíguos e com juros.
- Reimportação é idempotente.
- Formas desconhecidas e datas improváveis geram pendência.
- Exportação reimportada produz os mesmos totais (ida e volta).
- Totais do ledger por mês batem com a soma da planilha de origem.

## Fora de escopo

Importar extratos bancários, OCR de comprovantes, escrita de volta no Drive.

## Perguntas em aberto

1. Maquininha de DEB e CRED: configuração com vigência (resolvido). No
   piloto a maioria é débito e Pix; o valor inicial é preenchido na
   implantação.
2. `.xlsx` fora do git: resolvido, `.gitignore` ignora `*.xlsx` (exceto
   fixtures sintéticas em `tests/fixtures/`).

## Red Team / Blue Team / Postmortem

| Risco (Red Team) | Controle (Blue Team) |
|---|---|
| Planilha com fórmula ou macro maliciosa | Leitura com `openpyxl` sem executar macros; só valores; `.xlsm` recusado |
| Célula com texto que parece instrução ao modelo | Importador é código determinístico, sem LLM no caminho |
| Importar a mesma planilha duas vezes | Idempotência por hash de linha |
| Parser chuta data errada e distorce o mês | Confiança mínima, pendência para o ambíguo, nunca palpite |
| Dado real de paciente no repositório | Fixtures sintéticas; `.xlsx` reais no `.gitignore` |

**Postmortem antecipado.** A importação "quase certa" é pior que a falha:
totais fecham por acaso e escondem um mês trocado. O relatório de importação
compara total por mês e por forma contra a soma da planilha de origem e
falha se houver diferença.

## God Mode / L-99

**God Mode.** O relatório de importação é panorâmico: totais por mês, forma e
unidade lado a lado com a planilha de origem, mais a lista de pendências por
motivo. Nada é aplicado sem esse diff aparecer.

**L-99.**

- O parser da coluna de sessões tem gramática formal documentada e um corpus
  crescente de casos reais anonimizados, cada um com a saída esperada.
- Teste de ida e volta (exportar e reimportar) e teste de propriedade para
  idempotência.
- Orçamento: importar 10 mil linhas em menos de 30 s.
- Runbook: reverter uma importação (por `lote_id`, via estornos), reprocessar
  pendências, mudança de layout da planilha.
