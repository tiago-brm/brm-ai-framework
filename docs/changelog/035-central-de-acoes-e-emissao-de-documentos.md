# 035 — Central de Ações, identidade com o Agenda e emissor de documentos

**O quê:** implementa o **backend** de `docs/specs/035-central-de-acoes-e-emissao-de-documentos.md`.
O front da Central de Ações (`/acao/<id>`) não faz parte desta entrega: será feito sobre a API
abaixo. O framework só faz governança; nada de cadastro de conectores de terceiros.

## Entregas

### Fase 1: identidade
- `core/identidade.py`: `IdentityProvider` com `local` (token do `users.db`, sem mudança) e
  `agenda` (JWT do Supabase Auth). Valida assinatura (HS256 com segredo `agenda_jwt_secret` do
  `SecretStore`, ou RS256/ES256 por JWKS https dentro da `domain_allowlist`), `exp`, `sub`,
  `iss` e `aud` configuráveis, tenant opcional. Rejeita `alg: none` e algoritmo fora da lista.
  Extrai e-mail, papel, `aal`, `amr` e `auth_time` (ou o `timestamp` do `amr`, ou `iat`).
  Credencial recusada é auditada (`identidade:agenda:recusada`, sem o token) e vira 401.
- Papéis do Agenda mapeados em `client_config.yaml` > `identidade.agenda.papeis`
  (padrão: `SUPER_ADMIN`→super_admin, `MASTER`/`ADMIN`→admin, `USER`→viewer, `READONLY`→sem acesso).
  O framework continua com `Role` de três valores; "gestor" = `admin`, "recepção" = `viewer`.
- `http_app.py`: o middleware usa o resolvedor (tenta `local` e `agenda` na ordem do cliente) e
  publica a `Identidade` completa em `identidade_var` (`get_identidade()`).
- `core/atributos_usuario.py` (+ tabela `atributos_usuario` no `state.db`): PIN com
  `hashlib.scrypt` e sal, 4 a 8 dígitos (recusa repetido e sequência), contagem atômica de
  falhas, bloqueio temporário; unidades por pessoa. Chave: (provedor, id do usuário no provedor).
  Sem senha, sem cadastro, sem sincronização de usuários.

### Fase 2: Central de Ações
- `core/acoes.py` (+ tabela `acao`): ação com id de 128 bits (`secrets.token_hex(16)`), itens
  guardados no servidor com SHA-256 (`digest`), validade padrão 30 min (por cliente e por
  tipo), uso único (transição atômica `pendente`→`em_execucao`), estados `pendente`,
  `em_execucao`, `confirmada`, `falhou`, `expirada`, `cancelada`. Lote: uma ação, vários itens;
  na falha de um item o restante vira "não executado".
- Tipos de ação por módulo (`RegistroAcoes`, `ModuleHost.registrar_tipo_acao`, nome
  `<modulo>.<nome>`, esquema Pydantic estrito). Tipo nativo `nucleo.executar_skill`: qualquer
  skill do cliente; na criação as regras são avaliadas com quem pediu (deny não gera link;
  `require_approval`, A3 ou irreversível tornam a ação sensível); na execução roda o pipeline
  normal (`_call_skill`) como **quem confirmou**, com chave de idempotência `acao:<id>:<item>`.
- Execução sempre com a identidade de quem confirmou ou liberou, nunca de quem criou o link.
  Ação sensível: só `liberar` (papel mínimo do tipo/regra, nunca o mesmo que pediu, `aal2`
  recente quando `central_acoes.exigir_aal2_liberacao: true`) e quem não é gestor não vê os dados.
- DLP de entrada nos argumentos (a ação pede ids, não dado pessoal). Limite de links por hora e
  por conta. Reconferência do digest antes de executar (pedido adulterado vira `falhou`).
- Auditoria costurada por `acao_id` + `trace_id`: `acao:criada`, `acao:aberta`,
  `acao:pin_incorreto|pin_bloqueado`, `acao:campos_gravando|campos_gravados`,
  `acao:execucao_iniciada`, `acao:confirmada|falhou|expirada|cancelada`, `acao:liberacao_negada`.
  O evento de intenção é gravado antes do efeito; se a auditoria falhar, nada executa.
- Tools MCP: `criar_acao`, `criar_lote_acoes`, `status_da_acao`, `tipos_de_acao` (devolvem só
  link, resumo, estado e ids; nunca dado pessoal), `documento_modelos` e
  `documento_aprovar_modelo` (gestor).

