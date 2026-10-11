# 031 — Gatilhos e automações declarativas (spec)

**Status:** proposta, aguardando aprovação (2026-10-09).

**Origem:** pedido do usuário (2026-10-09): o sistema precisa de gatilhos
para tudo. Hoje tudo é chamada sob demanda. Gatilhos são notas `tipo: gatilho`
(spec 030), aprovadas e executadas pelo motor.

## Modelo

```brm-config
id: nf-pendente-diario
quando: { agenda: "0 18 * * 1-5" }          # ou evento
condicao: { ">": [{ "var": "qtd_sem_nf" }, 0] }
acao:
  skill: consultar_financeiro
  argumentos: { consulta: sem_nf, periodo: mes_atual }
  saida: alerta
autonomia: A0
limites: { max_por_hora: 1 }
```

- **Gatilho por agenda** (cron) e **por evento** (`recebimento.registrado`,
  `mes.proximo_do_fim`, `operacao.pendente_ha_horas`...).
- Condição em JSONLogic (mesmo avaliador sandboxado).
- Ação é uma **skill existente**, nunca código livre.
- Saídas: nota em `sistema/alertas.md`, painel, e-mail (SMTP já real). O
  WhatsApp real é Fase 2.

## Execução

- Ator de sistema `sistema:<gatilho_id>`, com conjunto mínimo de permissões
  declarado no gatilho e conferido pelo RBAC como qualquer usuário.
- **Gatilho só executa A0/A1.** Se a ação for A2/A3, o gatilho cria uma
  **operação proposta** (spec 022) para uma pessoa confirmar ou aprovar.
- Agendador dentro do motor, com próxima execução persistida em `state.db`.
  Execução pelo menos uma vez, com `idempotency_key = gatilho_id + janela`
  para não duplicar. Biblioteca ou implementação conferida na documentação
  atual antes de decidir (regra L-99); preferir o simples (laço assíncrono e
  tabela) a um agendador completo, se bastar.
- Eventos publicados após o commit, em tabela `evento` (outbox), para
  nunca disparar gatilho por transação que não concluiu.

## Segurança

- Cada execução gera evento de auditoria com `trace_id`, o gatilho e a nota
  aprovada que o definiu.
- Limites: execuções por hora, profundidade máxima de encadeamento (um
  gatilho que causa evento que causa outro), janela de tempo.
- Simulador (spec 018) ganha "o que este gatilho faria agora", sem efeito.

## Exemplos para o piloto

Sincronização da Agenda a cada poucos minutos (spec 026); aviso de sessão
passada sem desfecho; resumo diário do caixa; aviso de NF pendente; lembrete de fechar o mês;
operação aguardando aprovação há mais de 24 h; sessão realizada sem
pagamento (depende da spec 026).

## Red Team / Blue Team / Postmortem

| Risco (Red Team) | Controle (Blue Team) |
|---|---|
| Loop de gatilhos ou tempestade de eventos | Limite de profundidade, deduplicação e teto por hora; disjuntor que desativa o gatilho e alerta |
| Gatilho usado para escalar privilégio | Ator de sistema com permissões mínimas explícitas; só A0/A1 executam sozinhos |
| Gatilho altera dinheiro sem ninguém ver | Efeito relevante vira operação proposta para pessoa |
| Gatilho disparado por transação desfeita | Outbox pós-commit |
| Gatilho silenciosamente parado | Painel mostra última execução e falhas; alerta se não rodar |

**Postmortem antecipado.** Alertas demais viram ruído e ninguém lê. Cada
gatilho declara destinatário e frequência máxima; o painel mostra quais
alertas geraram ação.

## God Mode / L-99

**God Mode.** Lista única de todos os gatilhos ativos, últimas execuções e
resultados; o `trace_id` liga evento, gatilho, operação e lançamento.

**L-99.**

- Invariantes: (T1) gatilho nunca executa ação A2/A3 sem pessoa; (T2) cada
  janela executa no máximo uma vez; (T3) gatilho inativo não dispara.
- Falhas: queda do motor no meio da janela retoma sem duplicar.
- Orçamento: atraso de agenda menor que 5 s.
- Runbook: pausar todos os gatilhos, reprocessar uma janela, depurar um
  gatilho que não roda.

## Testes previstos

- Agenda dispara na janela e não duplica após reinício.
- Evento só dispara depois do commit.
- A2/A3 gera operação proposta, nunca executa.
- Encadeamento acima do limite é cortado e alertado.
- Simulação não tem efeito.

## Fora de escopo

Fluxos com várias etapas e espera (workflows longos), integração com filas
externas.
