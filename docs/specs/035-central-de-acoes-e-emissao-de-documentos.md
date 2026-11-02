# 035 — Central de Ações por link, identidade única com o Agenda e emissão de documentos (spec)

**Status:** backend implementado (2026-10-31); Agenda entregou G10 a G12, G14 a G16 em 2026-11-01 (ver changelog); front da Central de Ações pendente. Ver "Como ficou a implementação" no fim. Ajusta as specs 032, 033 e 034.

**Origem:** conversa com o dono (2026-10-31). O chat deixa de executar ações sensíveis e passa a
**preparar** ações que a pessoa confirma numa página do framework, logada com a própria conta. O
brm-ai-framework vira o intermediário de governança entre o chat e os produtos BRM (hoje o Agenda,
amanhã outros), e é ele quem guarda segundo cérebro, regras, trilha e documentos emitidos. Primeiro caso:
o contrato de sessão do Weis.

## Decisões

1. **Plano de controle e sistemas de registro.** O framework governa, registra e documenta. Os produtos
   BRM são donos dos dados do negócio. O framework **não copia** cadastro de paciente: guarda só a
   referência (`customer_id` do Agenda) e busca o dado na hora do uso.
2. **Um único conector MCP.** Os conectores por papel da 034 deixam de ser necessários (detalhes
   abaixo).
3. **Central de Ações:** front do próprio framework onde o link abre, a pessoa entra e confirma.
4. **Login único com o Agenda:** o Agenda é o provedor de identidade; o framework não guarda senha nem
   sincroniza usuários.
5. **Emissor de documentos determinístico:** o modelo de linguagem não escreve o contrato; o motor
   preenche o modelo e gera o PDF.
6. **Gerador de modelos (LLM) agnóstico:** o agente atual, escrito para advocacia, passa a servir
   qualquer segmento.

## Fronteira de dados

| No produto (dono do dado) | No framework |
|---|---|
| Cadastro do paciente: nome, telefone, e-mail, nascimento, CPF, endereço, responsável legal | Referência `customer_id`, nunca a cópia do cadastro |
| Agenda, sessões, faturas, pagamentos, repasse | Regras, ações por link, liberações do gestor |
| Usuários, senhas, papéis, MFA (Supabase Auth do Agenda) | Atributos de governança por usuário do Agenda, quando o Agenda não os tiver |
| | Trilha de auditoria de chat, link e efeito |
| | Segundo cérebro: procedimentos, modelos de documento, memória, **sem dado identificável** |
| | **Documentos emitidos**: PDF, hash, versão do modelo e os dados congelados daquele documento |

A única cópia de dado pessoal permitida é a de dentro do **documento emitido**, porque ele registra o
que foi assinado naquele dia.

## Um conector, política por conta

Com as ações sensíveis indo por link, o conector deixa de carregar autoridade. Basta um conector,
`/mcp`, com uma credencial por conta de chat. O papel daquela conta define o que o chat responde direto:

| Classe | Exemplos | Onde a resposta aparece |
|---|---|---|
| Sem dado pessoal | horários livres, serviços, procedimentos e regras do vault | No chat |
| Dado mínimo de agenda (opcional, por cliente) | "14h, Maria S., Dra. Ana, unidade Centro" | No chat, só se `client_config` permitir para o papel da conta |
| Dado pessoal, financeiro ou clínico | ficha do paciente, saldo, relatórios, contrato | **Link** para a Central de Ações |
| Efeito (grava ou envia) | agendar, cancelar, registrar pagamento, emitir contrato | **Link**, com confirmação por PIN ou senha da pessoa |
| Sensível de gestão | relatórios administrativos e financeiros, repasse | **Link**, com senha e TOTP do gestor |

O conector continua autenticado, mesmo sem autoridade: impede abuso público (qualquer um na internet
criando links), limita a taxa e registra qual conta pediu. Um conector também cabe no limite do plano
Free do Claude (um conector personalizado).

**Os termos da Anthropic não mudam com isso:** uma conta do Claude por pessoa (034). O conector único
reduz o que o chat consegue fazer, não quem pode usar a conta.

