# 026 — Conector BRM Agenda governado (spec)

**Status:** Fase 1 (leitura e sincronização) implementada no harness (2026-10-09), validada contra o
contrato abaixo com `httpx.MockTransport`. Depende de o `agendapro4` expor o endpoint de leitura (outro
agente). Ver `docs/changelog/026-conector-brm-agenda.md` e `docs/runbooks/agenda-operacao.md`.

**Origem:** hoje o Claude Desktop fala direto com o MCP da Agenda
(`agendapro4/apps/mcp`), sem regras, aprovação nem auditoria do BRM. O
roteiro da demo registra isso como limitação. Esta spec coloca a Agenda atrás
do BRM.

## Tarefa 0: descoberta (concluída em 2026-10-09)

Leitura somente leitura do `agendapro4` (`/Users/tiagopereiraramos/Dev/01-Ativos/agendapro4`):

- **Stack:** monorepo pnpm em TypeScript. Banco: Supabase self-hosted (Postgres,
  PostgREST, GoTrue) no Easypanel, **remoto**, schema `agenda_pro`, com RLS
  por `tenant_id`. Apps: `web` (React), `worker` (Express, REST, webhooks,
  cron), `saf` (chatbot), `mcp`.
- **Entidades úteis:** `customers` (chave por telefone, sem `external_id`),
  `staff`, `services`, `appointments` (`scheduled_at` em UTC, `status` em
  `PENDING_CONFIRMATION`, `WAITING`, `CONFIRMED`, `IN_PROGRESS`, `COMPLETED`,
  `CANCELLED`). Fuso por estabelecimento (`establishments.timezone`).
- **Não existe:** pacote de sessões, saldo, pagamento de paciente, status de
  falta (no-show), `completed_at`, página de pacotes.
- **Superfícies de integração:**
  - MCP (stdio, um tenant fixo por variável de ambiente, `service role`
    direto no banco, sem tool de cliente, cancelar ou remarcar, sem checagem
    de transição de status).
  - Edge function `public-api` (`x-api-key`): criar, cancelar e remarcar
    agendamento, cliente por telefone; limite de 5 por telefone por hora; não
    li verificação de conflito de horário.
  - Worker `/api/v1/customers` (Bearer com chave do estabelecimento), com
    busca e paginação.
  - Sem endpoint de leitura por período; sem webhooks de saída de agendamento.
- **Ambiente:** um único Supabase remoto. Não achei staging separado; a demo
  usa um tenant fictício no mesmo banco (confirmar com o dono).

### Escopo da Fase 1: leitura e sincronização (revisado em 2026-10-09)

Decisão do usuário: a recepção cadastra **paciente e agendamento no front da
Agenda**, e a Agenda não recebe pagamentos. Portanto a Fase 1 é **somente
leitura**: o motor lê a Agenda e grava no ledger; não escreve na Agenda.
Criar, cancelar e remarcar pelo harness ficam para a Fase 2.

**Transporte:** HTTP, por uma API de leitura servidor-a-servidor no `worker`
do `agendapro4` (já tem chave por estabelecimento e escopo de tenant). Nada
de credencial de serviço direta do harness (ignora o RLS). Mudança mínima e
só de leitura no `agendapro4`, que depende de aprovação do dono:

1. `GET /api/v1/appointments?from&to&status&staff_id&updated_since`, com
   paginação e escopo do tenant.
2. Leitura de clientes e serviços (com preço): já existem
   (`/api/v1/customers`, serviços), conferir o formato.

Plano de contingência se a mudança atrasar: consulta por dia pelo MCP
existente (`listar_agendamentos` por data), com mais chamadas.

### Contrato da Fase 1

| Operação | Ferramenta |
|---|---|
| Sessões por período, status e atualização | `agenda_sessoes` |
| Clientes (busca) | `agenda_clientes` |
| Serviços e preços | `agenda_servicos` |
| Sincronização | gatilho `sincronizar-agenda` (spec 031), A1, a cada poucos minutos, com cursor por `updated_at` em `state.db` |

