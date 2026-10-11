# UX Flows

> Jornadas reais do produto, com sucesso, falha e estados. Usadas pelo agente para implementar e verificar telas/tools e por [TESTING.md](TESTING.md) como roteiro de verificação.
> Duas superfícies: **conversa** (Claude + MCP — onde o usuário do cliente vive) e **painel** (`super_admin` — operação BRM).

**Estados do motor (vocabulário comum):** `allowed` · `denied` (com `reason`, `rule_id`, `proximo_passo`) · `pending_approval` ("human approval required"). Toda negativa e pendência chega ao usuário com motivo e próximo passo, e é auditada.

---

## Parte A — Conversa (Claude + MCP)

### Flow A1 — Pedido em linguagem natural (caminho principal)
1. Usuário escreve: "Resuma as últimas sessões do paciente com ansiedade de avaliação".
2. O Claude lê as instruções do servidor (`Cliente: …` + `context` do cliente) e a lista de tools **do seu perfil** (`tools/list` filtrado).
3. Não há ID: o Claude chama `vault_search` e identifica o paciente. Se houver **mais de um candidato**, ele pergunta ao usuário antes de agir.
4. Chama a tool da skill (ex.: `consultar_prontuario`) com o ID resolvido; os fatos de segurança (`vinculo_com_paciente`) **não** são parâmetros — o motor os calcula.
5. Motor: RBAC → fatos derivados → regras → interceptor → auditoria.
6. O Claude responde ao usuário com o resultado.

- **Success:** `allowed: true` + `result` (e `vault` quando a skill declara `vault_read`).
- **Failure — negada:** `allowed:false` + `reason` + `proximo_passo`. O Claude explica e **não tenta contornar** (instrução do servidor). Ex.: "Sem vínculo com este paciente … Peça ao psicólogo responsável."
- **Failure — pendente:** `"human approval required"`: o Claude diz o que falta (ex.: revisar e fazer a devolutiva do relatório) e o próximo passo.
- **Failure — DLP/allowlist:** `reason` do interceptor (ex.: "DLP: found CPF"); nada é devolvido.
- **Loading:** a primeira busca após subir o servidor é lenta (carrega o modelo de embeddings); o painel aquece o índice no *startup*, o stdio não.
- **Empty:** busca sem notas visíveis → o Claude informa que não achou (notas negadas por política **não** são sinalizadas como existentes).

### Flow A2 — Conectar um cliente ao Claude
1. Operador cria/valida `clients/<cliente>/config/` (ver Flow A4) e os usuários do cliente.
2. **Claude Code:** `claude mcp add brm-engine -e CLIENT_ID=… -e CONFIG_ROOT=… -e DATA_ROOT=… -e MCP_USER_EMAIL=… -- uv run python -m mcp_engine.core.mcp_server`.
   **Claude Desktop:** entrada `mcpServers` no `claude_desktop_config.json` com `command: uv`, `args: [run, --cwd, <repo>, python, -m, mcp_engine.core.mcp_server]` e as mesmas variáveis em `env`.
3. Reiniciar o Claude; as tools do perfil aparecem.
4. `scripts/preparar_demo_chat.py <perfil>` cria os perfis do `instituto-afw` e imprime o trecho de configuração.

- **Atenção:** a identidade é fixada **na subida** do processo; trocar de perfil = reconfigurar/reiniciar (por isso existe o simulador, Flow B6). Sem `MCP_USER_EMAIL`/`MCP_API_TOKEN` o usuário é `anonymous` (`viewer`).
- **Failure:** configuração inválida → falha na subida (`ConfigLoadError`); servidor não aparece → conferir caminhos/JSON/reinício.
- Mudou `rules.yaml`/`skills.yaml`? **Reinicie o servidor** (a config é lida na construção do engine).

### Flow A3 — Consultar a auditoria
1. Perfil com acesso pede "o que foi negado ontem?".
2. O Claude chama `query_audit_log` (filtros por data, ação, decisão).
3. Resultado: `{total, events[{occurred_at, actor, action, decision, rule_id}]}`.