Conectores por papel voltam a fazer sentido só se, no futuro, um papel puder **executar** direto pelo
chat.

## Central de Ações

| Aspecto | Regra |
|---|---|
| Endereço | `https://<cliente>.<dominio-brm>/acao/<id>` |
| `id` | Aleatório, 128 bits. **Nenhum parâmetro na URL**: o pedido fica no servidor, com hash dos argumentos |
| Validade | 30 minutos por padrão, configurável por tipo de ação; uso único |
| Quem executa | Quem entrou na página, com as permissões **dele**. O link não dá poder: vazado, não faz nada sem login |
| Página | Mostra "Você está como Joana. Não é você? Trocar", o resumo gerado pelo motor (nunca pelo modelo), qual conta pediu e quando |
| Confirmação | PIN ou senha da pessoa em cada ação com efeito (o PC da recepção é compartilhado); senha e TOTP do gestor para ações sensíveis de gestão |
| Dados ao vivo | Lidos do produto no momento em que a página abre; relatório é gerado na hora do clique |
| Campo faltante | Preenchido na página e **gravado no produto** (ex.: CPF no Agenda), com ator e `trace_id` |
| Lote | Um link para revisar e confirmar várias ações do mesmo pedido |
| Retorno ao chat | Tool `status_da_acao(id)`: pendente, confirmada, expirada, falhou |
| Trilha | Costurada pelo `id`: lado do chat (conta, resumo do pedido, link criado) e lado da página (pessoa, método de login, confirmação, efeito com `payment_id` ou `appointment_id`) |

A página é do framework, não do Agenda, para regras, senha e trilha ficarem num lugar só e servirem a
futuros produtos.

## Identidade única com o Agenda

- **Provedor de identidade:** o Supabase Auth do Agenda. A página da Central de Ações tem login próprio
  que autentica no Agenda com o mesmo e-mail e senha. O framework valida o JWT emitido pelo Agenda e
  não guarda senha. Não há sincronização de usuários.
- **Papéis:** o Agenda tem `SUPER_ADMIN`, `MASTER`, `ADMIN`, `USER` e `READONLY`. O framework mapeia
  esses papéis para os seus (`super_admin`, `gestor`, `recepcao`, `profissional`) em `client_config`, e
  o vínculo profissional ↔ usuário vem da migration 107 do Agenda.
- **MFA:** o código do Agenda não usa MFA hoje (nenhuma ocorrência de `mfa`, `aal2` ou `totp`). O Supabase
  Auth oferece TOTP; pedir ao Agenda que ative para `ADMIN` e `MASTER` (G13). Com isso, "senha do
  gestor" vira exigir JWT com `aal2` e autenticação recente.
- **Interface plugável:** `IdentityProvider` com `agenda` (padrão para clientes do Agenda) e `local`
  (clientes sem Agenda, e acesso de emergência auditado).
- **Login do conector do chat:** a mesma tela de login, via OAuth do framework, para a credencial de
  cada conta de chat.

## Contrato de sessão (Weis)

### A regra da clínica

**Todo paciente assina contrato no final da primeira sessão**, seja avulso, pacote ou recorrente. Hoje
as secretárias guardam isso de cabeça.

| Peça | Como o motor aplica |
|---|---|
| Gatilho | Primeira sessão do paciente que vira `COMPLETED` no Agenda (gatilho da spec 031 lendo o Agenda) |
| Pendência | Criada automaticamente: "contrato pendente: Maria S., sessão de hoje com a Dra. Ana". Aparece no chat quando a secretária pergunta o que falta e numa lista diária |
| Fatos derivados | `primeira_sessao_concluida`, `contrato_emitido` (consulta aos documentos emitidos no framework) |
| Regra dura | Sem contrato emitido, não se agenda a segunda sessão nem se vende pacote. Exceção só com liberação do gestor, auditada |
| Falta na primeira | `NO_SHOW` não conta como primeira sessão; a próxima `COMPLETED` dispara |

### Fluxo