### Regras de sincronização

- **O paciente nasce na Agenda.** O ledger cria o `paciente_ref` sob demanda
  (`external_ref = customers.id`) ao ver um agendamento de cliente ainda sem
  referência, e atualiza o nome a cada sincronização. Lançamento para quem a
  Agenda não conhece vira pendência ("cadastre na Agenda primeiro"), nunca
  cadastro novo.
- Cada agendamento vira uma linha em `sessao` (spec 023), por `agenda_id`,
  idempotente. Status: `COMPLETED` = realizada; `CANCELLED` = cancelada;
  demais = agendada.
- **Fonte única por desfecho:** *realizada* vem só da Agenda; *falta* é
  registrada no ledger (spec 023) enquanto a Agenda não tiver esse status.
  Quando a Agenda ganhar o status de falta, o sincronizador o mapeia e
  divergência entre os dois vira pendência.
- **Disciplina da recepção:** sessão cuja hora já passou e que ainda não
  está concluída, cancelada nem com falta registrada vira a pendência
  `sessao_sem_desfecho`. A mensagem orienta: "marque como concluída na
  Agenda ou registre a falta aqui".
- Possível duplicidade de cliente (mesmo nome normalizado com telefones
  diferentes) gera pendência, não fusão automática.

### Contrato do endpoint de leitura (acordado em 2026-10-09)

Implementado do lado do `agendapro4` por outro agente; o harness o consome por `AgendaProvider`.

- **Auth:** `Authorization: Bearer <chave de integração somente leitura>`; o tenant é implícito
  pela chave; a URL base vem do segredo `agenda_base_url`.
- `GET /api/v1/appointments?from=<ISO8601>&to=<ISO8601>&status=A,B&staff_id=<uuid>&updated_since=<ISO8601>&limit=<=200>&cursor=<opaco>`
  devolve `{"data":[{"id","customer_id","customer_name","customer_phone","staff_id","staff_name","service_id","service_name","service_price","duration_minutes","scheduled_at","status","updated_at","source"}],"next_cursor":"..."|null}`.
  `service_price` é número em reais; `scheduled_at` e `updated_at` são ISO em UTC; `status` é um de
  `PENDING_CONFIRMATION`, `WAITING`, `CONFIRMED`, `IN_PROGRESS`, `COMPLETED`, `CANCELLED`.
- `GET /api/v1/services` devolve `{"data":[{"id","name","price","duration_minutes","available"}]}`.
- `GET /api/v1/staff` devolve `{"data":[{"id","name","role","available"}]}`.
- `GET /api/v1/customers?search=&limit=&offset=` já existe (o provider aceita `data` ou `customers`).
- **Erros:** 401 chave inválida (sem nova tentativa); 429 limite (respeita `Retry-After`, com teto de
  30 s); 5xx (nova tentativa com recuo exponencial, 4 tentativas).

### O que ficou implementado (Fase 1)

Módulo de catálogo `agenda` (`mcp_engine/modules/agenda/`), exige o módulo `financeiro`:

- `AgendaProvider` (só métodos de leitura), `HttpAgendaProvider` (timeout, paginação por cursor,
  recuo em 429 e 5xx, TLS obrigatório, host na lista de domínios do manifesto, sem seguir
  redirecionamento, resposta limitada a 5 MB, erros saneados sem URL nem chave) e
  `FakeAgendaProvider` para testes.
- Skills nativas: `agenda_sessoes`, `agenda_clientes`, `agenda_servicos` e
  `agenda_sessoes_sem_pagamento` (todas A0, somente leitura; perfis que não são `admin` nem
  `super_admin` recebem agregados sem nomes) e `sincronizar_agenda` (A1).
