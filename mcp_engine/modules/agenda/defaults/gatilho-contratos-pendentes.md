---
tipo: gatilho
id: contratos-pendentes
---
Lista diária de contratos pendentes (spec 035): pacientes com sessão concluída nos últimos sete
dias e sem contrato emitido. Roda como perfil comum, então o alerta traz só a contagem; os nomes
ficam com o gestor (`agenda_contratos_pendentes`). Copie para o caderno do cliente e envie para
aprovação.

```brm-config
id: contratos-pendentes
descricao: Contratos de sessão pendentes nos últimos sete dias
quando: { agenda: "0 18 * * 1-6" }
acao:
  skill: agenda_contratos_pendentes
  argumentos: {}
saida:
  tipo: alerta
  nivel: aviso
  mensagem: "Contratos pendentes nos últimos sete dias: {{resultado.total}}."
autonomia: A0
permissoes: [agenda_contratos_pendentes]
destinatario: gestor
limites: { max_por_hora: 1 }
```
