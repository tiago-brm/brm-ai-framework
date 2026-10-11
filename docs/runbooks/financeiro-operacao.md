# Runbook: operação do ledger financeiro

Banco: `data/<cliente>/financeiro.sqlite`. Nunca edite o arquivo à mão: gatilhos recusam,
e o hash do fechamento denuncia a alteração.

## Verificar a saúde

Peça ao gestor `verificar_integridade` (consulta de `consultar_financeiro`) ou
`painel_financeiro`. Qualquer violação lista o invariante (F1 a F10) e o id do registro. Sem
violação, o mês pode ser fechado. `fechar_mes` recusa enquanto houver violação.

## Fechar e reabrir um mês

1. Concilie com a planilha de conferência: total por forma e por unidade, divergência
   explicada por escrito.
2. Resolva ou aceite as pendências abertas (`pendencias`).
3. `fechar_mes` (A3): o gestor propõe e outro perfil aprova. Só meses anteriores ao corrente.
4. Reabrir é exceção do `super_admin`, com motivo, em `reabrir_mes` (A2, regra de papel). O
   histórico (`fechado`, `reaberto`) fica no `fechamento_mensal`; refaça o fechamento depois.

## Corrigir um lançamento

Nada se edita. Use `estornar_lancamento` (aprovação do gestor) e lance de novo. Em mês
fechado, o estorno e o novo lançamento entram no mês corrente. Comprovante e status de NF
podem ser alterados por `anexar_comprovante` e `atualizar_status_nf`.

## Corrigir paciente duplicado

O ledger não funde pacientes. Quando a Agenda unificar o cadastro, o sincronizador atualiza
`paciente_ref.external_ref`. Para duplicidade já lançada: estorne os lançamentos do cadastro
errado e relance no correto (o estorno exige o mesmo paciente do original). Pacientes
`a_confirmar` vêm da importação; confirme o nome e o vínculo com o gestor.

## Comissão, taxa, maquininha e pacote

Mudam só por nota de configuração aprovada (spec 030), que chama as funções
`materializar_*`. Nunca por SQL. Troca de maquininha: nota `meio` com `substitui`, depois a
nota das taxas do meio novo; ajuste o `importacao.yaml` com a vigência.

## Backup e restauração

O ledger entra no backup de `docs/runbooks/backup-e-restauracao.md` (cópia consistente do
SQLite em WAL, com `.backup`, nunca copiando só o `.sqlite`). Depois de restaurar: rode
`verificar_integridade`; se algum mês fechado acusar F5, o backup é anterior ao fechamento
ou o arquivo foi alterado; escolha o backup mais recente que passe.
