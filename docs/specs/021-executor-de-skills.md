# 021 — Executor de skills: handlers nativos, conectores HTTP e SecretStore (spec)

**Status:** proposta, aguardando aprovação (2026-10-09).

**Origem:** hoje `call_skill` devolve um mock (`{"status":"ok",...}`); o único
efeito real é o SMTP. Sem executor, o BRM governa mas não faz. Esta spec
substitui o mock por um executor com três tipos e preserva a demo.

## Bloco `executor` no `skills.yaml`

```yaml
- name: registrar_recebimento
  executor:
    type: native                  # native | http | mock
    handler: financeiro.registrar_recebimento
- name: agenda_criar_agendamento
  executor:
    type: http
    method: POST
    url: "${secret:agenda_base_url}/v1/agendamentos"
    headers: { Authorization: "Bearer ${secret:agenda_token}" }
    timeout_s: 10
    mapeamento_resposta: { id: "$.data.id" }
- name: enviar_lembrete_sessao
  executor: { type: mock }        # padrão quando o bloco não existe
```

- Sem bloco `executor`, o tipo é `mock` (clientes e demos atuais seguem iguais).
- `native` resolve o handler num registro Python populado pelos módulos
  (spec 027). Handler recebe argumentos já validados e o `Actor`.
- `http` usa `httpx`, valida o host contra a allowlist do interceptor
  (e o bloqueio de IP privado, com exceção explícita por host de rede
  interna em `client_config.yaml: hosts_internos`).

## Pipeline

```
validar (Pydantic) → DLP entrada → fatos derivados → regras
   → [operação: proposta/confirmação, spec 022] → executor
   → DLP saída → auditoria
```

- Simulação nunca chega ao executor.
- `idempotency_key` obrigatória para skills com efeito. Repetir a mesma
  chave devolve o resultado anterior sem executar de novo.
- Retry só em erros de rede e apenas para skills marcadas `idempotente: true`.
  Timeout e erro HTTP viram `ExecucaoFalhou` estruturado, sem expor
  segredo nem corpo bruto.

## SecretStore

- Interface `SecretStore.get(nome)`; referências `${secret:nome}` resolvidas
  só no momento da execução.
- Backends da Fase 1: variáveis de ambiente e Docker secrets (arquivos em
  `/run/secrets`), sem formato próprio de arquivo. A interface permite um
  backend de nuvem depois (spec 028).
- Segredos nunca entram em log, auditoria nem resposta da ferramenta.
- Suporta valores binários (certificado A1 da Fase 2) como arquivo referenciado.

## Compatibilidade

`enviar_email` continua como handler `native` com a lógica de
`email_sender.py`. O hook `skill.vault_read` vira handler nativo opcional.

## Testes previstos

- Skill sem bloco `executor` mantém o mock.
- Handler nativo chamado com argumentos validados; argumento inválido é
  recusado antes.
- HTTP com `httpx.MockTransport`: sucesso, 4xx, 5xx, timeout, host fora da
  allowlist bloqueado.
- Mesma `idempotency_key` não executa duas vezes.
- Segredo não aparece em auditoria, erro nem log.
- Simulação não aciona handler nem HTTP.

## Fora de escopo

Filas assíncronas, jobs agendados, circuit breaker avançado.

## Perguntas em aberto

1. Skills `http` devem poder chamar o MCP da Agenda diretamente (cliente
   MCP) além de HTTP? Decisão na spec 026 após inspecionar o `agendapro4`.

## Red Team / Blue Team / Postmortem

| Risco (Red Team) | Controle (Blue Team) |
|---|---|
| SSRF: skill HTTP apontada para rede interna ou metadados de nuvem | Allowlist de host mais bloqueio de IP privado; exceção só por `hosts_internos` explícito |
| Redirecionamento 30x para host fora da lista | Redirects desligados por padrão; se ligados, revalidar cada salto |
| Resposta externa com instrução para o modelo | Resposta é dado: passa por DLP e mapeamento, e a regra não depende dela |
| Execução duplicada por retry do modelo ou da rede | `idempotency_key` e retry só em skills idempotentes |
| Segredo em mensagem de erro | Erros estruturados e sanitizados |

**Postmortem antecipado.** O risco real é o handler nativo ter acesso
irrestrito ao banco. Handlers recebem uma sessão de dados limitada ao módulo
e o `Actor`; nenhum handler abre conexão própria.

## God Mode / L-99

**God Mode.**

- Cada estágio do pipeline (validação, DLP, fatos, regras, executor, DLP de
  saída) acrescenta uma entrada ao `trace` da chamada; o simulador e a
  auditoria mostram o caminho completo e onde ele parou.

**L-99.**

- Invariante: o executor só aceita um objeto `ChamadaAutorizada`, produzido
  pelo pipeline após regras e DLP. Não existe caminho de código que chame um
  handler sem ele (garantido por construção e por teste).
- Orçamento: timeout por skill, teto de tamanho de resposta, limite de
  chamadas por minuto por usuário.
- Falha parcial definida: executou mas não gravou a auditoria final é
  tratado por reconciliação pela `idempotency_key`, nunca por reexecução.
- Runbook: conector fora do ar, segredo expirado, resposta fora do contrato.
