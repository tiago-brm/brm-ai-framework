# 033 — Acesso da equipe: identidade por pessoa, senha do gestor e distribuição na nuvem (spec)

**Status:** proposta, aguardando aprovação (2026-10-31). A seção "Opções de identidade" foi revista
pela [034](034-trilha-por-pessoa-com-poucas-contas.md): conta do Claude compartilhada viola os termos da
Anthropic, e as secretárias passam a usar o Assistente BRM.

**Origem:** pedido do dono (2026-10-31). A equipe da clínica (secretária, gestor, psicólogos) deve fazer
tudo que o BRM Agenda faz conversando no Claude: marcar, consultar, cancelar, registrar e conferir
pagamento, tirar relatórios. Relatórios administrativos e financeiros exigem **senha do gestor**.
Preocupações: (1) cliente pequeno não compra vários assentos Enterprise; (2) como identificar cada pessoa
para as regras valerem; (3) como distribuir de forma simples. O cliente aceitou hospedar o
brm-ai-framework e o SilverBullet na nuvem. Complementa a [032](032-financeiro-clinica-sobre-brm-agenda.md).

## Premissa: o assento do Claude não é a identidade

A regra por pessoa vale por causa do **login no conector do BRM**, não por causa do plano do Claude.
Não é preciso Enterprise. O que o plano decide é quantas contas Claude o cliente paga e se a conta é
compartilhada. Isso muda a opção de arquitetura (ver "Opções").

## Estado atual da autenticação (verificado no código, 2026-10-31)

| Ponto | Hoje |
|---|---|
| Credencial | Token estático por usuário, guardado como SHA-256 em `users.db` (`user_store.py`). Sem senha, sem login, sem MFA |
| HTTP | `Authorization: Bearer <token>` lido a cada request; sem credencial vira 401 (`http_app.py`) |
| stdio | `MCP_USER_EMAIL` ou `MCP_API_TOKEN` no ambiente do processo; sem credencial vira `anonymous` (viewer) |
| Transporte remoto | SSE (`/sse` e `/messages/`). Não há `/mcp` (Streamable HTTP) nem OAuth |
| Papéis | Três, fixos em enum: `admin`, `viewer`, `super_admin` (`auth.py`). Não existe recepção, gestor nem profissional |
| Confirmação | `confirmar_operacao` é uma tool que o modelo chama; o seed de permissões dá ao `viewer` acesso a ela |
| Aprovação | Por outro perfil, em `aprovar_operacao` ou na página Aprovações do painel (spec 022). Sem senha |
| SilverBullet | Uma credencial básica, fora do RBAC do MCP (spec 025, `silverbullet-seguranca.md`) |
| Nuvem | `docker-compose` com `engine`, `dashboard`, `silverbullet` (perfil `editor`), `backup` e `proxy` Caddy; um cliente por instância; runbook de migração |

Resumo: a identidade por pessoa existe, mas só como token colado em configuração local. Serve para
demo em um Mac. Não serve para uma secretária entrar sozinha pelo Claude.

## O que o Claude permite hoje (pesquisado em 2026-10-31)

- **Conectores personalizados (MCP remoto)** existem em todos os planos: Free (limite de um), Pro, Max,
  Team e Enterprise. Em Pro e Max a própria pessoa adiciona; em Team e Enterprise o dono da organização
  adiciona e os membros se conectam.
- Os conectores remotos são acessados **a partir da nuvem da Anthropic**, não da máquina da pessoa. O
  servidor precisa de URL pública com HTTPS. Isso encaixa com a decisão de hospedar na nuvem e elimina
  instalação por computador.
- O Claude Desktop **não** lê servidor remoto pelo arquivo `claude_desktop_config.json`; usa a tela de
  Conectores. Servidores locais (stdio) continuam pelo arquivo.