### Fase 3: emissor de documentos
- `core/documentos.py` (+ tabelas `documento_emitido`, `modelo_aprovado`): modelos em
  `<vault>/_modelos/*.md` com front matter `documento`, `versao`, `titulo`, `campos` (nome, tipo
  `texto|data|dinheiro|inteiro|cpf|booleano`, origem `grupo.campo`, `obrigatorio` ou
  `obrigatorio_quando`, `rotulo`). Jinja2 em `SandboxedEnvironment` com `StrictUndefined`; o texto
  só pode usar campos declarados; campo obrigatório ausente ou valor inválido (CPF, data,
  dinheiro) é erro, nunca texto inventado. O LLM não participa.
- PDF com **ReportLab** (pure-Python), fontes Vera embutidas, modo invariante. Gravado como
  arquivo somente leitura em `data/<cliente>/documentos/<id>.pdf` + linha imutável (triggers do
  SQLite recusam UPDATE e DELETE) com SHA-256 do PDF, versão e hash do modelo, **dados usados**,
  quem emitiu, ação e `trace_id`. Correção = nova emissão com `substitui` (mesma referência).
- Modelo só emite depois de `documento_aprovar_modelo` (gestor); a aprovação guarda o hash. Mudar
  o arquivo exige nova versão e nova aprovação.
- Download autenticado (`GET /api/central/documentos/{id}/pdf`, confere o hash a cada leitura).
  O PDF nunca volta pela tool do chat.

### Contrato de sessão (módulo `agenda`, não no núcleo)
- `modules/agenda/contrato.py`: tipo `agenda.emitir_contrato_sessao` (`agendamento_id`,
  `modelo`, `substitui`). Lê agendamento e ficha ao vivo do provider, calcula menoridade na data
  da sessão e usa o responsável legal (financeiro primeiro) quando menor. Campo faltante
  editável é validado (CPF, CEP, UF, nascimento, e-mail, telefone) e gravado na Agenda com ator
  e `trace_id`.
- Contrato do provider: `AgendaCadastroProvider` (`obter_agendamento`, `obter_cliente`,
  `historico_cliente`, `atualizar_cliente`); `FakeAgendaProvider` implementa; `HttpAgendaCadastroProvider`
  recusa com `AgendaRecursoIndisponivel` até a Agenda entregar G10 a G15. `HttpAgendaProvider`
  segue sem nenhum método de escrita.
- Fatos derivados `agenda.contrato`: `primeira_sessao_concluida` (só `COMPLETED`; `NO_SHOW` não
  conta; `null` se o histórico ainda não existe na Agenda) e `contrato_emitido` (consulta os
  documentos emitidos). Skills usam `fatos_de_modulo: [agenda.contrato]` (o `usar:` ganhou essa
  sobrescrita aditiva).
- Regra de exemplo `contrato-antes-do-pacote` no `clinica-piloto`: pacote depois da primeira
  sessão sem contrato → `require_approval` (gestor). Trocar para `deny` torna o bloqueio rígido.
- Skill `agenda_contratos_pendentes` (gestor vê nomes, demais só a contagem) e nota de gatilho
  `defaults/gatilho-contratos-pendentes.md` (alerta diário com a contagem), a copiar para o
  caderno e aprovar.
- Modelo de exemplo `clients/clinica-piloto/vault/_modelos/contrato-sessao.md`, marcado
  `exemplo: true` até o dono enviar o contrato real.

### Fase 5: gerador de modelos agnóstico
- `vault/document_generator.py`: prompt sem advocacia; o segmento vem de `client_config.context`
  e de `_modelos/guia.md` (opcional). A saída inclui `modelo_id` e `campos`. Ao promover, modelo
  com campos é escrito em `_modelos/<id>.md` (front matter válido, `versao: 1`, validado pelo
  mesmo parser do emissor) e ainda precisa de `documento_aprovar_modelo`. Sem campos, o
  comportamento anterior (nota no vault) é mantido. Rascunho + aprovação como antes.

## API REST da Central de Ações

Prefixo `/api/central`. Autenticação: `Authorization: Bearer <token local ou JWT do Agenda>`;
sem credencial válida, 401. Erros: `{"detail": {"motivo": "...", ...extras}}`.