1. A secretária pede no chat: "prepara o contrato da Maria da sessão de hoje com a Dra. Ana".
2. O framework acha a Maria e a sessão de hoje no Agenda, escolhe o modelo de contrato no segundo
   cérebro e devolve um link da Central de Ações.
3. A secretária clica e entra com o login dela (o mesmo do Agenda). A página mostra a prévia com dados
   buscados ao vivo: paciente, responsável legal se menor, profissional, unidade, data, modalidade e
   preço da sessão (o preço congelado no agendamento).
4. Campo faltante (CPF, endereço, responsável) é preenchido ali e gravado no Agenda.
5. Ela confirma com o PIN. O framework gera o PDF, guarda com hash, versão do modelo e dados usados, e
   registra na trilha. A pendência fecha.
6. Ela baixa e imprime pela página. No chat aparece só "contrato pronto" e o link.

O PDF **não volta dentro do chat**: assim CPF e endereço nunca passam pelo modelo nem ficam no
histórico da conversa.

### Assinatura

Fase 1: impresso e assinado no papel. Fase 2: anexar a via assinada digitalizada pela mesma página
(fecha o ciclo com prova). Assinatura eletrônica fica para spec própria.

### Paciente menor

O Weis atende crianças e adolescentes. O contrato de menor é assinado pelo responsável legal, então o
modelo exige os dados do responsável quando `birth_date` indicar menor de idade.

## Emissor de documentos (novo, núcleo agnóstico)

- **Modelos** ficam no vault, em `_modelos/`, com front matter que declara cada campo: nome, tipo,
  origem (`paciente.cpf`, `sessao.preco`, `clinica.razao_social`), obrigatório ou não.
- **Preenchimento determinístico:** placeholders tipados em linguagem de modelo com sandbox. Campo
  obrigatório ausente gera erro, nunca texto inventado. O modelo de linguagem não participa.
- **PDF:** biblioteca escolhida na implementação (HTML para PDF ou geração direta), com fontes
  embutidas para o resultado ser igual em qualquer máquina.
- **Guarda:** PDF, SHA-256, versão do modelo, dados usados, quem emitiu e quando. Documento emitido não
  se edita; correção é nova emissão que aponta para a anterior.
- **Agnóstico:** o núcleo não conhece "contrato de sessão". O Weis tem o modelo; outro cliente tem os
  seus.

## Gerador de modelos (LLM), tornado agnóstico

O `document_generator.py` atual é um agente que **redige modelos** para o vault, com rascunho e
aprovação humana. O prompt está escrito para escritório de advocacia.

- O prompt passa a vir do segmento do cliente (`client_config.context` e uma nota do vault), sem
  menção fixa a advocacia.
- A saída passa a incluir a **declaração dos campos** que o emissor vai preencher.
- Continua sem preencher dado de paciente: ele redige o modelo, e o emissor preenche.
- Aprovação do gestor antes de o modelo valer (como hoje).

## Pedidos ao time do Agenda

O cadastro de paciente hoje tem `name`, `phone`, `email`, `birth_date`, `tags`, `notes` e `deleted_at`.
Não tem CPF, endereço nem responsável legal.

| # | Pedido | Por quê |
|---|---|---|
| G10 | `customers`: `cpf` (único por tenant, validado), endereço estruturado (CEP, logradouro, número, complemento, bairro, cidade, UF) | Contrato e futura NFS-e precisam; o Agenda é o dono do cadastro |
| G11 | Responsável legal: tabela `customer_guardians` (nome, CPF, parentesco, telefone, e-mail, se é o responsável financeiro) | Paciente menor; o Weis tem unidades infantil e adolescente |
| G12 | Rota de atualização de cadastro por integração, com `X-BRM-Actor` e `X-BRM-Trace-Id` | A Central de Ações grava o campo faltante no Agenda |
| G13 | MFA (TOTP) ativado para `ADMIN` e `MASTER`, com `aal2` no JWT | Senha e TOTP do gestor sem senha própria no framework |
| G14 | Unidades de quem não é profissional (secretárias), por exemplo `profile_locations` | Regras por unidade; hoje só o profissional tem unidade (`staff_locations`) |
| G15 | Leitura de "primeira sessão concluída" por paciente (ou o histórico de status por paciente) | Gatilho do contrato |

