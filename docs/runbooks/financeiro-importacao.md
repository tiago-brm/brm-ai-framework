# Runbook: importação de planilhas

## Importar

1. Confira `clients/<id>/config/importacao.yaml` (formas, meios e vigências) e se
   profissionais, comissões, meios e taxas já foram cadastrados pelas notas aprovadas.
2. Dry-run: `PYTHONPATH=. uv run python scripts/importar_planilha.py arquivo.xlsx --cliente <id>`.
   Leia a conciliação (planilha, ledger, pendente, diferença) e as pendências por motivo.
3. `--aplicar` quando a conciliação estiver OK. Diferença diferente de zero reverte a
   importação e sai com código 1: investigue antes de repetir.
4. Guarde o `lote_id` do relatório.

## Reprocessar pendências

`consultar_financeiro pendencias` lista; `resolver_pendencia` com `aceitar` (e `correcao`
de forma, meio, datas ou tipo) grava a linha; `descartar` encerra sem gravar e a linha não
reaparece em reimportações. Pendência de profissional desconhecido: cadastre o profissional
e a comissão e reimporte a planilha (linhas já importadas viram "duplicadas").

## Reverter uma importação

`PYTHONPATH=. uv run python scripts/importar_planilha.py --cliente <id> --reverter-lote N`
estorna tudo do lote por contra-lançamentos (mês fechado exige reabertura antes).

## Mudança de layout da planilha

O leitor localiza o cabeçalho pelos rótulos (`DATA`, `PACIENTE`, `VALOR` ...) e o mês e a
unidade pelo texto livre do topo. Se a clínica mudar rótulos ou ordem, ajuste
`importers/leitores.py` (`COLUNAS`) e acrescente um caso em `tests/test_importador.py` com
uma fixture sintética nova. Planilha com macro (`.xlsm`) é recusada: salve como `.xlsx`.