| Rota | Corpo | Resposta / erros |
|---|---|---|
| `GET /eu` | | `{identidade, papel, papel_origem, provedor, metodo, aal, pin_definido, unidades}` |
| `PUT /eu/pin` | `{pin_novo, pin_atual?}` (atual obrigatório se já há PIN) | `{pin_definido: true}`; 422 PIN fraco; 401 atual errado; 423 bloqueado |
| `DELETE /usuarios/{provedor}/{usuario_id}/pin` | | gestor redefine PIN de outra pessoa; 403 se for o próprio ou não gestor; 404 |
| `PUT /usuarios/{provedor}/{usuario_id}/unidades` | `{unidades: [str]}` | gestor; `{unidades}` |
| `GET /acoes/{id}` | | Página: `{acao_id, status, resumo_pedido, voce, pedido_por, pedido_em, expira_em, sensivel, requer: "pin"\|"liberacao_gestor", papel_liberacao, pin_definido, itens: [{indice, tipo, titulo, resumo, dados, campos_faltantes: [{nome, rotulo, tipo, editavel}]}]}`. Dados lidos ao vivo do produto. Ação sensível aberta por não gestor: `itens: []` + `aviso`. Já tratada: `resultado`, `motivo`, `confirmado_por`, `liberado_por`. 404 inexistente; 403 papel; 410 expirada; 503 produto indisponível |
| `POST /acoes/{id}/campos` | `{pin, campos: [{item, nome, valor}]}` | grava no produto (ator e trace_id); `{acao_id, gravados}`; 401/423/428 PIN; 422 valor ou campo inválido |
| `POST /acoes/{id}/confirmar` | `{pin, campos?: [...]}` | `{acao_id, status: confirmada\|falhou, motivo, resultado: [{item, ok, resumo, *_id}], detalhes}`; 403 se sensível; 409 já usada; 422 `faltantes`; 401 `tentativas_restantes`; 423 `bloqueado_ate`; 428 PIN não definido |
| `POST /acoes/{id}/liberar` | idem | para ação sensível (gestor, ≠ quem pediu; `aal2` recente se exigido); 403 segregação/papel; 401 reautenticar |
| `POST /acoes/{id}/cancelar` | | quem pediu ou gestor; `{status: cancelada}` |
| `GET /documentos/{id}` | | metadados (sem dados do paciente); só quem emitiu ou gestor (404 caso contrário) |
| `GET /documentos/{id}/pdf` | | `application/pdf`, `Content-Disposition: attachment`, `X-Content-SHA256`, `Cache-Control: no-store`; auditado |

Fluxo da página: `GET /acoes/{id}` → (preencher faltantes) → `POST /confirmar` com os campos e o
PIN (ou `/liberar` se `requer = liberacao_gestor`) → mostrar `resultado` → baixar o PDF por
`documento_id`. Estados que a página deve tratar: 401, 403, 404, 409, 410, 422, 423, 428, 503.

## Configuração (`client_config.yaml`)
`identidade` (provedores, `agenda.{emissor, audiencia, algoritmos, segredo, jwks_url,
claim_papel, claim_tenant, tenant_id, papeis, folga_s}`), `central_acoes` (`url_base` https,
`validade_min`, `max_por_hora`, `max_itens_lote`, `pin_tentativas`, `pin_bloqueio_min`,
`exigir_aal2_liberacao`, `reautenticacao_min`), `dados_do_emissor` (origem `emissor.*`).

## Dependências
`reportlab` (traz `pillow` e `charset-normalizer`), `jinja2` e `pyjwt[crypto]` (já no lock);
`uv sync --locked` ok.

## Decisões e desvios (validar com o dono)
- **Onde está o papel no JWT.** O Agenda guarda o papel na tabela `profiles`; o JWT padrão do
  Supabase não o traz. Assumi `app_metadata.role` (configurável em `claim_papel`), o que exige um
  hook de token no Agenda (pedido **G16**, novo). Enquanto não houver, só o provedor `local` entra.
- Sem MFA hoje no Agenda: `exigir_aal2_liberacao` fica `false` por padrão. A "senha do gestor"
  desta fase é PIN do gestor + segregação; ligar `aal2` quando G13 existir.
- O PIN é por pessoa, no framework (hash scrypt); primeiro PIN definido pela própria pessoa
  logada (`PUT /eu/pin`), e redefinido pelo gestor.