CPF e endereço são dado pessoal; o pedido deve vir com a política de acesso (quem lê o CPF na tela do
Agenda) e a máscara na API de leitura.

## Red Team / Blue Team

| Risco | Controle |
|---|---|
| Link reenviado ou guardado e usado depois | Uso único, validade curta, executa com a identidade de quem abriu |
| Parâmetros alterados no link | Não há parâmetros na URL; hash do pedido no servidor |
| Sessão da colega aberta no PC da recepção | PIN por ação com efeito, "Você está como", bloqueio por inatividade |
| Modelo devolve link de domínio falso | Domínio fixo do cliente; treinamento; passkey opcional para o gestor |
| Contrato com dado errado | Prévia com dados ao vivo antes de confirmar; emissão nova aponta para a anterior |
| Modelo de contrato alterado sem aprovação | Modelo versionado no vault; só vale depois da aprovação do gestor; o PDF guarda a versão usada |
| Framework vira ERP paralelo | Fronteira de dados acima; teste que falha se uma tabela do framework guardar CPF ou telefone fora de documento emitido |
| Framework fora do ar | Modo degradado declarado: chat e Central param, telas do Agenda seguem |

## Plano de entrega

| Fase | Entrega |
|---|---|
| 1 | `IdentityProvider` do Agenda, validação de JWT, mapeamento de papéis; conector único com credencial por conta |
| 2 | Central de Ações: links, página, confirmação por PIN, `status_da_acao`, trilha costurada |
| 3 | Emissor de documentos e contrato de sessão (depende de G10, G11, G12) |
| 4 | Regra do contrato na primeira sessão (gatilho, pendência, bloqueio; depende de G15) |
| 5 | Gerador de modelos agnóstico; anexo da via assinada |

## Perguntas em aberto

1. A "primeira sessão" é a primeira na clínica ou a primeira com cada profissional? (Paciente que muda
   de psicóloga assina outro contrato?)
2. O bloqueio sem contrato vale para agendar a segunda sessão e vender pacote, ou só gera aviso?
3. O dono pode mandar o modelo atual do contrato do Weis? Ele define os campos obrigatórios e se o
   contrato muda por modalidade (avulsa, pacote, recorrente).
4. O chat pode mostrar dado mínimo de agenda (primeiro nome e horário) para a conta da secretária, ou
   tudo com nome vai por link?
5. Confirmar com o time do Agenda se o Supabase Auth self-hosted está com MFA disponível.

## Como ficou a implementação

**Entregue (backend):** `IdentityProvider` `local` e `agenda` (JWT do Supabase Auth, papéis
mapeados em `client_config`), atributos por pessoa com PIN scrypt, Central de Ações
(`core/acoes.py`, API `/api/central`, tools `criar_acao`, `criar_lote_acoes`, `status_da_acao`,
`tipos_de_acao`), emissor de documentos (`core/documentos.py`: Jinja2 em sandbox, ReportLab,
documento imutável com hash), contrato de sessão no módulo `agenda` (tipo de ação, fatos
`primeira_sessao_concluida` e `contrato_emitido`, regra de exemplo, lista de pendentes) e o gerador
de modelos agnóstico com declaração de campos. Detalhes, API e decisões em
`docs/changelog/035-central-de-acoes-e-emissao-de-documentos.md`.

**Fora desta entrega:** a página `/acao/<id>`, o OAuth e o `/mcp` (033/034), o anexo da via
assinada, e tudo que depende de G10 a G15 do Agenda (testado só com provider falso). Surgiu o
pedido **G16**: o JWT do Agenda precisa trazer o papel (hook de token), pois hoje o papel está só em
`profiles`. A pergunta em aberto 2 foi resolvida provisoriamente com `require_approval` (exceção do
gestor), configurável para `deny`.
