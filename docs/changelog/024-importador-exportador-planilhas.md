# 024 — Importador e exportador de planilhas

**O quê:** implementa a spec `docs/specs/024-importador-exportador-planilhas.md` em
`mcp_engine/modules/financeiro/importers/` e `scripts/importar_planilha.py`.

## Recursos verificados (regra L-99)

- `openpyxl` 3.1.5 (estável corrente), adicionado com `uv add openpyxl` (`pyproject.toml` e
  `uv.lock`). Leitura com `read_only=True`, `data_only=True` e `keep_vba=False`: só valores,
  nenhuma fórmula é avaliada e nenhuma macro executa.
- Os `.xlsx` reais em `docs/apresentacao-clinica/` foram lidos apenas para entender o layout;
  as fixtures de `tests/fixtures/` são sintéticas (nomes genéricos), geradas por
  `tests/fixtures/sinteticos.py`.

## Importador

- `importar(db, arquivo, opcoes)` roda tudo numa transação do ledger (as mesmas regras,
  gatilhos e repasse da gravação real). **Dry-run por padrão**: a transação é revertida e o
  relatório sai igual. `--aplicar` confirma, mas só se a conciliação fechar.
- Layouts detectados pelo conteúdo: `secretarias` (cabeçalho livre com secretária, mês e
  unidade; colunas por rótulo) e `financeiro` (aba `SAÍDAS`, blocos viram `grupo` de
  `categoria_despesa`; linhas sem valor e subtotais são ignorados). `.xlsm`, macro VBA
  (`vbaProject`), arquivo acima de 20 MB e não-zip são recusados.
- Mapeamento de forma em `clients/<id>/config/importacao.yaml` (`formas` e
  `despesas_formas`, com `vigente_de`/`vigente_ate` por entrada, e `confianca_minima`).
  Padrões embutidos seguem a tabela da spec. Meio indisponível na data, forma sem mapeamento,
  profissional desconhecido e valor ambíguo viram pendência, nunca palpite.
- Parser da coluna "data sessões" (`sessoes_parser.py`): gramática EBNF e tabela de confiança
  no próprio módulo; corpus em `tests/test_importador.py` (casos reais do modelo e bordas).
- Pendências na tabela `pendencia_operacional` (tipos `importacao_*`, com `lote_id`, aba,
  linha, motivo, sugestão e o lançamento proposto). A spec nomeava `importacao_pendencia`:
  unificada com a pendência operacional da spec 023, para uma só fila e uma só skill
  (`resolver_pendencia`: `aceitar` com correção opcional de forma, meio, datas e tipo;
  `descartar`, que também impede a pendência de reaparecer na reimportação).
  Observação que sugere estorno gera `importacao_possivel_estorno`, que ao ser aceita estorna
  o lançamento.
- Paciente novo vira `paciente_ref` com `status=a_confirmar`. NF numérica vira `emitida`,
  `S/NF` vira `sem_nf`, vazio `pendente`; NF repetida gera aviso. Soma com juros
  (`+ JUROS DE ATRASO`) é gravada como `tipo=juros`, valor total preservado e **sem repasse**
  (divisão entre principal e juros é decisão do gestor).
- **Idempotência (desvio da spec):** a chave é o SHA-256 do layout, aba, cabeçalho
  (secretária, mês, unidade), conteúdo normalizado da linha e o número da ocorrência entre
  linhas idênticas, sem o hash do arquivo e sem o número da linha. Assim, reimportar a mesma
  planilha ou uma versão com linhas acrescentadas não duplica, e dois pagamentos idênticos e
  legítimos não se perdem. O hash do arquivo fica em `importacao_lote`.
- **Conciliação que falha:** o relatório compara, por mês, forma (ou grupo) e unidade, a
  soma da planilha com a soma recalculada no banco (inseridas e já existentes) mais a soma
  das pendências. Diferença diferente de zero marca `ok: false`, o `--aplicar` é revertido
  (`modo: revertido`) e o CLI sai com código 1. `--exigir-sem-pendencia` endurece.
- `reverter_lote(db, lote_id)` e `--reverter-lote N` estornam o que um lote importou.
- CLI: `PYTHONPATH=. uv run python scripts/importar_planilha.py arquivo.xlsx --cliente <id>`
  (opções `--aplicar`, `--mes`, `--layout`, `--json`, `--por`).

## Exportador

- `exportar_planilha(tipo, periodo, unidade)` (skill A1) grava em
  `data/<cliente>/exports/`. `secretarias`: mesmo layout (a unidade é obrigatória),
  estornos como linhas negativas com observação, sessões como data única ou lista completa.
  `financeiro`: abas `ENTRADAS` (por forma e unidade) e `SAÍDAS` (blocos, subtotais),
  com **TOTAL ENTRADAS, TOTAL SAÍDAS e RESULTADO calculados** pelo motor e gravados como
  valores (não fórmulas), para qualquer leitor ver os mesmos números.
- Perfil que não é gestor recebe `PAC-<id>` no lugar do nome do paciente.
- Ida e volta: a exportação reimportada em outro ledger produz os mesmos totais por mês e
  forma (teste). Com estornos, a exportação soma o ledger, mas a reimportação deixa as linhas
  negativas como pendência (estorno é feito no ledger).

## Testes

`tests/test_importador.py` (parser, dry-run, conciliação, idempotência, pendências, vigência de
maquininha, ida e volta, planilhas hostis, 10 mil linhas em segundos, CLI, reversão de lote,
ausência de `.xlsx` real no repositório) e `tests/test_importador_propriedades.py`
(Hypothesis: parser nunca levanta exceção, ida e volta de datas, idempotência e soma).

## Pendências

- Importar despesa pendente exige corrigir a planilha e reimportar (a aceitação por
  pendência cobre só recebimentos).
- Crédito entra como 1 parcela (aviso no relatório); a planilha não traz o parcelamento.
- A skill de importação pelo chat não existe: importar é operação de implantação via CLI.