- **Failure:** `AuditChainError` se o arquivo foi adulterado/truncado → preservar o arquivo e investigar ([SECURITY.md](SECURITY.md) §12).

### Flow A4 — Onboarding de cliente novo
Skill do projeto: `.claude/skills/brm-onboarding`.
1. `clients/<cliente>/config/client_config.yaml` (mínimo: `client_id`, `domain_allowlist`, `block_private_ip`, `dlp_patterns`, `context`).
2. **Guardrails primeiro** (não negociável): allowlist de todos os domínios que skills vão chamar; DLP do cliente (CPF, CNPJ, CID, telefone…).
3. Vault (Obsidian por convenção `clients/<cliente>/vault/` ou `vault_root` explícito; ou Notion) com `tipo` e campos de frontmatter usados pelas políticas.
4. `rules.yaml`/`skills.yaml` iniciais (podem começar vazios) e `vault_access.yaml` com `default: deny`.
5. Regras/skills adicionais **por rascunho** (Flow A5). Documentos padrão por Flow A6.
6. Criar usuários (`criar_usuario` / painel) e validar com o simulador e a matriz (Flow B6/B7).
7. `uv run pytest -q` e demos/pré-voo antes de entregar.

- **Failure:** `vault_root` explícito inexistente → `VaultRootNotFoundError`; Notion sem token/raiz → `NotionConfigError`.

### Flow A5 — Rascunho de regra/skill a partir de nota → promoção
1. Operador escreve a intenção em português numa nota (`config_drafts/…`).
2. `vault_interpret_and_propose(note_id)`: DLP de saída estrito → agente → validação → rascunho `pending`.
3. `vault_list_drafts` para revisar (`kind`, `status`).
4. Revisão humana do conteúdo (JSONLogic/skill) **e** do efeito prático (ex.: "nega tudo?").
5. `vault_promote_draft(draft_id, kind)` → entra no fim de `rules.yaml`/`skills.yaml`; `approved`; auditado.
6. Reiniciar o servidor para a regra valer.

- **Failure:** nota com padrão DLP → bloqueada antes do LLM; JSONLogic/Pydantic inválido → rascunho rejeitado ou promoção com erro auditado; sem chave de LLM → erro claro ao usar o agente.
- **Quem pode:** `admin`/`super_admin` (`tool_permissions`); a identidade de quem promoveu fica em `reviewed_by` e na auditoria.

### Flow A6 — Documento padronizado
`vault_generate_document(instrucoes)` → rascunho `document` → revisão humana → `vault_promote_document(draft_id)` grava a nota no vault. Mesmas falhas e permissões do A5.

---

## Parte B — Painel `super_admin`

Entrada comum: URL do engine (padrão `http://localhost:8000`) + token Bearer. Subir: `CLIENT_ID=… uv run uvicorn mcp_engine.core.http_app:app --port 8000` e `cd dashboard && npm run dev` (porta 5173) ou `build` + `preview` (4173).

### Flow B1 — Login
1. Usuário abre o painel → "Carregando…" enquanto valida a sessão salva.
2. Sem sessão: formulário com URL e token → `GET /api/me`.
3. Sucesso → Visão geral.
- **Failure:** `403` → "Este token não tem perfil super_admin — o painel é restrito a ele."; demais → "Não foi possível conectar ao BRM Engine. Confira a URL e o token."
- Sair: clique no avatar de iniciais (limpa o token).

### Flow B2 — Visão geral
Resume eventos recentes, usuários, matriz de permissões (viewer × admin), regras (JSONLogic), vault e catálogo de tools, com atalhos para cada seção.
- **Empty:** "Nenhum evento registrado." / "Nenhum usuário cadastrado." / "Nenhuma regra configurada." / "Vault vazio." / "Sem tools no catálogo."

