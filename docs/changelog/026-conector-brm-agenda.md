# 026 — Conector BRM Agenda (Fase 1: leitura e sincronização)

**O quê:** implementa a Fase 1 da spec `docs/specs/026-conector-brm-agenda.md` como o módulo de
catálogo `agenda` (`mcp_engine/modules/agenda/`). Somente leitura na Agenda; o motor grava no ledger
do financeiro e nunca na Agenda.

## Entregas

- **Provider** (`provider.py`): contrato `AgendaProvider` (listar agendamentos, serviços,
  profissionais, buscar clientes) e `HttpAgendaProvider` contra o contrato de leitura da spec.
  Sem método de escrita. Segurança: https obrigatório, host dentro dos domínios do manifesto,
  sem credenciais na URL, sem seguir redirecionamento, timeout, resposta limitada a 5 MB (também
  sem `Content-Length`), 4 tentativas com recuo em 429 (`Retry-After`, teto 30 s) e 5xx, 401 sem
  repetir, mensagens fixas sem URL nem chave. `FakeAgendaProvider` (`fake.py`) para testes.
- **Skills** (`skills.yaml`): `agenda_sessoes`, `agenda_clientes`, `agenda_servicos`,
  `agenda_sessoes_sem_pagamento` (A0) e `sincronizar_agenda` (A1). Quem não é `admin` nem
  `super_admin` recebe agregados sem nomes. `agenda_sessoes_sem_pagamento` lê só o ledger (cruzamento
  agenda x financeiro) e continua funcionando com a Agenda fora do ar.
- **Sincronização** (`sincronizacao.py`): `sincronizar(host, provider)` e
  `executar_sincronizacao(host)`. Busca fora de qualquer trava, depois aplica tudo numa transação do
  ledger; só então confirma o estado em `data/<cliente>/agenda.sqlite` (cursor `updated_at`,
  agendamentos vistos e espelho de clientes). Falha em qualquer ponto não avança o cursor.
- **API entre módulos:** `ModuleHost.module_api(nome)` e `BaseModule.module_api(host)` em
  `modules/__init__.py` (aditivos); o financeiro expõe `FinanceiroAPI` (`financeiro/api.py`) com
  `transacao()` (fachada `LedgerAgenda`) e `sessoes_realizadas_sem_pagamento`. `ModuleHost` ganhou o
  campo `modulos`. O módulo `agenda` falha na subida se `financeiro` não estiver habilitado.
- **Gatilho:** função `executar_sincronizacao(host)` (devolve `{"ok": ...}`, audita `agenda.sincronizar`),
  comando manual `scripts/sincronizar_agenda.py` e nota modelo
  `modules/agenda/defaults/gatilho-sincronizar-agenda.md`, a ativar quando o agendador existir.
- **Cliente piloto:** `clients/clinica-piloto` habilita `agenda`, permite o domínio
  `agenda.brmsolutions.com.br` e adota as skills por `usar:`. Sem credenciais: falta configurar os
  segredos `agenda_api_key` e `agenda_base_url` (ver `docs/runbooks/agenda-operacao.md`).

## Decisões e desvios

- URL base como segredo `agenda_base_url` (única configuração por cliente acessível ao módulo).
- Domínio requerido do manifesto (`agenda.brmsolutions.com.br`) é provisório: confirmar o host real.
- `sessao_sem_desfecho` descartada por gestor volta na próxima sincronização enquanto a sessão
  continuar sem desfecho (comportamento da função do financeiro).
- Duplicidade de cliente só é avaliada quando o cliente é novo ou muda de nome ou telefone, para
  não recriar pendência resolvida.
- Sem aviso de divergência de valor previsto e sem reconciliação semanal (próximas etapas).

## Testes

`tests/test_agenda.py` (sincronização, idempotência, cursor após reinício, falhas atômicas,
pendências, duplicidade, conflito, valor previsto, cobrança por sessão, leitura por perfil, fuso,
Agenda fora do ar, segredos fora de log e auditoria, orçamento de 2 s, comando manual, motor) e
`tests/test_agenda_provider.py` (HTTP com `MockTransport`: Bearer, parâmetros, paginação, 401, 429,
5xx, timeout, redirecionamento, resposta gigante, contrato inválido, URL insegura, fuso e horário
de verão).
