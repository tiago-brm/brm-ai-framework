# INTERNO: piloto do Módulo 1 para o Instituto Ana Flávia Weis (v5)

> **INTERNO. NÃO ENVIAR AO CLIENTE.** Contém custos, margens e estratégia. Destinatário: dono da BRM Solutions. Documento do cliente: `docs/comercial/proposta-instituto-weis.html` (fonte `.md`, cópia `.docx`).

Data: 09/10/2026 · Câmbio: US$ 1 = R$ 5,22 (dólar comercial em 28/09/2026; confirmar no dia). Margens **sem tributos** (o regime da BRM não foi informado). "Custo de referência" = horas x R$ 200 (valor do seu tempo, não desembolso).

**Termos firmes do dono (v3):** implantação R$ 5.900 em 5 parcelas de R$ 1.180 (1ª na autorização); mensalidade R$ 399 (licenças R$ 349 + hospedagem R$ 50), contrato de 12 meses; bônus de 30 h de consultoria. O que for meu está como [PROPOSTA] na proposta.

**Sobre o "sem nota":** enquadrei o desconto como "valores sem tributos; na emissão da nota incidem os tributos, estimados em até 20%" (R$ 5.900 vira R$ 7.080; R$ 1.180 vira R$ 1.416; R$ 399 vira R$ 478,80), porque emitir serviço sem nota é irregular e o cliente também precisa da nota. Você pode editar.

---

**Decisões finais do dono (v5):** o brinde de NFS-e é **adicional** às 30 h (nada de abater); os valores R$ 5.900 / 5 x R$ 1.180 / R$ 399 são firmes. A proposta ao cliente foi simplificada para uma clínica pequena: **removi dela** valor-hora (R$ 200/R$ 180), banco de horas com preço, reajuste com índice e a tabela de manutenção; horas extras viram "orçadas e aprovadas antes de qualquer cobrança". Esses preços continuam aqui como **referência interna** (seção 5) para você usar na hora de orçar. Garantia de 90 dias e texto de tributos permanecem. A estratégia de recuperação agora é vender outros pilotos nos mesmos termos (12.5 e 12.9).

**Nova decisão (v4):** a skill de NFS-e de Campo Grande/MS é **brinde**, só essa. A proposta a apresenta ao lado do bônus de 30 h, com escopo, teto de esforço e condições [PROPOSTA]. Seção 12 deste memorando traz o custo, o subsídio total e as salvaguardas. Os números das seções 1 a 11 (v3) valem para o piloto sem o brinde.

## 1. Resumo em cinco linhas

- O escopo reduzido consome **50 a 75 h (provável 60 h)**. R$ 5.900 dá **R$ 98,3/h efetivo** (R$ 78,7 a R$ 118).
- **Subsídio da BRM na implantação: R$ 6.100** no provável (R$ 12.000 de referência menos R$ 5.900); R$ 4.100 a R$ 9.100 na faixa.
- Receita do ano 1: **R$ 10.688** (5.900 + 12 x 399). Incluindo suporte estimado (12 h) e descontando a infraestrutura, **R$ 124/h**.
- **A hospedagem de R$ 50 está abaixo do custo real (R$ 145)**: perde R$ 95/mês só de infraestrutura.
- Os R$ 349 de licença são a única margem real; o piloto só se justifica como investimento em caso de uso e em clientes seguintes.

## 2. Horas reais do escopo reduzido (R$ 200/h)

Ledger e importador já estão prontos; só implantação.

| Etapa | Otimista | Provável | Conservador |
|---|---|---|---|
| Levantamento e parametrização (recebimentos, repasse, perfis) | 6 | 8 | 10 |
| Importação das planilhas (cliente separa e entrega os dados) | 14 | 18 | 24 |
| Homologação (aceite, avaliação com modelo real, ajustes) | 6 | 8 | 10 |
| Infra e implantação (Docker, usuários, vault, backup, SilverBullet só BRM) | 6 | 7 | 8 |
| Treinamento enxuto | 3 | 4 | 5 |
| Piloto paralelo 30 dias | 9 | 11 | 13 |
| Go-live | 2 | 2 | 3 |
| Gestão | 4 | 2 | 2 |
| **Total** | **50** | **60** | **75** |

