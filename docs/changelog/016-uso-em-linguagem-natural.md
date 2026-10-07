# 016 — Uso em linguagem natural: tools por skill e fatos derivados

**O quê:** implementa a spec `docs/specs/016-uso-em-linguagem-natural.md`
(aprovada com a opção "uma tool MCP por skill").

- `mcp_engine/core/mcp_server.py`
  - `build_server` registra cada skill do `skills.yaml` como tool MCP
    própria, com parâmetros tipados, descrição, exemplos de pedido e as
    descrições de cada parâmetro. A tool genérica `call_skill` continua.
  - `FastMCP(instructions=...)` passa ao cliente o contexto do negócio
    (`context` do `client_config.yaml`) e as regras de conversa: achar IDs
    com `vault_search`, perguntar quando houver mais de um candidato, não
    informar fatos de segurança, e repassar motivo e `proximo_passo`.
  - Docstrings de `call_skill` e `vault_search` deixam de falar em domínio
    jurídico.
  - `call_skill` resolve `derived_facts` no motor (`_resolve_derived_facts`),
    a partir do frontmatter do vault, antes de avaliar as regras. O valor
    derivado vence o argumento informado; a divergência vira evento
    `<skill>:fato_divergente` na auditoria. Sem nota correspondente vale o
    `if_missing` da configuração (nos clientes de clínica, nega ou trata
    como menor de idade).
  - Respostas de negado ou pendente trazem `proximo_passo` (e `message`
    quando aguardam aprovação).
- `mcp_engine/core/models.py`: `DerivedFact`, `SkillDefinition.derived_facts`
  e `.examples`, `Rule.next_step`, `RuleDecision.next_step`,
  `ClientConfig.context`.
- `mcp_engine/core/rule_evaluator.py`: repassa `next_step`.
- Clientes `instituto-afw` e `clinica-psi`: skills reescritas com
  descrições em português, exemplos e parâmetros descritos; fatos derivados
  para `vinculo_com_paciente`, `paciente_menor`, `matricula` e
  `frequencia_percentual`; `next_step` nas regras; notas de aluno
  (`ALU-017`, `ALU-023`) no vault do Instituto.
- Demos (`scripts/demo_clinica.py`, `scripts/demo_casos_de_uso.py`): deixam
  de calcular vínculo, matrícula e frequência; passam só os argumentos que
  uma pessoa diria em uma conversa.

## O que ainda não existe

- Os sinalizadores que dependem de ler o texto escrito pelo próprio modelo
  (`incluir_sintomas`, `menciona_valor_ou_vaga`, `promete_resultado`)
  continuam informados pelo modelo. Verificá-los no servidor exigiria
  classificar o texto.
- Controle de acesso por nota do vault: `vault_search` ainda devolve
  qualquer nota a quem perguntar. É pré-requisito antes de dado real.
- `vault_get_note` como tool: a busca por `vault_search` resolve o ID, mas
  não há leitura direta de uma nota por ID.
- Reconhecimento de nomes próprios no DLP (NER).

**Validação:** `tests/test_nlp_tools.py` (7 testes); suíte completa e
`ruff check .` rodados após a mudança.
