# Handoff para o time do BRM Agenda: o que o BRM AI Framework faz e o que precisamos do Agenda

**Para quem:** quem desenvolve o BRM Agenda e precisa entender a integração sem ler o código do framework.
**Cliente do piloto:** Instituto Ana Flávia Weis (3 unidades, 6 secretárias, 2 gestores, psicólogos depois).
**Status (2026-11-01):** G10, G11, G12, G14, G15 e G16 entregues pelo Agenda; o provider HTTP do framework já usa a ficha real (`/api/v1/customers/:id/ficha`). Pendentes: G13 (TOTP) e G17 (abaixo). Falta um teste ponta a ponta contra o Agenda de verdade.
**Detalhes técnicos:** `docs/specs/032` a `035` e `docs/changelog/035-central-de-acoes-e-emissao-de-documentos.md` neste repositório.

## 1. O que é o framework, em um parágrafo

É um servidor que fica entre o chat (Claude) e os sistemas da BRM. A secretária conversa com o Claude; o
Claude chama ferramentas do framework; o framework decide se a pessoa pode, registra tudo (quem pediu, o
quê, quando, resultado) e só então chama o Agenda. **O Agenda continua sendo o dono dos dados.** O
framework governa, registra e emite documentos. Ele não é um segundo cadastro.

```
Secretária no Claude ──► BRM AI Framework ──► BRM Agenda
                         (quem pode? registra,   (dono do cadastro,
                          aprova, emite PDF)      agenda e financeiro)
```

## 2. Regras que valem para a integração

1. **Sem cópia de paciente.** O framework lê a ficha no Agenda na hora de usar. A única cópia dos dados
   fica congelada dentro do documento emitido (por exemplo, o contrato assinado), como prova.
2. **Campo faltante vai para o Agenda.** Se falta CPF ou endereço, o framework pede a quem está
   atendendo e grava no Agenda. Não guarda do lado dele.
3. **Todo pedido do framework ao Agenda diz quem pediu.** Cabeçalhos `X-BRM-Actor` (pessoa que pediu)
   e `X-BRM-Trace-Id` (rastro que liga o pedido ao registro do framework). O Agenda deve gravar os dois
   no histórico da alteração.
4. **O chat nunca recebe senha.** Ações sensíveis são confirmadas numa página do próprio framework
   (link de uso único, 30 minutos), e não digitadas no chat.
5. **Papel e unidade vêm do login do Agenda.** O framework não mantém usuários próprios no ambiente da
   clínica; valida o token do Supabase Auth do Agenda.

## 3. Como o framework fala com o Agenda hoje

- Leitura com a `api_key` do estabelecimento (`Authorization: Bearer`): `/api/v1/appointments`,
  `/api/v1/services`, `/api/v1/staff`, `/api/v1/customers`.
- Ficha: `GET /api/v1/customers/:id/ficha` mascarada para consultar o histórico e a pendência. O
  CPF e o endereço completos (`unmask=1`, com `purpose`) só são pedidos ao preparar ou emitir o
  contrato, sempre com `X-BRM-Actor` e `X-BRM-Trace-Id`.
- Escrita: `PATCH /api/v1/customers/:id/ficha`, com ator e trace, para gravar o campo que faltava, e
  `PATCH /api/v1/clinic/appointments/:id/status`, com ator e trace, para mudar o status da sessão
  (sempre depois de a pessoa confirmar na Central de Ações; cancelar exige liberação do gestor).
  Como a Agenda substitui a lista de responsáveis, o framework reenvia todos pelo `id` e muda só o
  responsável financeiro (ou o primeiro).
- Login: valida o JWT do Supabase e lê o papel da claim `brm_role` (hook de token do Agenda).
- Financeiro: `/api/v1/finance/*` e `/api/v1/clinic/*` conforme a spec 032.

## 4. Fluxo que motiva os pedidos: contrato da sessão

Regra da clínica: **ao fim da 1ª sessão de todo paciente** (pacote, avulsa ou recorrente) o paciente
assina um contrato. Faltar na 1ª sessão não conta.

1. O framework detecta "1ª sessão concluída e sem contrato" (precisa de G15) e cria uma pendência.
2. A secretária pede o contrato no chat. O framework lê a ficha no Agenda (G10, G11).
3. Se falta CPF, endereço ou responsável legal, o framework avisa o que falta. O dado é informado numa
   página do framework e gravado no Agenda (G12), com ator e trace.
4. O framework gera o PDF com os dados da ficha. A secretária baixa e imprime.
5. O framework guarda o PDF emitido (imutável, com hash) e o registro de quem emitiu.

## 5. Pedidos ao time do Agenda

Situação em 2026-11-01. G10, G11, G12, G14, G15 e G16 estão entregues; o framework já os usa.