| | Otimista | **Provável** | Conservador | Teto sugerido |
|---|---|---|---|---|
| Horas | 50 | **60** | 75 | 70 |
| Custo de referência | R$ 10.000 | **R$ 12.000** | R$ 15.000 | R$ 14.000 |
| Valor-hora efetivo (5.900/h) | R$ 118,0 | **R$ 98,3** | R$ 78,7 | R$ 84,3 |
| Subsídio (referência menos 5.900) | R$ 4.100 | **R$ 6.100** | R$ 9.100 | R$ 8.100 |
| Equilíbrio de horas a R$ 5.900 | 29,5 h | 29,5 h | 29,5 h | 29,5 h |

Leitura: a implantação paga **menos da metade** do seu valor de referência (5.900 / 12.000 = 49%). Com tributos de até 20% por cima, nada muda para a BRM (o valor base é líquido).

**Tabela para comparação:** o pacote Recomendado completo era R$ 31.900 (155 h); o piloto é 18% disso por 39% das horas.

## 3. Receita, caixa e valor-hora no ano 1

### 3.1 Calendário de recebimento (premissas a confirmar)

Premissas: parcelas nos meses 0 a 4 (1ª na autorização, ou seja, "~6 meses" lido como 5 parcelas mensais); mensalidade começa no go-live, estimado no mês 3; contrato de 12 meses de mensalidade (meses 3 a 14).

| Mês | Parcela implantação | Mensalidade | Recebido no mês | Acumulado |
|---|---|---|---|---|
| 0 (autorização) | 1.180 | | 1.180 | 1.180 |
| 1 | 1.180 | | 1.180 | 2.360 |
| 2 | 1.180 | | 1.180 | 3.540 |
| 3 (go-live) | 1.180 | 399 | 1.579 | 5.119 |
| 4 | 1.180 | 399 | 1.579 | 6.698 |
| 5 | | 399 | 399 | 7.097 |
| 6 | | 399 | 399 | 7.496 |
| 7 | | 399 | 399 | 7.895 |
| 8 | | 399 | 399 | 8.294 |
| 9 | | 399 | 399 | 8.693 |
| 10 | | 399 | 399 | 9.092 |
| 11 | | 399 | 399 | 9.491 |
| 12 | | 399 | 399 | 9.890 |
| 13 | | 399 | 399 | 10.289 |
| 14 | | 399 | 399 | **10.688** |

Se o dono quiser a mensalidade desde a autorização, os 12 meses terminam no mês 11 e o acumulado fecha antes; a cobrança de hospedagem antes do go-live não faz sentido, porque a spec 028 só libera a nuvem depois do piloto.

### 3.2 Fluxo de caixa acumulado (cenário provável)

Custos considerados: tempo de implantação 60 h x R$ 200 = R$ 12.000, distribuído em R$ 3.000 por mês nos meses 0 a 3; infraestrutura R$ 145/mês a partir do mês 3; suporte e operação de 1 h/mês (R$ 200) a partir do mês 3 (estimativa minha; a garantia de 90 dias e o uso da hospedagem puxam isso).

| Visão | Mês 4 | Mês 8 | Mês 14 |
|---|---|---|---|
| Recebido acumulado | 6.698 | 8.294 | 10.688 |
| **Só desembolso** (infra R$ 145/mês) | +6.408 | +7.424 | **+8.948** |
| **Com seu tempo valorizado** (R$ 200/h) | -5.992 (12.690 de custo) | -5.776 | **-5.452** (16.140 de custo) |

Em caixa, o piloto é positivo desde o mês 0 (você não desembolsa nada além de infra). Com o tempo a R$ 200/h, **nunca se paga no ano 1** e o saldo melhora só R$ 54 por mês (399 menos 345), ou seja, levaria anos: ele é um **investimento**, não um negócio.

### 3.3 Valor-hora efetivo incluindo suporte

| Visão | Horas | Receita (líquida de infra R$ 1.740) | R$/h |
|---|---|---|---|
| Implantação só | 60 | 5.900 | 98,3 |
| Ano 1 (implantação + 12 h suporte/operação) | 72 | 8.948 | **124,3** |
| Ano 1 com bônus de 30 h totalmente usado | 102 | 8.948 | **87,7** |
| Faixa do ano 1 (50 h a 75 h + 12 h) | 62 a 87 | 8.948 | 144,3 a 102,9 |

Sem descontar a infra: R$ 148,4/h (10.688 / 72).

## 4. A hospedagem de R$ 50 contra o custo real