- `READONLY` do Agenda não entra (papel `None` no mapeamento padrão).
- Pergunta em aberto 2 da spec: a regra de exemplo usa `require_approval` (exceção do gestor);
  o efeito é configurável.
- Tools novas em cliente com `rbac_default: deny` e `users.db` antigo ficam negadas até
  `scripts/migrar_permissoes.py` ou concessão no painel.
- O MCP `/mcp` com OAuth e a credencial por conta de chat seguem nas specs 033/034.

## Pendências
- **Agenda (pedidos da spec):** G10 (CPF e endereço), G11 (responsáveis), G12 (rota de
  atualização e leitura de agendamento/ficha por integração com `X-BRM-Actor` e
  `X-BRM-Trace-Id`), G13 (MFA e `aal2`), G14 (unidades de quem não é profissional), G15 (histórico
  de status por paciente) e **G16** (papel no JWT). Sem eles o `HttpAgendaCadastroProvider`
  recusa e `primeira_sessao_concluida` fica `null`; tudo foi testado com o `FakeAgendaProvider`.
- Pendência automática ao concluir a primeira sessão: ficou como **lista diária** (gatilho por
  agenda + `agenda_contratos_pendentes`). O gatilho por evento depende de G15.
- Modelo real do contrato (campos e variações por modalidade): hoje há só o exemplo.
- Anexo da via assinada (fase 5 da spec) e assinatura eletrônica.
- Bloqueio por inatividade, "Trocar de usuário" e passkey: são da página.
- Janela de liberação do gestor (pergunta 3 da 033) não implementada: uma liberação vale por ação.
- Página `/acao/<id>`: o front consome a API acima; o `url_base` do cliente define o link.

## Testes
`tests/test_identidade.py` (JWT válido, mapeamento, expirado, emissor e audiência errados,
assinatura errada, `alg none`, papel ausente, tenant, RS256 por JWKS, HTTP e auditoria),
`tests/test_acoes.py` (link único, 128 bits, expiração, digest adulterado, PIN errado e bloqueio,
hash scrypt, lote, segregação, `aal2`, skills pela central, trilha, falha de auditoria fecha, API
e tools MCP), `tests/test_documentos.py` (modelo, preenchimento estrito, sandbox, PDF, hash,
imutabilidade por trigger, adulteração do arquivo, correção, aprovação), `tests/test_contrato_sessao.py`
(fluxo com Agenda falsa, campo faltante gravado com ator e trace, menor, fatos, `NO_SHOW`, regra,
pendentes, fronteira de dados: nenhuma tabela do framework guarda CPF/telefone/endereço fora do
documento emitido), `tests/test_document_generator.py` (prompt agnóstico e campos). A convenção
da spec 027 (`test_nucleo_livre_de_termos_setoriais`) cobre os novos arquivos de `core/`.

Resultado: `pytest`: 1035 passam e 1 falha pré-existente (abaixo), contra 921+1 antes da entrega; `ruff check .` limpo.
Falha pré-existente (já falhava antes): `tests/test_agenda.py::test_leitura_sessoes_gestor_ve_nomes_e_viewer_so_agregados`
(dependente da data de hoje; independente desta entrega).

## Atualização 2026-11-01: Agenda real e modelo simples

- **Provider HTTP do Agenda** (`HttpAgendaCadastroProvider`): lê `GET /api/v1/customers/:id/ficha`
  (mascarada; completa só com ator, trace e finalidade), grava com `PATCH` e `X-BRM-Actor` /
  `X-BRM-Trace-Id`, e preserva os responsáveis pelo `id` (a Agenda substitui a lista inteira).
  `obter_agendamento` procura nos últimos 120 dias até existir o pedido G17.
- **Identidade:** o papel vem da claim `brm_role` do hook de token do Agenda (`client_config`).
- **Modelo simples:** o modelo no segundo cérebro não precisa mais de `campos:`. Cada
  `{{ fonte.caminho }}` vem da ficha e do agendamento; `{{ nome }}` sem ponto é lacuna livre,
  informada na emissão (`valores` da ação, até 200 caracteres, uma linha). Dentro de
  `{% if paciente.menor_de_idade %}` os campos só são exigidos quando for verdadeiro. O modelo
  declarado continua funcionando. O chat não escreve o texto nem recebe CPF ou endereço.
- O modelo de exemplo `contrato-sessao` foi reescrito no formato simples.