### Flow B3 — Usuários
1. Lista "Perfis cadastrados" (identidade + perfil; token nunca aparece).
2. "Novo usuário": e-mail e perfil (`viewer`/`admin`/`super_admin`); o token é **sempre gerado** pelo motor (a API aceita token próprio, a tela não oferece).
3. O token é **exibido uma única vez** na criação ("Token gerado (só aparece agora)"); copie na hora.
4. "Trocar perfil": seletor na lista, para usuário já existente.
- **Failure:** `400` com o motivo ("usuário já existe", "perfil inválido", "só um super_admin pode atribuir super_admin").

### Flow B4 — Matriz de permissões (por tool)
1. Grade "Perfil × tool" com colunas `viewer` e `admin` (`super_admin` não aparece: tem acesso total por construção).
2. Alternar uma célula (`permitido`/`negado`) → `PUT /api/permissions`; vale **sem reiniciar**.
3. Lembrete: tool **sem nenhuma linha** é aberta a todos. **Armadilha de UI:** a grade mostra `negado` para célula sem linha, mesmo a tool estando aberta; marcar `permitido` em um perfil cria a primeira linha e **passa a restringir** a tool só aos perfis marcados (ver [SECURITY.md](SECURITY.md) L-14). Para restringir uma tool a `admin`, marque `permitido` na coluna `admin`.
- **Failure/Empty:** erro do catálogo ou "Catálogo de tools vazio."

### Flow B5 — Tools, Regras e Vault (somente leitura)
- **Tools:** catálogo completo (inclui as geradas por skill). **Regras:** id, descrição, condição, efeito, mensagem. **Vault:** notas e conteúdo da selecionada.
- Editar regra/vault **não** é possível aqui: use os fluxos A5/A6 e edição direta do vault.
- *Copy a corrigir:* o título "Vault jurídico" e docstrings de `vault_add_note`/`vault_list_notes` ainda citam o domínio jurídico, contra o princípio agnóstico de segmento (D-016).

### Flow B6 — Simulador ("ver como outro perfil")
1. Escolher o perfil num cartão → faixa fixa "Simulando como *Nome · papel*".
2. Executar um **cenário** de `demo_scenarios.yaml` ou o **formulário livre** (skill + parâmetros gerados do `skills.yaml`).
3. Ler o resultado: decisão, regra, mensagem, `proximo_passo`, **fatos derivados** (ex.: `vinculo = nao`), DLP aplicado.
4. "O que este perfil vê no vault": busca com a mesma política do perfil (nota completa, só ficha ou nada; contagem de ocultas e políticas).
5. A auditoria ao lado atualiza a cada execução.
- **Garantias:** nenhum efeito externo (e-mail não sai); a sessão real do `super_admin` não muda; cada execução gera `simulacao:*` com `executado_por` e `como`.
- **Failure:** `404` se o simulador estiver desligado no cliente (`simulador: false`) ou o perfil não existir.
- **Empty:** sem `demo_scenarios.yaml` a lista de cenários fica vazia e o formulário livre continua funcionando.

### Flow B7 — Governança ("Quem pode o quê")
Tabela cenários × perfis com a decisão de cada célula, calculada na hora a partir de regras e fatos derivados (sem DLP, sem escrever na auditoria). É a imagem principal para demonstração.
- **Loading:** "Calculando…". **Empty:** "Nenhum cenário cadastrado em demo_scenarios.yaml."

### Flow B8 — Auditoria
Filtros (data, ação, decisão) → lista de eventos com selo de decisão, ator, ação e regra. **Empty:** "Nenhum evento para esses filtros." Erro de cadeia aparece como mensagem de erro.

---

## Estados padrão de qualquer tela nova

| Estado | Comportamento |
|---|---|
| Loading | "Carregando…" local ao cartão, sem travar a página |
| Empty | `Empty` com frase curta dizendo o que falta e como preencher |
| Error | mensagem vermelha em português com a ação corretiva; nunca stack trace |
| Success | confirmação discreta + lista atualizada |
| Negado | selo + motivo + próximo passo |

Detalhe visual em [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) §12; contratos em [API.md](API.md).