Pesquisa de 09/10/2026 (reaproveitada): Hostinger KVM 1 (1 vCPU/4 GB) R$ 29,99 promo, renova R$ 59,99; KVM 2 (2 vCPU/8 GB) R$ 43,99, renova R$ 77,99; VPS nacionais 4 GB de agregadores cerca de R$ 35 a R$ 100 (estimativa sem data); Locaweb Cloud Server R$ 179 (PDF sem data). Requisito do repositório: engine com limite de 2 GB, dashboard, SilverBullet e backup; piso 2 vCPU/4 GB. Data center no Brasil da Hostinger **não confirmado** na página.

- Custo assumido: **R$ 120 de VPS + R$ 25 de backup/domínio = R$ 145/mês** (faixa R$ 45 a R$ 180).
- Preço R$ 50: **margem -R$ 95/mês (-190%)** só de infraestrutura; **-R$ 295/mês** com 1 h de operação (R$ 200). No ano: -R$ 1.140 (só infra) ou -R$ 3.540 (com 1 h/mês).
- R$ 50 só cobre o custo de um VPS de entrada na **promoção** (R$ 30 a R$ 44) e nada de operação; o preço de renovação já passa de R$ 60.
- **Saídas:** (a) mais de um cliente por servidor (2 vCPU/8 GB a R$ 78 renovado dá cerca de R$ 40 por cliente com 2 clientes, mas misturar dado de saúde exige isolamento e contrato); (b) instalar no equipamento ou na conta de nuvem do cliente e cobrar só a licença; (c) revisar o valor da hospedagem na renovação; a proposta hoje só prevê reajuste pelo IPCA, e incluir revisão explícita depende da sua aprovação.
- A proposta descreve a hospedagem como **servidor + backup diário cifrado** e exclui monitoramento e restauração (que viram horas).

## 5. O que a mensalidade NÃO inclui, e as salvaguardas

**Não inclui suporte nem manutenção.** Proposta [PROPOSTA]: (1) **garantia de correção de defeitos por 90 dias** desde o go-live; (2) depois, **R$ 200/h, fundador R$ 180/h** durante o contrato de 12 meses; (3) **banco de horas opcional** de 20 h por R$ 3.400 (R$ 170/h). Estimativa de absorção da garantia: 4 a 8 h no pior caso (R$ 800 a R$ 1.600 de referência).

Salvaguardas (já na proposta onde aplicável):

| Salvaguarda | Situação |
|---|---|
| Teto de horas do escopo | Interno **70 h**; na proposta aparece só como "teto de esforço definido em contrato" [PROPOSTA] (sem número para não convidar a multiplicar por R$ 200). Excedente orçado antes |
| Relógio parado | Se o cliente atrasar entregas, o prazo anda junto; retrabalho por dado sujo é extra |
| Cancelamento | Parcelas restantes continuam devidas [PROPOSTA] |
| Dados | Do cliente; exportação em formato aberto em 15 dias úteis; plataforma e módulos da BRM |
| Reajuste | IPCA a cada 12 meses sobre licenças, hospedagem e horas; implantação não reajusta |
| Escopo | Contas a pagar/receber, fluxo previsto, pacotes e faltas **fora** do piloto (o módulo existe; só não é parametrizado). Confirme se algum entra, porque recebimento de pacotes e faltas mexe no esforço da importação |

## 6. Bônus: 30 horas de consultoria

**30 h x R$ 200 = R$ 6.000, quase igual à implantação inteira (R$ 5.900).** É um segundo desconto do mesmo tamanho. Condições sugeridas (na proposta como [PROPOSTA]): válidas só durante o contrato de 12 meses; não cumulativas; não reembolsáveis em dinheiro; só para novos módulos/integrações (não suporte); blocos mínimos de 4 h; agendamento conforme disponibilidade da BRM.

Sugestão: **limitar a validade ao contrato** e vincular o uso à integração com a Agenda (estimada em cerca de 24 h de configuração e conferência), de modo que o bônus vire a porta de entrada da venda seguinte e não consultoria genérica. Se ficar sem uso, a BRM não perde nada. Se for todo usado, o ano 1 cai para R$ 87,7/h (seção 3.3).

## 7. Ponto de equilíbrio da plataforma com este preço

Contribuição mensal por cliente neste preço, sem suporte: licenças R$ 349 + hospedagem (50 - 145) = **R$ 254**; com 1 h/mês de suporte não cobrado, **R$ 54**.

