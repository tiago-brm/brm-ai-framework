---
tipo: modelo
zona: modelos
documento: contrato-sessao
versao: 1
exemplo: true
titulo: Contrato de prestação de serviços (EXEMPLO)
---
# Contrato de prestação de serviços

**MODELO DE EXEMPLO. Substituir pelo contrato real da clínica antes de usar.**

## Partes

**Contratada:** {{ emissor.razao_social }}, CNPJ {{ emissor.cnpj }}.

**Contratante:** {{ paciente.nome }}, CPF {{ paciente.cpf }}, nascido(a) em {{ paciente.nascimento }}, residente em {{ paciente.endereco.logradouro }}, {{ paciente.endereco.numero }}{% if paciente.endereco.complemento %}, {{ paciente.endereco.complemento }}{% endif %}, {{ paciente.endereco.bairro }}, {{ paciente.endereco.cidade }}/{{ paciente.endereco.uf }}, CEP {{ paciente.endereco.cep }}.
{% if paciente.menor_de_idade %}
**Responsável legal:** {{ responsavel.nome }}, CPF {{ responsavel.cpf }}, na qualidade de {{ responsavel.parentesco }}, que assina por e em nome do contratante.
{% endif %}
## Objeto

Atendimento com {{ sessao.profissional }}{% if sessao.servico %} ({{ sessao.servico }}){% endif %}, a partir da sessão de {{ sessao.data }}.

## Valor

Valor por sessão: {{ sessao.preco }}, conforme combinado no agendamento.

## Faltas e cancelamentos

- Cancelamentos devem ser avisados com antecedência mínima de 24 horas.
- Faltas sem aviso podem ser cobradas.

## Sigilo

As informações do atendimento são sigilosas, nos termos do código de ética profissional.

## Foro

Fica eleito o foro de {{ emissor.cidade }}.

{{ emissor.cidade }}, {{ sessao.data }}.

Assinatura do contratante ou responsável: ______________________________

Assinatura da contratada: ______________________________
