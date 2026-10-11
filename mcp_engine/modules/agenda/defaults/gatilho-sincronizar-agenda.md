---
tipo: gatilho
id: sincronizar-agenda
---
Sincroniza a Agenda com o ledger a cada 5 minutos, em horário comercial. Somente leitura na
Agenda; idempotente. Modelo para ativar quando o agendador (spec 031) estiver no ar: copie
para o caderno do cliente e envie para aprovação.

```brm-config
id: sincronizar-agenda
quando: { agenda: "*/5 7-20 * * 1-6" }
acao:
  skill: sincronizar_agenda
  argumentos: {}
autonomia: A1
limites: { max_por_hora: 12 }
```