- Manutenção da plataforma (estimativa 20 h/mês x R$ 200 = R$ 4.000): **16 clientes** a R$ 254 (74 a R$ 54).
- Mais amortizar a Fase 1 (570 a 890 h = R$ 114 mil a R$ 178 mil em 36 meses, R$ 3.167 a R$ 4.944/mês): **28 a 35 clientes** a R$ 254.
- Com hospedagem compartilhada (custo ~R$ 40 por cliente) a contribuição sobe para ~R$ 359 e os números caem para 11 e 20 a 25 clientes.

Conclusão: **este é um preço de piloto, não de tabela.** Para o cliente seguinte, partir do preço de tabela (implantação de R$ 29.900 a R$ 31.900 conforme a v2, mensalidade com hospedagem a R$ 450 e manutenção por horas) e usar o Instituto como caso.

## 8. Cronograma (escopo reduzido, contado a partir de agora)

O piloto paralelo de 30 dias não encolhe com o escopo, então o ganho de prazo é pequeno.

| Etapa | Otimista | Provável | Conservador |
|---|---|---|---|
| Levantamento e parametrização | 2 sem | 3 sem | 4 sem |
| Importação (em parte junto da anterior) | 2 sem | 3 sem | 5 sem |
| Homologação e treinamento | 1 sem | 1 sem | 2 sem |
| Piloto paralelo | 4 sem | 5 sem | 8 sem |
| Go-live | 1 sem | 1 sem | 1 sem |
| **Go-live em torno de** | **sem 9** | **sem 12** | **sem 18** |

Dependências do cliente: planilhas, tabela de comissões e de preços por profissional, disponibilidade para treinamento, respostas de importação, calendário de fim de ano. Dependências da BRM: avaliação com o modelo real **nunca rodada** (rode antes de autorizar); SilverBullet só para a equipe da BRM; integração com a Agenda futura (endpoint de leitura existe localmente, **não aplicado nem implantado**; conector não construído).

## 9. Riscos

| Risco | Efeito | Mitigação |
|---|---|---|
| Importação estoura (texto livre em "DATA SESSÕES", formas e NF mistas) | Até 75 h, subsídio de R$ 9.100 | Cliente separa e entrega os dados; teto de 70 h; excedente orçado; relógio parado |
| Cliente trata R$ 5.900 como preço normal | Âncora errada para módulos seguintes | Proposta diz "preço de piloto" e que as demais etapas são a preço de tabela |
| Bônus de 30 h vira suporte grátis | R$ 6.000 de trabalho sem receita | Condições na seção 6, restrição a módulos novos |
| Hospedagem a R$ 50 | -R$ 95/mês, -R$ 1.140/ano só de infra | Seção 4 |
| Avaliação com modelo real reprova | Horas extras de ajuste | Rodar antes de autorizar; mais de 30 h é extra |
| Tributos de até 20% por cima não cobrem a alíquota real, ou o regime não permite | Margem líquida menor | Informar o regime e eu refaço |
| Cancelamento antes das 5 parcelas | Caixa perdido | Parcelas restantes devidas; cláusula a validar com jurídico |
| Poucos clientes | Plataforma vira custo seu | Seção 7 |
| Preços de terceiros mudam (Claude, VPS, Resend) | Repasse | Reconferir antes do envio |
| WhatsApp via Baileys | Banimento do número do cliente | Fora do piloto; só com API oficial ou termo de risco |

## 10. Perguntas em aberto (seção 12.8 traz as do brinde)

1. As 5 parcelas são nos meses 0 a 4 (leitura que adotei)? E a mensalidade começa no go-live ou na autorização?
2. Regime tributário da BRM: "até 20%" é a alíquota real?
3. O R$ 50 de hospedagem é intencional (dois ou mais clientes por servidor?) ou aceita revisão na renovação?
4. Quais itens extras (pacotes, faltas, contas a pagar) entram no piloto?
5. O Instituto já usa a BRM Agenda e qual o número de profissionais, unidades e meses de histórico?

## 11. Fontes (09/10/2026, salvo indicação)

