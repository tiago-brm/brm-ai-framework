---
tipo: modelo
zona: modelos
documento: recibo-pagamento
versao: 1
exemplo: true
titulo: Recibo de prestação de serviço (EXEMPLO)
---
# Recibo de Prestação de Serviço

**Nº {{ recibo.numero }}** · **{{ recibo.valor }}**

Recebi de **{{ recibo.pagador_nome }}**, CPF {{ recibo.pagador_cpf }}, a importância de **{{ recibo.extenso }}**, referente a **{{ recibo.descricao }}**, pago em {{ recibo.forma }}.

Atendimento realizado por: {{ recibo.profissionais }}.

Para maior clareza, firmo o presente recibo, que comprova o recebimento integral do valor mencionado, concedendo **quitação plena, geral e irrevogável** pela quantia recebida.

{{ emissor.cidade }}, {{ recibo.data }}

______________________________

**{{ emissor.razao_social }}**
CNPJ: {{ emissor.cnpj }}

Registrado por {{ recibo.registrado_por }}.