- Há suporte a OAuth, com callback `https://claude.ai/api/mcp/auth_callback`.
- Team passou a aceitar **2 assentos** no mínimo (fontes divergem e podem estar desatualizadas;
  confirmar na página de preços antes de cotar). Valores de referência encontrados: padrão em torno de
  US$ 20 a 25 por assento por mês.

Fontes: [Suporte Claude, conectores remotos](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp),
[construção de conectores](https://support.claude.com/en/articles/11503834-building-custom-connectors-via-remote-mcp-servers),
[Team a partir de 2 assentos](https://pasqualepillitteri.it/en/news/8545/claude-team-2-seats-small-teams),
[preços do Team](https://krater.ai/blog/claude-team-plan-pricing).

## Opções de identidade

| Opção | Como identifica | Custo para o cliente | Instalação | Veredito |
|---|---|---|---|---|
| **A. Conector remoto com login próprio do BRM, uma conta Claude por pessoa** (Pro ou Team) | Login por pessoa no BRM (OAuth), depois token por pessoa | Um assento Claude por pessoa | Nenhuma: o dono adiciona a URL, cada um clica em Conectar | **Recomendada agora** |
| B. Uma conta Claude compartilhada, token por computador | Identifica o **computador**, não a pessoa | Um assento | Instalar e configurar em cada PC | Só demo. Não atende "quem fez", e a conta compartilhada partilha o mesmo token do conector |
| **C. Assistente dentro do BRM Agenda**, usando a API do Claude | Login do Agenda (Supabase), que já tem papel e vínculo com profissional | Nenhum assento; tokens de API na mensalidade | Nenhuma | **Recomendada para escalar a clientes pequenos** (nova entrega) |

Detalhes que pesam:

- Na opção B, quem usa a conta compartilhada no mesmo conector herda a mesma sessão. A auditoria por
  pessoa, que é o ponto do produto, deixa de existir.
- Na opção A, o conteúdo da conversa fica na conta Claude de quem conversou. Contas pessoais Pro
  deixam dado de paciente fora do controle da clínica. Preferir Team, com administração central.
  Retenção e uso para treinamento diferem por plano: **confirmar nos termos vigentes** antes de
  prometer ao cliente.
- A opção C muda o modelo comercial (a BRM paga tokens e repassa) e exige construir a interface de
  chat. A tabela `web_chat` e o SAF do Agenda mostram que a base existe, mas **não verifiquei** o
  quanto serve para uso interno da equipe.

**Recomendação:** A na Fase 1, com Team no cliente, e C como produto a avaliar quando houver o segundo
ou terceiro cliente pequeno. A proposta do Weis já diz que o acesso ao Claude é contratado pelo
Instituto; a opção A é coerente com isso.

## Autenticação alvo (opção A)

1. **Transporte:** adicionar Streamable HTTP em `/mcp`. Manter SSE enquanto houver cliente que precise.
   Conferir qual transporte o conector do Claude exige hoje antes de escolher o padrão.
2. **OAuth 2.1 no próprio engine** como servidor de autorização, com PKCE (S256), metadados de
   descoberta (recurso protegido e servidor de autorização), callback do Claude na lista permitida,
   token de acesso curto, refresh com rotação e revogação. O SDK `mcp` instalado já traz a interface de
   provider de servidor de autorização (`mcp.server.auth`); a spec 010 só a descartou porque exigia um
   servidor externo. Validar na implementação.
3. **Página de login do BRM** (do próprio engine, não do chat): e-mail e senha com hash forte
   (argon2id), limite de tentativas, bloqueio temporário. **MFA TOTP obrigatório** para gestor e
   super_admin. A spec 020 deixou MFA fora; passa a estar dentro.
4. **Provedor de identidade plugável:** interface `IdentityProvider` com `local` (`users.db`) e
   `agenda` (valida a senha no Supabase Auth do Agenda e traz o papel e o `staff_id`). Com `agenda`, a
   equipe usa o mesmo login do sistema. Decisão aberta (pergunta 2).
5. **Papéis configuráveis por cliente** em vez do enum fixo: `recepcao`, `gestor`, `profissional`,
   `super_admin`. `viewer` e `admin` legados são mapeados para não quebrar demos. O `profissional` fica
   ligado ao `staff_id` do Agenda (a migration 107 do Agenda já liga profissional ao perfil), para
   "ver só a própria agenda" ser um fato derivado da regra.
6. **Desligamento imediato:** desativar a pessoa no BRM revoga todos os tokens dela na hora, mesmo que
   o Claude dela continue logado.
7. **Compatibilidade:** o token estático continua valendo para painel, scripts, testes e o modo stdio.

## Senha do gestor: autorização fora do chat

**Princípio:** a senha do gestor **nunca é digitada no chat**. Tudo que é digitado vai para o contexto
do modelo, para o histórico da conversa no Claude e, em tese, pode ser induzido a vazar por prompt
injection. A senha só existe em uma página do BRM.

**Fluxo do relatório sensível:**

1. A secretária pede, por exemplo, "relatório financeiro de outubro".
2. A regra marca a skill como `require_step_up` (senha do gestor). O motor não executa e devolve um
   resumo gerado por ele (o que será liberado, para quem, qual período) e um link de uso único.
3. O Claude mostra o link. A secretária o envia ao gestor ou o gestor está ao lado.
4. O gestor abre o link no navegador, entra com senha **e** TOTP, vê o resumo do motor (não do modelo)
   e clica em Liberar.
5. O motor grava uma autorização amarrada a: skill, hash dos argumentos, quem pediu e quem liberou.
   Uso único, validade de 5 minutos.
6. A secretária diz "liberado". O Claude refaz a chamada e o motor executa.
7. Auditoria registra pedido, liberação (com a identidade do gestor) e execução, com o mesmo `trace_id`.

**Níveis de sensibilidade** (declarados por skill, validados no `config_loader`):

| Nível | Exemplos | Controle |
|---|---|---|
| N0 | horários livres, agenda do dia, saldo de um paciente | Executa direto |
| N1 | agendar, cancelar, remarcar, registrar pagamento | Resumo e confirmação da pessoa (spec 022) |
| N2 | pagamento fora do padrão, estorno, conferir `PENDING_REVIEW` | Aprovação de outro perfil logado (gestor) |
| N3 | relatórios administrativos e financeiros, repasse, parâmetros, exportação | **Senha e TOTP do gestor** pelo fluxo acima |

**Entrega do relatório:** tabela no chat mais arquivo `.xlsx` por **link assinado de uso único**, com
validade curta. Os números vêm do motor por template determinístico; o modelo não redige valores.

**Limite honesto:** depois da liberação, o que a secretária vê entra na conversa dela no Claude, fora do
controle do BRM. A senha protege **quem consegue pedir e receber**, não o que a pessoa faz com o
resultado. Quem não deve ver comissão não deve receber a liberação.

**Defesas do fluxo:** página de liberação mostra nome de quem pediu e o resumo do motor (anti-phishing);
a secretária não consegue abrir o link como gestor; limite de tentativas e bloqueio; link não aceita
argumentos diferentes dos da proposta (hash).

**Confirmação N1 e o modelo:** hoje o modelo pode chamar `confirmar_operacao` sozinho. Mitigações
baratas: anotar as tools de escrita com `destructiveHint` e `readOnlyHint` para o Claude pedir
permissão ao usuário, e a regra do N2/N3 já exige um humano em outra superfície. O risco residual do N1
(o usuário liberou "sempre permitir" no Claude) é aceito e documentado.

## Distribuição na nuvem

**Modelo:** uma instância por cliente (SQLite por cliente, como a premissa do runbook). O plano de
controle multi-tenant e Postgres continuam na Fase 2 da spec 019.

| Peça | Decisão |
|---|---|
| Hospedagem | Easypanel, que a BRM já usa para o Agenda (`DEPLOY_EASYPANEL.md`), com um serviço por cliente a partir da imagem versionada |
| Endereço | `https://<cliente>.mcp.brmsolutions.com.br`, TLS automático, `/health` monitorado |
| Provisionamento | `scripts/provisionar_cliente.py` (estende `novo_cliente.py`): cria pastas e segredos, cria o gestor com **convite** (link para definir senha e TOTP), imprime a URL do conector e o checklist |
| Entrada do cliente | 4 passos: dono adiciona a URL em Conectores; cada pessoa clica em Conectar e faz o login BRM; gestor define senha e TOTP; teste guiado de 5 perguntas |
| Atualização | Imagem com tag `BRM_VERSION`, migrações automáticas, backup antes de subir |
| Segredos | Variáveis do Easypanel; chave do Agenda por cliente (ver 032); rotação pelo runbook |
| Backup | `age` para armazenamento de objetos, restauração testada (já na 028) |
| Observabilidade | `/health`, alerta de indisponibilidade, painel de auditoria |

**Custo:** a análise interna já mostra a hospedagem de R$ 50/mês abaixo do custo (R$ 145). Nuvem
pública, backup em objeto e monitoramento sobem esse custo. Revisar a mensalidade antes de replicar.

## SilverBullet na nuvem

- **Não expor o SilverBullet na internet com credencial única.** O risco já documentado (editor fora do
  RBAC, escrita pode executar Space Lua no navegador de quem abrir) piora quando está público.
- Colocar o caderno atrás do **mesmo login BRM** (autenticação delegada pelo proxy ao engine, rota
  `/caderno/*`), liberado só para `gestor` e `super_admin`, com TOTP. Alternativa mais simples: acesso
  só por VPN ou lista de IPs.
- `sistema/` e `relatorios/` continuam somente leitura por montagem; nada que o caderno escreva vale
  sem validação e aprovação no motor (spec 030).
- Para o Weis, o caderno guarda regras, procedimentos e glossário. **Não** guardar dado identificável de
  paciente no vault (o escopo da proposta exclui prontuário).
- Backup do vault junto com os bancos.

## Dados pessoais e LGPD

- A clínica é controladora e a BRM, operadora. Entram como suboperadores o provedor de nuvem e, pela
  conta do próprio cliente, a Anthropic. Revisar o contrato de tratamento de dados e a lista de
  suboperadores antes de assinar.
- Nome de paciente e valor pago de sessão podem ser dado pessoal sensível por inferência. O DLP não
  reconhece nomes (NER está na Fase 2 da 019). A secretária **vai** digitar nomes. Decisão do cliente e
  da BRM: aceitar nomes no chat com plano Team e contrato adequado, ou operar por identificador.
- Telefone e CPF seguem bloqueados na entrada. Isso conflita com cadastrar paciente pelo chat (ver
  pergunta 5).

## Red Team / Blue Team

| Risco | Controle | Teste |
|---|---|---|
| Duas pessoas usando a mesma conta Claude | Identidade vem do login BRM por pessoa; documentar que conta Claude compartilhada não é suportada | Mesmo token em dois papéis é rejeitado |
| Senha do gestor digitada no chat | Fluxo fora do chat; DLP de entrada ganha padrão de "senha" para negar e alertar | Frase "minha senha é..." é bloqueada |
| Secretária libera o próprio relatório | Segregação: quem libera é diferente de quem pede; liberação exige senha e TOTP do gestor | Pedido e liberação pelo mesmo usuário falham |
| Reuso do link de liberação | Uso único, 5 minutos, amarrado ao hash dos argumentos | Segundo uso e argumento alterado falham |
| Link de liberação em site falso | Domínio fixo do cliente, resumo do motor na página, aviso no texto do chat | Revisão manual do texto |
| Token OAuth roubado | Access token curto, refresh com rotação, revogação por usuário | Token revogado deixa de funcionar |
| Funcionária desligada continua logada no Claude | Desativação revoga os tokens do BRM na hora | Teste de revogação |
| Força bruta no login | Limite por conta e por IP, atraso progressivo, alerta | Teste de bloqueio |
| SilverBullet público | Atrás do login BRM ou VPN; sem credencial única na internet | Varredura externa |
| Prompt injection em nome de paciente ou observação | Regras fora do modelo; argumentos tipados; N3 exige humano em outra superfície | Corpus adversarial da 029 |
| Provedor de identidade `agenda` indisponível | Falha fechada; gestor mantém acesso de emergência local (break-glass auditado) | Teste com Agenda fora do ar |

## Postmortem antecipado

1. **Conta Claude compartilhada "para economizar".** Perde a auditoria por pessoa. Tratado por
   documentar, por treinar e por mostrar no painel quem fez cada ação.
2. **Fadiga do link de liberação.** Se todo relatório pede o gestor, ele deixa de olhar. Tratado por
   manter N3 só para o que é de fato sensível e pela pergunta 3 (janela de liberação).
3. **Cliente perde o celular com TOTP.** Fluxo de recuperação pelo super_admin, auditado.
4. **Anthropic muda regras de conectores ou planos.** A opção C é a saída que não depende do plano.
5. **Custo de nuvem acima da mensalidade.** Revisar preço (acima).

## Os cinco pilares

| Pilar | Evidência |
|---|---|
| Red Team | Tabela acima |
| Blue Team | OAuth, MFA, step-up e revogação implementados e testados |
| Postmortem | Seção acima |
| God Mode | Painel mostra pedidos, liberações e quem liberou, por `trace_id`; nível de sensibilidade declarado por skill |
| L-99 | Invariantes: "senha nunca passa pelo modelo", "liberação é de uso único e amarrada ao hash", "desativar revoga na hora"; testes de propriedade da máquina de estados da autorização; runbook de perda de TOTP e de rotação |

## Plano de entrega

| Fase | Entrega | Critério de pronto |
|---|---|---|
| 1 | Papéis configuráveis, `IdentityProvider`, login com senha e TOTP, OAuth e `/mcp`, conector funcionando no Claude Desktop e no claude.ai com dois usuários distintos | Ações dos dois aparecem com identidades diferentes na auditoria |
| 2 | Step-up N3 com link, arquivo assinado, níveis de sensibilidade no `config_loader` | Relatório financeiro só sai com liberação do gestor |
| 3 | `provisionar_cliente.py`, imagem versionada, Easypanel, caderno atrás do login, backup na nuvem | Cliente novo entra no ar em menos de uma hora, sem instalar nada nos PCs |
| 4 | Avaliar a opção C (assistente no Agenda) | Decisão do dono |

## Perguntas em aberto

1. Opção A (um Claude por pessoa, com Team) ou C (assistente dentro do Agenda)? Recomendo A agora.
2. Identidade `local` (usuários do BRM) ou `agenda` (mesmo login do Agenda)? Recomendo `agenda` se o
   Agenda estiver sempre no ar para a clínica, com `local` só para o gestor de emergência.
3. A liberação do gestor vale **por relatório** ou abre uma **janela de 15 minutos** para a secretária?
   Recomendo por relatório no início.
4. Quais relatórios entram no N3? Proposta: tudo que mostre repasse, comissão, faturamento total, ou
   dado de mais de um profissional.
5. Cadastro de paciente pelo chat exige telefone, que o DLP bloqueia. Manter o cadastro na tela do
   Agenda, ou abrir uma exceção explícita e auditada só nesse campo?
6. Quantas pessoas usarão o Claude no Weis? Isso define o custo de assentos que o cliente assume.
7. Domínio: `*.mcp.brmsolutions.com.br` serve?

## Fora de escopo

SSO corporativo (Google, Microsoft), biometria, aprovação em múltiplos níveis, aplicativo móvel
próprio.
