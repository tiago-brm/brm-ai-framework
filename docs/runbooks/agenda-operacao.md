# Runbook: operação da integração com a Agenda

Escopo: Fase 1, somente leitura (spec 026). O motor lê a Agenda e grava no ledger; nunca escreve na
Agenda.

## Configuração

Dois segredos por cliente, no SecretStore (variável `BRM_SECRET_<CLIENTE>_<NOME>` ou arquivo
`/run/secrets/<cliente>/<nome>`); nunca no `.env` versionado nem no repositório:

| Segredo | Conteúdo |
|---|---|
| `agenda_api_key` | chave de integração **somente leitura**, escopada ao tenant do cliente |
| `agenda_base_url` | URL https da API da Agenda (host precisa estar na `domain_allowlist`) |

O motor avisa na subida se algum segredo faltar. A sincronização manual:
`CLIENT_ID=<cliente> uv run python scripts/sincronizar_agenda.py`.

## Chave somente leitura

- Peça ao dono da Agenda uma chave de integração de leitura para o tenant do cliente. Chave com
  poder de escrita ou de outro tenant deve ser recusada.
- O harness só usa `GET`; a chave nunca aparece em log, auditoria nem mensagem de erro.
- Cada cliente tem a própria chave; nunca reutilize entre clientes.

## Rotação da chave

1. Gere a nova chave na Agenda (a antiga continua valendo).
2. Atualize `agenda_api_key` no SecretStore do cliente e reinicie o motor (os segredos são lidos a
   cada chamada, mas reiniciar garante a conferência na subida).
3. Rode a sincronização manual e confirme `"ok": true`.
4. Revogue a chave antiga na Agenda.

Se a Agenda recusar a chave (401), as ferramentas respondem "A Agenda recusou a chave de integração":
a chave expirou, foi revogada ou é de outro tenant. Faça a rotação acima.

## Se a Agenda cair

- `agenda_sessoes`, `agenda_clientes`, `agenda_servicos` e `sincronizar_agenda` devolvem erro claro
  ("A Agenda está indisponível no momento", "limitou as consultas" ou "não respondeu a tempo"), sem
  URL nem chave. Nada é gravado e o cursor não avança.
- O **financeiro segue normal**: lançamentos, consultas, fechamento e `agenda_sessoes_sem_pagamento`
  usam só o ledger. Os dados da Agenda ficam defasados até a próxima sincronização;
  `ultima_sincronizacao` na resposta mostra a defasagem.
- Quando a Agenda voltar, a próxima sincronização recupera tudo pelo cursor `updated_at`, sem
  perder nem duplicar. Para forçar: `scripts/sincronizar_agenda.py`.

## Divergências

- **Sessão sem desfecho:** a recepção marca como concluída na Agenda ou registra a falta no ledger;
  a pendência se encerra sozinha na sincronização seguinte.
- **Conflito concluída x falta:** o gestor decide (`resolver_pendencia`); corrija a Agenda ou estorne a
  falta.
- **Possível duplicidade de cliente:** una os cadastros na Agenda. O ledger nunca funde sozinho.
- **Cliente desconhecido em lançamento:** vira pendência; cadastre o paciente na Agenda primeiro.
- **Cursor suspeito:** apague as linhas `cursor_atualizado_em` de `estado` e a tabela
  `agendamento_visto` em `data/<cliente>/agenda.sqlite` (faça cópia antes); a próxima sincronização
  relê os últimos 90 dias e é idempotente.
