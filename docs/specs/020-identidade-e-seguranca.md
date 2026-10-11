# 020 — Identidade por pessoa, RBAC default deny e DLP de entrada (spec)

**Status:** proposta, aguardando aprovação (2026-10-09).

**Origem:** análise de prontidão do AIRP (spec 019). Pontos encontrados no
código atual:

- a auditoria registra o agente (`claude`), não a pessoa;
- ferramenta sem linha em `tool_permissions` fica aberta a todos;
- tokens de usuário ficam em texto puro em `users.db`;
- CORS é `*`;
- o DLP varre só a resposta, não os argumentos da requisição;
- a escrita em `audit.jsonl` não tem trava.

## Identidade nos eventos

- `Actor`: `user_id`, `email`, `role`, `via` (`stdio`, `http`, `simulacao`).
- Todo evento de auditoria ganha `user_id`, `user_email` e `role`, mantendo
  `agent: claude`. `schema_version` do evento sobe; a verificação da cadeia
  aceita linhas antigas (sem os campos novos).
- Simulação continua gravando `executado_por` e `como`.
- Credencial desconhecida deixa de virar viewer anônimo com acesso a
  ferramentas: vira `anonimo`, que só enxerga `ping` (se existir) e nada mais.

## Default deny

- Ferramenta sem permissão explícita para o papel é negada, exceto para
  `super_admin`.
- `scripts/migrar_permissoes.py` cria as linhas que preservam o
  comportamento atual de cada cliente, para a troca não quebrar a demo.
- `client_config.yaml: rbac_default: deny` (padrão). `allow` existe só
  para clientes legados e emite aviso na inicialização.

## Tokens

- Novos tokens: `secrets.token_urlsafe(32)`, guardados como SHA-256, exibidos
  uma única vez na criação.
- Comparação em tempo constante. Endpoint `POST /api/usuarios/{id}/rotacionar`.
- Migração: na primeira inicialização, tokens em texto puro são convertidos
  para hash e o valor original deixa de existir.

## CORS

`client_config.yaml: cors_origins: [...]`. Padrão: origens locais do painel.
`*` só com `cors_permitir_qualquer: true` e aviso no log.

## DLP de entrada

- `interceptor.varrer_requisicao(argumentos)` aplica os mesmos padrões do
  DLP de saída aos argumentos string (recursivo).
- `deny` bloqueia antes de qualquer regra com efeito; `mask` substitui o valor
  antes da execução.
- Auditoria registra tipo e quantidade de ocorrências e o digest do trecho,
  nunca o valor.
- Os demais campos tipados (valor, data, enumerações) já são validados pelo
  Pydantic e não passam pelo DLP.

## Auditoria

- Trava de arquivo (`fcntl`/`msvcrt`) ao anexar, para manter a cadeia íntegra
  com mais de um processo ou thread.
- `query_audit_log` ganha filtro por `user_email`.

## Testes previstos

- Evento traz `user_id` e `role` em chamada permitida, negada e simulada.
- Ferramenta sem permissão: negada para viewer e admin, permitida para
  super_admin; `rbac_default: allow` mantém o legado com aviso.
- Token em texto puro é migrado para hash e continua autenticando.
- CORS não devolve `*` por padrão.
- CPF em argumento é bloqueado; CID em argumento é mascarado antes da execução.
- Escritas concorrentes na auditoria mantêm a cadeia verificável.

## Fora de escopo

SSO/OIDC, MFA e múltiplos super_admin simultâneos.

## Perguntas em aberto

1. `anonimo` deve existir ou a conexão sem credencial deve ser recusada?
   Recomendação: recusar no HTTP e manter `anonimo` só no modo stdio de demo.

## Red Team / Blue Team / Postmortem

| Risco (Red Team) | Controle (Blue Team) |
|---|---|
| Token roubado do `users.db` ou de backup | Só o hash fica no banco; rotação por endpoint |
| Site malicioso no navegador do gestor chama o painel | CORS restrito; rotas `/api/*` exigem token explícito, não cookie |
| Argumento com CPF ou CID mascarado por engano na saída, mas vazado na entrada | DLP de entrada antes da execução |
| Adulteração do `audit.jsonl` | Cadeia de hash verificada na leitura; trava de escrita |
| Escalada: viewer chama ferramenta recém-criada sem permissão | Default deny |

**Postmortem antecipado.** A troca para default deny pode derrubar a demo
no dia da reunião; por isso a migração semeia as permissões atuais e o
`rbac_default: allow` legado existe com aviso. Teste de regressão roda a
`preflight_demo.py` completa.

## God Mode / L-99

**God Mode.**

- O bypass do `super_admin` vira **break-glass**: continua permitido, mas
  todo uso fora das permissões grava o evento `break_glass` com `motivo`
  obrigatório e aparece em destaque no painel.
- `trace_id` gerado na entrada de cada requisição e gravado em todos os
  eventos, de modo que `query_audit_log(trace_id=...)` reconstrói um pedido
  inteiro, da identidade ao resultado.

**L-99.**

- Invariantes: (I1) nenhum evento sem `actor`; (I2) a cadeia de hash é
  verificável a qualquer momento; (I3) nenhuma ação com efeito ocorre sem o
  evento correspondente gravado.
- Falha em fechado: se a auditoria não consegue gravar (disco cheio,
  trava), **a ação não executa**.
- Testes de propriedade para a cadeia (qualquer adulteração é detectada) e
  para a ordem dos eventos sob concorrência.
- Runbook: rotação de token, recuperação de cadeia quebrada, perda de acesso
  do único `super_admin`.