- `executar_sincronizacao(host)` (entrada do gatilho `sincronizar-agenda`, spec 031), o comando
  `scripts/sincronizar_agenda.py` e a nota modelo `defaults/gatilho-sincronizar-agenda.md`. O
  agendador não é deste módulo.
- Sincronização idempotente por `agenda_id`, com cursor por `updated_at` em `agenda.sqlite` (margem
  de 5 min; agendamento já visto com o mesmo `updated_at` é ignorado). Grava no ledger só pela API
  entre módulos (`ModuleHost.module_api("financeiro")`); o módulo nunca abre o banco do
  financeiro.
- Valor previsto da sessão: pacote, depois valor acordado do paciente, depois preço do serviço.
- Pendências: `sessao_sem_desfecho` (aparece e é encerrada sozinha quando a sessão ganha desfecho),
  `conflito_desfecho` (já gerada pelo ledger) e `possivel_duplicidade_cliente` (sem fusão; o
  telefone só é guardado como hash em `agenda.sqlite`).

Desvios da spec: a URL base vem do SecretStore (`agenda_base_url`), por ser a única configuração por
cliente que o módulo enxerga; o domínio requerido no manifesto é `agenda.brmsolutions.com.br`
(confirmar o host real da Agenda e ajustar o manifesto). O estado fica em `agenda.sqlite`, não em
`state.db`. Fora desta fase: aviso de divergência entre os três valores previsto, reconciliação
semanal por contagem e o agendador.

### Segurança

Agenda remota: só TLS, host na allowlist, chave de integração **somente
leitura** no SecretStore, tenant fixado por configuração. Testes do harness
usam apenas o tenant fictício.

## Red Team / Blue Team / Postmortem

| Risco (Red Team) | Controle (Blue Team) |
|---|---|
| Chave de integração com poder de escrita ou de outro tenant | Chave só de leitura, escopada ao tenant, no SecretStore; rotacionável |
| Sincronização duplicada ou atrasada | Idempotência por `agenda_id` e cursor de atualização |
| Dado clínico copiado para o ledger | Espelho com campos mínimos (ids, datas, status, valor); nada de notas |
| Cliente duplicado vira dois pacientes | Pendência de possível duplicidade; nunca fusão automática |
| Registro de falta usado para esconder sessão realizada | Conflito entre Agenda (concluída) e falta no ledger vira pendência e exige gestor |

**Postmortem antecipado.** Ledger e Agenda divergem sem ninguém notar. A
Agenda é a fonte de agenda; o ledger é um espelho mínimo. Reconciliação
semanal por contagem de sessões por profissional e período.

## Testes previstos

- Sincronização cria `paciente_ref` e `sessao` e não duplica ao repetir.
- Cursor retoma depois de reinício sem perder nem repetir.
- Cliente desconhecido em lançamento vira pendência.
- `sessao_sem_desfecho` aparece após o limite e some quando resolvida.
- Conflito Agenda concluída × falta no ledger vira pendência.
- Chave só de leitura: tentativa de escrita falha.
- Segredo ausente de log e auditoria.

## Fora de escopo

Fase 2: criar, cancelar e remarcar pelo harness, chatbot de triagem por
WhatsApp, lembretes automáticos e pagamento no agendamento.

## God Mode / L-99

**God Mode.** Visão cruzada agenda × financeiro, somente leitura:
`agenda_sessoes_sem_pagamento` lista sessões realizadas sem recebimento
correspondente no período. É a prévia dos contas a receber da Fase 2, sem
criar obrigação alguma.

**L-99.**

- Casos de borda: horário de verão, virada de dia, cliente com telefone
  alterado, agendamento reagendado entre duas sincronizações.
- Orçamento: sincronização de um dia de agenda em menos de 2 s; timeout e erro explícitos.
- Runbook: Agenda fora do ar (ferramentas de agenda devolvem erro claro, o
  financeiro segue), token expirado, divergência de cadastro.