claude.com/pricing (Team) · skill `claude-api` (tabela de 06/10/2026) · resend.com/pricing · hostinger.com/br/vps-hosting · whtop.com (estimativa) · Locaweb Cloud Server PDF (sem data) · câmbio R$ 5,2226 em 28/09/2026 · Meta/Zenvia/Unipile (WhatsApp, tarifas divergentes, não usar como preço) · twilio.com/pt-br/sms/pricing/br · repositório `docs/specs/019` a `031`, `docs/changelog/*`, `docs/runbooks/*`.

---

## 12. Brinde: emissão assistida de NFS-e (Campo Grande/MS)

### 12.1 Custo a R$ 200/h

Base de estimativa: o enunciado cita ~130 a 200 h para integração direta pela API do Emissor Nacional e ~80 a 130 h via gateway (originais, do tempo em que a plataforma não existia). **Não encontrei o documento-fonte dessas faixas no repositório**; as specs só registram `nfse` como módulo privado do cliente, Fase 2, atrás de `FiscalProvider` (specs 019 e 027). Usei os valores informados.

Reestimativa agora: o motor, os módulos privados por cliente (`clients/<id>/modules`, spec 027), a operação com confirmação e a auditoria já existem; falta o que é específico de fiscal. Restringi a um município, um CNPJ e três operações.

| Parte do trabalho | Direta (Emissor Nacional) | Via gateway |
|---|---|---|
| `FiscalProvider` + skills emitir/consultar/cancelar sobre as operações com confirmação | 24 | 20 |
| Montagem e assinatura do documento (XML/DPS), certificado A1 (mTLS), segredos | 30 | 8 |
| Mapeamento de campos de Campo Grande (tomador, serviço, alíquota, retenções) | 12 | 10 |
| Homologação em ambiente de teste, erros e rejeições, reprocessamento | 20 | 14 |
| Testes automatizados, runbook, ajustes de perfis/regras | 14 | 12 |
| **Provável** | **100 h** | **64 h** |
| Faixa (otimista a conservador) | 70 a 150 h | 50 a 100 h |

| | Provável | Faixa |
|---|---|---|
| Custo de referência, direta | **R$ 20.000** | R$ 14.000 a R$ 30.000 |
| Custo de referência, via gateway | R$ 12.800 | R$ 10.000 a R$ 20.000, mais custo recorrente de terceiro (repasse) |
| Teto de esforço sugerido | **100 h (R$ 20.000)** | excedente orçado antes |

Recomendo **integração direta** se o Emissor Nacional aceitar Campo Grande; gateway só se houver bloqueio. Os dois dependem de A1 e inscrição municipal do cliente, e **o tempo de espera de terceiros (prefeitura, ambiente de teste) não está nas horas**. O texto de Campo Grande "em transição para o padrão nacional" vem da documentação pública que a coordenação me citou; **eu não verifiquei**.

Comparação: o brinde custa **3,4 vezes a implantação** (R$ 20.000 contra R$ 5.900) e mais de 4 anos de licenças do Instituto (R$ 349 x 12 = R$ 4.188 por ano).

### 12.2 Subsídio total e ano 1 consolidado

| Item | Horas | Custo de referência |
|---|---|---|
| Implantação (piloto, provável) | 60 | R$ 12.000 (subsídio R$ 6.100 depois dos R$ 5.900 recebidos) |
| Brinde NFS-e (direta, provável) | 100 | R$ 20.000 (subsídio integral) |
| Bônus de 30 h, se todo usado | 30 | R$ 6.000 (subsídio integral) |
| Suporte e operação, 12 meses | 12 | R$ 2.400 (parte coberta na seção 3) |
| **Subsídio total (60 + 100 + 30 h)** | | **R$ 32.100** (6.100 + 20.000 + 6.000) |

Receita do ano 1 continua **R$ 10.688** (sem tributos). Horas totais com tudo usado: 60 + 100 + 30 + 12 = **202 h**.

| Cenário do ano 1 | Horas | Receita líquida de infra (R$ 8.948) | R$/h efetivo |
|---|---|---|---|
| Piloto sem brinde nem bônus (v3) | 72 | 8.948 | 124,3 |
| Com brinde NFS-e, sem usar o bônus | 172 | 8.948 | **52,0** |
| **Tudo usado** (brinde provável + bônus) | 202 | 8.948 | **44,3** |
| Melhor caso (50 h + 70 h via direta otimista + 12 h, sem bônus) | 132 | 8.948 | 67,8 |
| Pior caso (75 h + 150 h + 30 h + 12 h) | 267 | 8.948 | 33,5 |