| # | Pedido | Situação |
|---|---|---|
| G10 | CPF único por tenant e endereço estruturado em `customers` | Entregue (`customer_pii`, mascarado na leitura) |
| G11 | Responsável legal (`customer_guardians`) | Entregue |
| G12 | Atualização de cadastro por integração com `X-BRM-Actor` e `X-BRM-Trace-Id` | Entregue (`PATCH .../ficha`) |
| G13 | TOTP para `ADMIN` e `MASTER`, com `aal2` no JWT | **Pendente.** Até lá o framework usa o PIN do gestor |
| G14 | Unidade de quem não é profissional (`profile_locations`) | Entregue (`whoami` e `PUT .../locations`) |
| G15 | Primeira sessão concluída por paciente | Entregue (`historico` e `primeira_sessao_concluida_em` na ficha; `first-session-completed`) |
| G16 | Papel e tenant no JWT | Entregue (`brm_role`, `brm_tenant_id`) |
| G17 | `GET /api/v1/appointments/:id` | Entregue pelo Agenda (2026-11-03), aguardando o deploy do Worker. O framework já a usa e, enquanto ela não existir, procura na lista dos últimos 120 dias |
| G18 | `PATCH /api/v1/clinic/appointments/:id/status` aceita `IN_PROGRESS` | Entregue pelo Agenda (2026-11-03), aguardando o deploy do Worker. Ação `agenda.atualizar_status_sessao` já aceita "em atendimento"; só passa a funcionar quando a rota estiver no ar |
| G19 | CRP no cadastro do profissional: `crp` em `GET /api/v1/staff` e `PUT /api/v1/clinic/staff/:id/crp` | Entregue pelo Agenda (2026-11-03), depende da migration 117 e do deploy do Worker, nessa ordem. O contrato lê o CRP da Agenda e, na falta dele, da chave `crp_<nome>` do cliente; ação `agenda.definir_crp_profissional` (gestor) grava |

**CPF e endereço são dado pessoal (LGPD).** Pedimos que a entrega venha com a regra de quem vê o CPF na
tela do Agenda e com máscara na API de leitura. O framework só desmascara na hora de emitir o documento.

## 6. Como testamos sem o Agenda

Existe um Agenda simulado (`mcp_engine/modules/agenda/fake.py`) com fichas completas, incompletas e de
menor de idade. Todos os testes do fluxo do contrato rodam nele. Quando G10 a G15 existirem, o provider
HTTP é preenchido e os mesmos testes passam a valer contra o Agenda real.

## 7. O que o Agenda não precisa fazer

- Não precisa gerar o contrato, o PDF nem guardar o documento emitido.
- Não precisa conhecer o Claude, o MCP nem o framework além das rotas acima.
- Não precisa criar usuários para o framework: ele valida o login do próprio Agenda.

## 8. Como o texto do PDF é escrito (decisão tomada)

O texto do contrato **não é escrito pelo chat nem por código por tipo de contrato**. O modelo é um
texto no segundo cérebro (aprovado pelo gestor, com hash) com lacunas como `{{ paciente.nome }}`.
O framework preenche as lacunas com os dados da ficha lidos do Agenda na hora; o chat não vê o CPF
nem o endereço. Lacunas que não vêm do cadastro (por exemplo `{{ valor_pacote }}`) são informadas
pela secretária e conferidas antes da emissão. Contrato novo = um arquivo de texto novo.
Para o Agenda isso não muda nada.

## 9. Perguntas para o time do Agenda

1. G17 é simples de entregar junto das rotas clínicas?
2. O TOTP (G13) tem data? Sem ele, o piloto usa o PIN do gestor.
3. O hook de token já está ativo no ambiente do Weis? Tokens emitidos antes da ativação não têm
   `brm_role` e são recusados até o refresh.

## 10. Respostas do Agenda e o que decidimos (2026-11-03)

- **Pagamento em dinheiro com a fatura aberta:** o valor entra como crédito do paciente e é aplicado
  quando a fatura fecha (rotina da madrugada seguinte ao vencimento); com fatura antiga fechada, quita
  essa primeiro. A página de confirmação do framework avisa isso antes de o pagamento ser confirmado.
- **Recibo:** o Agenda vai incluir, na resposta da confirmação de pagamento, os dados para imprimir o
  recibo (número sequencial, clínica, paciente, profissional, valor, forma, data, referência) e uma rota
  para reimprimir. O framework gera o PDF a partir desse payload quando existir. Sem data.

## 11. Entregas do Agenda de 2026-11-05 e o que o framework passou a fazer

- **Pagamento (migration 118):** a ação `agenda.registrar_pagamento` já avisa na página se o valor
  bate com o saldo (baixa na hora, recibo) ou não (fica `PENDING_REVIEW` até a gerência). Repetição
  (HTTP 200) vira "já estava registrado". A ação do gestor `agenda.confirmar_pagamento` chama
  `POST /clinic/payments/:id/confirm`.
- **Recibo:** `agenda.emitir_recibo_pagamento` relê `GET /clinic/payments/:id/receipt` (CPF sempre
  mascarado; o framework nunca pede `unmask`) e gera o PDF a partir do modelo `recibo-pagamento` do
  segundo cérebro (aprovado pelo gestor, com hash). Skill de leitura: `agenda_recibo_pagamento`.
- **Bônus de indicação:** skill de leitura `agenda_bonus_indicacao` (gestor) e ações do gestor
  `agenda.definir_corte_bonus_indicacao` e `agenda.definir_aptidao_bonus_indicacao`.
- **A confirmar com o Agenda:** os nomes exatos dos campos do `receipt` (o framework lê `number`,
  `amount`, `amount_in_words`, `paid_at`, `method`, `payer.name`, `payer.cpf`, `professionals[].name`
  e `.crp`, `items[].description`, `registered_by`) e do `GET /clinic/staff/referral-bonus-eligibility`
  (`id`, `referral_bonus_eligible`, `patients_6m_count`). Ajustar em `recibo.py` se divergirem.
