---
tipo: politica
zona: politicas
confidencialidade: financeiro
titulo: Confidencialidade de comissão e faturamento
---
# Confidencialidade de comissão e faturamento

Esta política vale para todo o chat, a Central de Ações e os relatórios. Quem não é administrador
(ou super_admin) **não** recebe, por nenhum caminho:

- comissão, repasse, taxa, parte da clínica e quanto cada profissional ganha;
- o bônus de indicação e a aptidão de cada profissional;
- faturamento, resultado, entradas e saídas, painel financeiro e relatórios da clínica;
- qualquer planilha exportada (financeira ou das secretarias, que serão descontinuadas pelo módulo
  novo da Agenda);
- o valor previsto das sessões sem pagamento;
- o repasse devolvido ao registrar um recebimento.

Isso inclui o próprio profissional: ele também não vê o seu repasse pelo chat.

## O que a recepção continua vendo

Cobrança do paciente (saldo a receber e pacote), faltas, pendências, notas fiscais por status e os
últimos lançamentos sem nome de paciente.

## Como é garantido

1. **Regras duras** (`rules.yaml`): `comissao-so-admin`, `bonus-indicacao-so-admin`,
   `faturamento-so-admin` e `planilha-so-admin`. Quem não é administrador só consulta o
   que está na lista de permitidos; consulta nova nasce negada.
2. **Travas no código** do ledger e da Agenda, que valem mesmo que uma regra seja removida.
3. **Leitura de notas** (`vault_access.yaml`): notas dos tipos `relatorio`, `comissao`, `taxa` e
   `repasse`, ou com `confidencialidade: financeiro`, só aparecem para o administrador.
4. **Central de Ações**: as ações de bônus e de aptidão exigem o papel de administrador.

## Como pedir ao chat

Se alguém sem permissão perguntar, o chat responde que a informação é restrita ao administrador e
indica pedir a ele. Não deve estimar, resumir nem dar ordem de grandeza.