Custo de referência total (tudo usado): 202 h x R$ 200 = R$ 40.400 mais infra R$ 1.740 = R$ 42.140 contra receita de R$ 10.688, ou **-R$ 31.452** no ano 1 (era -R$ 5.452 na v3). Em caixa de desembolso você ainda não gasta além da infra; o que sai é tempo seu.

### 12.3 O brinde é adicional ao bônus (decisão do dono)

Fica como está na proposta: as 30 h valem nos 12 meses do contrato, independentes da NFS-e; retirei do texto do cliente a ideia de liberá-las só depois do brinde. **Observação opcional (só interna):** se quiser limitar a exposição, você pode combinar com o cliente, na conversa, que o uso das 30 h comece depois da entrega da NFS-e, sem escrever isso na proposta.

### 12.4 Salvaguardas

- **Teto de 100 h** no brinde; excedente orçado antes a R$ 200/h (fundador R$ 180/h), com a sua concordância.
- **Contrato de 12 meses:** se cancelar antes, o brinde vira cobrável proporcionalmente: sugiro (horas efetivamente gastas x R$ 200) x (meses restantes / 12). Exemplo: cancelamento no mês 4 com 100 h gastas = 100 x 200 x 8/12 = **R$ 13.333**. [PROPOSTA]; validar com o jurídico.
- **Prazo condicionado a dependências externas:** certificado A1, inscrição municipal, ambiente de teste e aceite da prefeitura; sem data fixa na proposta. O relógio do brinde só começa quando essas entradas existirem.
- **Manutenção fiscal fora do brinde:** mudança de layout ou de regra (IBS/CBS a partir de 2027) em horas ou contrato de manutenção.
- **Conformidade:** "ajuda a cumprir; validar com a contabilidade".
- Não comece o desenvolvimento antes de confirmar com a prefeitura que a integração é possível: o risco externo é o maior risco do brinde.

### 12.5 Recuperação do investimento: vender outros pilotos nos mesmos termos

Visão do dono: a BRM **absorve o subsídio** (implantação abaixo do custo + brinde NFS-e + 30 h) e tenta **vender outros pilotos a R$ 5.900 + R$ 399/mês**, preço que considera justo, reaproveitando o módulo de NFS-e para outras clínicas de Campo Grande. O módulo `nfse` nasce privado do Instituto (`clients/<id>/modules`) e é promovido ao catálogo quando o segundo cliente aparecer. O cálculo está em 12.9.

**Alternativa claramente separada (não usada na proposta):** cobrar das outras clínicas a NFS-e à parte, a preço de tabela, por exemplo implantação de R$ 4.900 (cerca de 24 h) e licença do módulo de R$ 150/mês [PROPOSTA]. Isso só acelera a recuperação (ver 12.9, última linha).

### 12.6 Cronograma

A NFS-e é **pós go-live** e **não altera o prazo do Módulo 1** (9 / 12 / 18 semanas). Depois que A1, inscrição municipal e acesso de teste estiverem disponíveis: estimativa de **6 / 10 / 16 semanas** para entrega do brinde, mais o tempo de espera de terceiros. Na proposta, apenas "data a combinar".

### 12.7 Riscos adicionais

| Risco | Efeito | Mitigação |
|---|---|---|
| Campo Grande em transição para o padrão nacional, ou sem integração disponível | Brinde atrasa, muda de escopo ou fica inviável | Confirmar com a prefeitura antes de começar; escrito como risco externo na proposta; gateway como plano B |
| Ambiente de teste ausente ou instável | Homologação vira meses | Relógio parado; sem data fixa |
| Estouro do teto de 100 h | Subsídio acima de R$ 20.000 | Teto e excedente orçado antes |
| Cliente sem A1 ou inscrição municipal | Brinde não começa | Entradas listadas como dependência do cliente |
| Mudança fiscal (IBS/CBS 2027) | Manutenção contínua não coberta | Fora do brinde; horas ou contrato |
| Erro fiscal atribuído à BRM | Responsabilidade | "Ajuda a cumprir; validar com a contabilidade"; confirmação humana |
| Cancelamento antes de 12 meses | Brinde de R$ 20.000 sem retorno | Cobrança proporcional |
| Soma dos brindes (R$ 26.000 de referência) vira expectativa de tabela | Âncora errada para módulos seguintes | Proposta rotula como brinde e piloto |

