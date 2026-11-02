# 034 — Trilha por pessoa com poucas contas do Claude: canais, identidade e auditoria (spec)

**Status:** proposta, aguardando aprovação (2026-10-31). Revê a seção "Opções de identidade" da
[033](033-acesso-da-equipe-identidade-senha-gestor-e-nuvem.md).

**Origem:** o Weis terá **duas contas do Claude**: uma para as secretárias e outra para a gestão; no
futuro, os psicólogos. Para cada conta é preciso saber **quem pediu o quê** pelo chat. O dono pediu
análise em profundidade.

## A restrição que muda o desenho

Verificado em 2026-10-31 nas fontes oficiais:

- Plano Team ([Central de Ajuda](https://support.claude.com/en/articles/12004354-purchase-and-manage-seats-on-team-plans)):
  "Each seat on a Team plan is assigned to one named individual, and only that person may use it."
  "Users can't share login credentials or let others use their account." "It isn't designed for
  rotating one seat among several active users."
- Planos individuais ([Termos do Consumidor](https://www.anthropic.com/legal/consumer-terms), seção 2):
  "You may not share your Account login information [...] with anyone else." "You also may not make
  your Account available to anyone else."

**Uma conta usada por várias secretárias viola os termos da Anthropic**, em qualquer plano. As
consequências:

- **Operacional:** suspensão da conta deixa a clínica sem a ferramenta no meio do mês.
- **Comercial:** a BRM estaria vendendo uma arquitetura que depende de o cliente violar o contrato com
  um fornecedor. Isso não entra em proposta nem em contrato.
- **Técnica:** mesmo ignorando os termos, a trilha por pessoa seria fraca (seção seguinte).

## O que o servidor MCP enxerga

Quando o Claude chama uma tool por um conector remoto, o BRM recebe:

| Recebe | Não recebe |
|---|---|
| Nome da tool e argumentos | O texto que a pessoa digitou |
| O token OAuth do conector, que identifica a **conta** Claude que autorizou | Quem está diante do teclado |
| Requisição vinda da nuvem da Anthropic (o IP não é o da clínica) | Qual conversa originou a chamada. Sessão MCP estável por conversa **não é garantida**: relatos divergem, e há indicação de que a versão nova do protocolo eliminou a sessão; não verifiquei na especificação |

Consequência: **em conta compartilhada o servidor nunca distingue pessoas.** Tudo o que se consegue é
uma declaração ("sou a Joana") feita dentro de um chat que todas as colegas da conta conseguem ler e
continuar.

## Alternativas avaliadas para conta compartilhada (rejeitadas)

| Variante | Como funcionaria | Por que falha |
|---|---|---|
| Declaração | O modelo pergunta o nome e o passa em cada tool | Atribuição sem prova; o modelo pode trocar ou esquecer |
| Código de uso único no chat (TOTP da pessoa) | Ao abrir a conversa, a secretária digita o código do autenticador dela; o motor devolve um "crachá" que o modelo passa nas tools | O crachá fica no histórico, visível à colega, que pode continuar a mesma conversa ou copiá-lo enquanto valer. Prova quem **abriu**, não quem **pediu** |
| Um conector por pessoa na mesma conta ("BRM - Joana", "BRM - Ana") | Cada uma autoriza o seu com senha | Autorização fica salva na conta; qualquer usuária da conta liga o conector da outra |
| Turno por conta | A secretária faz check-in num site e tudo da conta vira dela até o check-out | Quebra com duas secretárias ao mesmo tempo; esquecer o check-out atribui ações à pessoa errada |

Todas continuam violando os termos. **Nenhuma entra no produto.**

## Modelo proposto: cada canal identifica a pessoa por conta própria

O motor passa a aceitar mais de um **canal**. Todos chegam às mesmas tools governadas (RBAC, regras,
DLP, confirmação, auditoria), mas cada um prova a pessoa do seu jeito.

| Canal | Quem usa | Como prova a pessoa | Vê o pedido em texto? |
|---|---|---|---|
| **Claude (conector remoto)** | Quem tem **assento nomeado** | OAuth 2.1 com login BRM por pessoa (033) | Não |
| **Assistente BRM** (chat dentro do BRM Agenda, usando a API do Claude) | Quem não tem assento: secretárias, psicólogos | Login do Agenda; o motor valida o JWT da pessoa | **Sim, literal** |
| Painel BRM | Gestor, super_admin | Login BRM | Não se aplica |
| stdio | Desenvolvimento e demo | Token local | Não |

### Princípio do produto: um conector por papel, um login por pessoa, a porta é do cliente

Decisão de produto (2026-10-31): a BRM vende a governança, agnóstica do chat.

- **Conector por papel.** O mesmo motor publica um endereço por papel (`/mcp/secretaria`,
  `/mcp/gestao`, `/mcp/psicologo`). Cada um expõe só as tools e as regras daquele papel: o modelo erra
  menos e a superfície de ataque diminui. O RBAC continua valendo dentro de cada conector.
- **Login por pessoa.** Quem conecta faz o login BRM (OAuth); a trilha registra a pessoa. A identidade
  fica no BRM, não no fornecedor do chat.
- **A porta é escolha do cliente, por pessoa:** Claude (Free, Pro, Team), outro cliente que fale MCP,
  o Assistente BRM e, no futuro, o WhatsApp. Conta de chat compartilhada não é suportada: a trilha só
  diria o papel, e o risco de suspensão é do cliente.

### O Weis em números

3 unidades, 2 secretárias por unidade (**6 secretárias**), **2 pessoas na gestão**, psicólogos depois.

| Arranjo | Custo de contas para o cliente | Trilha | Observação |
|---|---|---|---|
| Todos no Claude Team (8 assentos) | ~US$ 160 a 200/mês, ~R$ 835 a 1.045 (câmbio de R$ 5,22; preços de terceiros, confirmar) | Por pessoa, sem o texto literal | Mais que o dobro da mensalidade de R$ 399; provável trava de venda para clínica pequena |
| Gestão no Team (2) + secretárias no Claude Free | ~US$ 40 a 50/mês | Por pessoa | Free aceita um conector (cabe o `/secretaria`), mas o limite de uso provavelmente não aguenta um dia de recepção, e conta de consumidor é ruim para dado de paciente |
| **Gestão no Team (2) + secretárias no Assistente BRM** | ~US$ 40 a 50/mês + tokens (BRM) | Por pessoa, **com texto literal** | Cabe nas "2 contas" planejadas; exige construir o assistente |

**Recomendação para o Weis, em duas etapas:**

1. **Piloto (uma unidade):** gestão com 2 assentos Team; as 2 secretárias da unidade piloto com assentos
   Team próprios, na mesma organização (4 assentos no total). Conectores `/gestao` e `/secretaria` com
   login por pessoa. Nada de interface nova; mede-se o uso real (mensagens por dia, tokens por pedido).
2. **Expansão (3 unidades):** com o uso medido, decidir entre manter Team para as 6 secretárias ou
   levá-las ao Assistente BRM. É uma conta de custo de assentos contra custo de tokens e de
   desenvolvimento, feita com números do piloto.

### Unidade como parte da identidade

Cada pessoa ganha `unidades` (uma ou mais). Vira fato derivado nas regras:

- secretária agenda, cancela e registra pagamento nas próprias unidades (ou em todas, se a clínica
  quiser cobertura entre unidades: pergunta 2);
- gestão vê todas;
- psicólogo vê só a própria agenda, em qualquer unidade em que atenda (`staff_locations` do Agenda);
- a trilha filtra por unidade.

### Psicólogos

Assistente BRM ou assento próprio. Nunca conta compartilhada: o histórico compartilhado expõe pacientes
de um profissional a outro, o que conflita com o sigilo do CFP.

O Assistente BRM resolve três pedidos de uma vez:

1. **Trilha literal:** o BRM hospeda a conversa e guarda o que foi digitado, por pessoa.
2. **Senha do gestor digitada de verdade:** um diálogo do BRM, fora do contexto do modelo, recebe senha e
   TOTP. A senha nunca entra no prompt.
3. **PC compartilhado da recepção:** troca rápida de operador (cada secretária entra com o próprio login
   ou PIN do BRM) e bloqueio por inatividade.

### Como o Assistente BRM usa o Claude (verificado em 2026-10-31)

- **Login da assinatura não pode.** A documentação do Agent SDK diz: "Unless previously approved,
  Anthropic does not allow third party developers to offer claude.ai login or rate limits for their
  products, including agents built on the Claude Agent SDK." A extensão do VS Code usa o login da
  assinatura porque é produto da própria Anthropic (Claude Code); a BRM não pode repetir isso.
  Além disso, o assento Team é de uma pessoa nomeada e os termos proíbem "route their work through
  another user's account".
- **Os créditos de API do Team podem.** Planos Team incluem crédito mensal de API: US$ 20 por assento
  padrão e US$ 100 por assento premium, somados num saldo único com teto de US$ 500. Os créditos valem
  para a API do Claude e para o Agent SDK; não acumulam de um mês para o outro. O dono da organização
  Team liga uma organização do Console ao plano, e a chave de API sai dessa organização.
- **Modelo "chave do cliente".** A clínica cria a chave na própria organização do Console (num
  workspace com limite de gasto) e a entrega à BRM pelo cofre de segredos. O contrato com a Anthropic
  continua com a clínica, como a proposta já prevê. Quando o crédito acaba, as chamadas param, a não
  ser que a clínica compre crédito adicional ou ligue a recarga automática.
- **SDK:** o assistente roda dentro do motor (Python) com o SDK cliente da Anthropic e o laço de tools
  do próprio motor, chamando o mesmo caminho governado (`call_skill`). O Agent SDK também serve, mas
  embute o binário do Claude Code com ferramentas de arquivo e terminal, mais pesado do que um chat de
  recepção precisa.
- **Custo de tokens:** não estimado. Medir no piloto e comparar com o crédito do plano.

Fontes: [Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview),
[créditos de API para Team](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers).

## Modelo de identidade no motor

| Entidade | Campos principais |
|---|---|
| Pessoa | `pessoa_id`, nome, e-mail, papéis, `staff_id` do Agenda (quando profissional), ativa |
| Credencial | tipo (`senha`, `totp`, `oauth_cliente`, `jwt_agenda`, `token_estatico`), criada, revogada |
| Sessão | `sessao_id`, `pessoa_id`, canal, `auth_time`, métodos usados (`amr`: senha, TOTP), expira, revogada |
| Canal | `claude_conector`, `assistente_brm`, `painel`, `stdio` |

- **Papéis configuráveis** por cliente (`recepcao`, `gestor`, `profissional`, `super_admin`), como na 033.
- **Nível de garantia** de cada sessão: `NG1` (senha), `NG2` (senha e TOTP). Skill N3 exige `NG2`
  recente (por exemplo, `auth_time` de menos de 5 minutos) ou liberação do gestor.
- **Assistente BRM:** o front do Agenda manda o JWT da própria pessoa e o motor o valida contra o
  emissor do Agenda. O backend do assistente **não afirma** identidade; repassa a prova. Assim um defeito
  no assistente não permite agir como outra pessoa.
- **Desativar a pessoa** revoga sessões e tokens em todos os canais.

## Senha do gestor por canal

| Canal | Como acontece |
|---|---|
| Assistente BRM, secretária pedindo | O BRM abre um diálogo próprio (fora do chat do modelo) com o resumo gerado pelo motor; o gestor digita senha e TOTP ali mesmo, no PC da recepção. Liberação de uso único, amarrada ao hash do pedido |
| Claude, gestor no próprio assento | Reautenticação com TOTP quando a sessão tiver mais de 5 minutos, pelo link de liberação da 033 |
| Claude, qualquer outro caso | Link de liberação da 033 |

A regra dura continua: **senha nunca no texto da conversa**. O DLP de entrada ganha um padrão para
frases como "minha senha é" que nega e alerta.

## Trilha de auditoria: o que registrar

A trilha tem quatro camadas, ligadas pelo `trace_id`:

| Camada | Conteúdo | Assistente BRM | Conector Claude |
|---|---|---|---|
| **Pedido** | O que a pessoa escreveu | Texto literal, com DLP aplicado antes de gravar | Indisponível; o modelo preenche `pedido_resumo` na tool, gravado como **"resumo do modelo"**, não como fala da pessoa |
| **Decisão** | Tool, argumentos (mascarados), fatos derivados, regras avaliadas, decisão, nível | Sim | Sim |
| **Efeito** | Chamada ao Agenda com `X-BRM-Actor` e `X-BRM-Trace-Id`, id devolvido (`payment_id`, `appointment_id`), resultado | Sim | Sim |
| **Prova** | Confirmação, aprovação ou liberação: quem, método (`amr`), quando, hash do pedido | Sim | Sim |

Campos de identidade em **todo** evento: `pessoa_id`, papel, canal, `sessao_id`, nível de garantia,
`auth_time`, e no conector também a identificação do cliente OAuth.

Pedido com mais de uma tool (por exemplo, "remarca a Maria para quinta e registra o Pix dela") gera um
`trace_id` por mensagem da pessoa e um evento por tool, todos ligados.

### Integridade, retenção e acesso

- **Encadeamento por hash** (já existe) mais **âncora diária externa**: o hash do fim do dia é gravado
  em armazenamento de objetos com retenção imutável e enviado ao gestor. Assim nem a própria BRM
  reescreve o passado sem ser notada, o que importa porque a BRM opera o sistema.
- **Retenção:** proposta de 5 anos para eventos de efeito financeiro e de agenda, e prazo menor para o
  texto literal das conversas (ex.: 12 meses). **Decidir com o jurídico da clínica.**
- **Acesso:** gestor vê a trilha da própria clínica no painel, filtra por pessoa, período e tool, e
  exporta CSV. Secretária vê só o próprio histórico. Toda consulta à trilha também é auditada.
- **Minimização:** paciente gravado por `customer_id`; nome só na camada Pedido do assistente, sob a
  política de retenção curta.

### Perguntas que a trilha precisa responder (critério de aceite)

1. O que a Joana pediu ontem, com o texto dela, e o que o sistema fez em cada pedido?
2. Quem registrou o pagamento `X` no Agenda, por qual canal, e quem confirmou?
3. Quem liberou o relatório financeiro de outubro, com qual método, e para quem?
4. Quais pedidos foram negados esta semana, por qual regra?
5. Partindo de um `payment_id` no Agenda, chegar à mensagem que originou o pedido.

## Red Team / Blue Team

| Risco | Controle | Teste |
|---|---|---|
| Clínica volta a compartilhar uma conta "para economizar" | Termos informados por escrito; o motor alerta quando o mesmo cliente OAuth aparece com padrões de uso de várias pessoas (horários sobrepostos, papéis distintos pedidos) | Simulação no piloto |
| Secretária usa a sessão aberta da colega no PC da recepção | Bloqueio por inatividade, troca rápida de operador, sessão por pessoa | Teste de bloqueio após inatividade |
| Assistente BRM afirma uma identidade falsa | Motor só aceita o JWT emitido pelo Agenda; o backend do assistente não assina identidade | Requisição com JWT de outro emissor é recusada |
| Senha digitada no chat | Diálogo próprio; DLP nega o padrão; alerta | Frase de teste é bloqueada |
| `pedido_resumo` do modelo diverge do que foi pedido | Gravado como resumo do modelo; a fonte de verdade da ação são os argumentos e o efeito | Rótulo conferido no painel |
| Trilha adulterada por quem opera o servidor | Cadeia de hash e âncora diária imutável fora do servidor | Alteração de uma linha antiga é detectada |
| Texto literal guarda dado sensível demais | DLP antes de gravar, retenção curta, acesso só do gestor | Varredura de CPF e telefone na trilha |
| Emissor do Agenda fora do ar | Falha fechada no assistente; gestão segue pelo Claude; acesso de emergência auditado | Teste com Agenda desligado |

## Postmortem antecipado

1. **"Duas contas" vira dez pessoas numa conta.** Suspensão pela Anthropic e trilha sem valor. Evitado
   por esta spec não oferecer esse caminho e pelo alerta de uso.
2. **Secretárias preferem o Claude ao Assistente BRM.** Se o assistente for pior, elas burlam. Ele
   precisa ser focado: agenda, pagamento, consulta, com confirmações em botão.
3. **Custo de tokens maior que o previsto.** Medir no piloto, limitar contexto e histórico, e revisar a
   mensalidade.
4. **Trilha cresce sem dono.** Política de retenção e expurgo automático desde o início.

## Os cinco pilares

| Pilar | Evidência |
|---|---|
| Red Team | Tabela acima |
| Blue Team | Validação do JWT do Agenda, OAuth por pessoa, bloqueio de sessão, âncora diária, testados |
| Postmortem | Seção acima |
| God Mode | Do `payment_id` no Agenda até a mensagem literal, por `trace_id`; painel por pessoa e canal |
| L-99 | Invariantes: "nenhum evento sem pessoa", "nenhuma identidade afirmada por intermediário", "senha nunca no prompt", "passado imutável e verificável fora do servidor" |

## Plano de entrega

| Fase | Entrega | Critério de pronto |
|---|---|---|
| 1 | Modelo de identidade (pessoa, sessão, canal, nível), papéis configuráveis, campos de identidade em todo evento, validação do JWT do Agenda | Evento de teste traz pessoa, canal e nível; JWT de outro emissor é recusado |
| 2 | Conectores por papel (`/mcp/secretaria`, `/mcp/gestao`) com OAuth por pessoa (033) e unidade na identidade | No piloto, 4 pessoas aparecem como pessoas distintas, cada uma só com as tools do seu papel e da sua unidade |
| 3 | Âncora diária, retenção, exportação e consulta por pessoa e unidade no painel | Adulteração detectada; expurgo roda |
| 4 (condicional ao piloto) | Assistente BRM: painel de chat no Agenda, laço de tools no motor, gravação literal com DLP, troca de operador, diálogo de senha do gestor | As 5 perguntas da trilha respondidas a partir de um dia de uso simulado |

## Impacto comercial

- A proposta do Weis diz que o acesso ao Claude é contratado pelo Instituto. Com o Assistente BRM, parte
  do uso passa a ser custo da BRM (tokens), e a BRM vira a parte contratante da Anthropic para esse
  canal. Revisar mensalidade, contrato de tratamento de dados e lista de suboperadores.
- Informar por escrito ao cliente que contas do Claude não podem ser compartilhadas.

## Perguntas em aberto

1. O dono aceita o piloto em uma unidade com 4 assentos Team (2 da gestão e 2 secretárias) e a decisão
   sobre as outras 4 secretárias depois do piloto?
2. Secretária opera só a própria unidade ou também cobre as outras?
3. As 2 secretárias de cada unidade dividem o mesmo computador? (Com Claude, cada uma precisa de perfil
   próprio no navegador ou usuário próprio no Windows; com o Assistente BRM, basta trocar de operador.)
   Quem são as duas pessoas da gestão?
4. Retenção: 5 anos para efeitos e 12 meses para texto literal servem? Confirmar com o jurídico.
5. Quem paga os tokens do assistente: embutido na mensalidade ou repassado por uso?
6. Psicólogos vão tratar dado clínico pelo chat? Se sim, assento próprio ou assistente, nunca conta
   compartilhada.
7. O Agenda emite JWT com o método de autenticação (`amr`) e o `aal`? Se não, o nível de garantia vem
   do próprio motor (TOTP do BRM).

## Fora de escopo

Biometria, SSO corporativo, gravação de tela, assinatura digital ICP-Brasil das liberações.