### 12.8 Perguntas do brinde

1. (Decidido: o brinde é adicional ao bônus.)
2. Já confirmou com a prefeitura de Campo Grande se aceita integração direta?
3. O Instituto tem certificado A1 e inscrição municipal?
4. Teto de 100 h e cobrança proporcional no cancelamento estão de acordo?

### 12.9 Quantos pilotos recuperam o subsídio: versão conservadora e versão enxuta

Revisão crítica: os números que usei até aqui são prudentes para um primeiro cliente, mas pesados para uma operação pequena.

| Premissa | Conservadora | Enxuta (realista) | Por quê |
|---|---|---|---|
| Implantação do Instituto | 60 h | 55 h | Escopo já pequeno; módulo financeiro e importador prontos |
| Brinde NFS-e | 100 h | 70 h | A plataforma e os módulos privados existem; um município, três operações, direta. Os 70 h são o piso da faixa 70 a 150; a espera de terceiros fica fora das horas |
| Bônus de 30 h | usado por inteiro | 15 h realmente usadas | Clínica pequena raramente consome tudo |
| Hospedagem (custo) | R$ 145/mês | R$ 75/mês | KVM 1 renovado R$ 59,99 + cerca de R$ 15 de backup; promoção R$ 29,99 só no 1º prazo. Data center no Brasil **não confirmado** |
| Suporte efetivo sem cobrança | 1 h/mês (R$ 200) | 0,5 h/mês (R$ 100) | Suporte não está no pacote; só a garantia de 90 dias (4 a 8 h) e dúvidas pontuais |
| Prazo da NFS-e | 6 / 10 / 16 sem | 6 sem (se a prefeitura permitir) | Depende de terceiros, não das horas |
| Implantação de cada novo piloto | 45 h | 40 h | Reuso de 25% a 35% (modelos, scripts, perfis) |
| **Subsídio total** | **R$ 32.100** (6.100 + 20.000 + 6.000) | **R$ 22.100** (5.100 + 14.000 + 3.000) | |
| Ano 1 do Instituto, R$/h efetivo | R$ 44,3 (202 h) | R$ 67,0 (146 h; receita líquida de infra R$ 9.788) | |

Contribuição de **cada novo piloto** a R$ 5.900 + R$ 399/mês, a R$ 200/h de referência:

| | Conservadora | Enxuta |
|---|---|---|
| Ano 1: 10.688 menos implantação, infra e suporte | 10.688 - 9.000 - 1.740 - 2.400 = **-R$ 2.452** | 10.688 - 8.000 - 900 - 1.200 = **+R$ 588** |
| Por mês a partir do ano 2: 399 menos infra e suporte | 399 - 145 - 200 = **R$ 54** | 399 - 75 - 100 = **R$ 224** |

**Resultado.** Na versão conservadora, os pilotos nesses termos **não recuperam** o subsídio de R$ 32.100 em prazo razoável (cada piloto custa R$ 2.452 no ano 1 e rende R$ 648 por ano depois; 10 pilotos levariam mais de 8 anos). Isso é consequência de valorizar todo o seu tempo a R$ 200/h. Na versão enxuta (todos os pilotos começando juntos; se forem escalonados, soma-se o atraso):

| Pilotos novos | Caixa no ano 1 | Recorrência mensal depois | Meses para recuperar R$ 22.100 (a contar do início) |
|---|---|---|---|
| 3 | R$ 1.764 | R$ 672 | cerca de **42 meses** |
| 5 | R$ 2.940 | R$ 1.120 | cerca de **29 meses** |
| 10 | R$ 5.880 | R$ 2.240 | cerca de **19 meses** |

Para o Instituto continuar contribuindo, some R$ 224/mês dele a partir do ano 2. Se as clínicas novas também comprarem a NFS-e à parte (alternativa de 12.5, R$ 150/mês por CNPJ mais R$ 4.900, que empata com 24 h), 5 pilotos recuperam em cerca de **22 meses**.

**Em uma frase:** só com pilotos a R$ 399/mês a recuperação depende de 5 a 10 clínicas e de uma operação realmente enxuta (hospedagem barata, quase nenhum suporte não cobrado); se o suporte virar 1 h por mês por cliente, a conta deixa de fechar. Dois cuidados: manter suporte fora da mensalidade (horas orçadas) e confirmar o provedor de hospedagem.
