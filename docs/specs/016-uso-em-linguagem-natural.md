# 016 — Uso em linguagem natural (spec)

**Status:** aprovada em 2026-10-07 (uma tool por skill); implementada, ver
`docs/changelog/016-uso-em-linguagem-natural.md`.

**Origem:** requisito do usuário (2026-10-07): ao chamar o MCP pelo Claude ou
qualquer outro cliente, o uso deve ser por linguagem natural, de forma que
a conversa no chat funcione sem o usuário falar em nomes de skill ou em
argumentos. Esta spec registra o que falta no motor para isso e propõe o
desenho. Nada foi implementado ainda.

## Como é hoje

- O modelo enxerga uma única tool genérica, `call_skill(skill_name,
  arguments: dict)` (`mcp_engine/core/mcp_server.py`). Não há tool para
  listar as skills do cliente nem seus parâmetros.
- O docstring de `call_skill` e o de `vault_search` citam só exemplos
  jurídicos (`enviar_email`, `consultar_erp`, "vault jurídico"). Num
  cliente de clínica, o modelo não tem como descobrir `consultar_prontuario`
  ou `emitir_declaracao_comparecimento`.
- A `description` e os `parameters` de `skills.yaml` não chegam ao modelo.
- Alguns fatos usados pelas regras são argumentos informados pelo próprio
  chamador. Na demo (`scripts/demo_clinica.py`) o script os calcula; numa
  conversa real quem os informaria seria o modelo:
  `vinculo_com_paciente`, `matricula`, `frequencia_percentual`,
  `paciente_menor`, `mensalidade_*`. Para esses, o modelo poderia errar ou
  ser induzido a informar um valor favorável, e a regra confiaria nele.

## Desenho proposto

1. **Descoberta de skills.** Nova tool `listar_skills` que devolve, para o
   perfil logado, `name`, `description` e `parameters` (tipo, obrigatório,
   descrição) de cada skill. Alternativa: registrar cada skill como tool MCP
   própria com schema tipado, o que dá ao modelo o esquema direto e é a
   forma mais natural para o cliente de chat. A escolha entre as duas fica
   para a aprovação desta spec.
2. **Docstrings neutras de cliente.** Os docstrings de `call_skill`,
   `vault_search` e afins deixam de citar o domínio jurídico e passam a
   mandar o modelo consultar `listar_skills` e o vault. O vocabulário do
   cliente vem de `client_config.yaml`: nova chave `context` com uma
   descrição curta do negócio.
3. **Fatos derivados no servidor, não informados pelo modelo.** Nova
   seção `derived_facts` por skill em `skills.yaml`, resolvida pelo motor
   antes de `evaluate()`:
   - `vinculo_com_paciente`: compara a identidade logada com
     `psicologo_responsavel` da nota do paciente no vault.
   - `paciente_menor`: lê `faixa_etaria`/`idade` do vault.
   - `matricula`, `frequencia_percentual`, `mensalidade_*`: leem o
     cadastro (vault ou sistema da clínica).
   Argumentos informados pelo modelo que colidirem com um fato derivado
   são descartados e a divergência é registrada na auditoria.
4. **Referência a pessoas por nome ou ID.** O modelo recebe "o paciente do
   Wagner com ansiedade de avaliação" e precisa chegar ao `PAC-xxx`. O motor
   expõe `vault_search`/`vault_get_note` para resolver isso, e a skill
   recebe sempre o `paciente_id` resolvido. Regra de produto: se houver mais
   de um candidato, o modelo pergunta ao usuário antes de agir.
5. **Respostas negadas ou pendentes legíveis.** `reason` e a mensagem da
   regra já são em português claro; acrescentar `proximo_passo` (campo `next_step` na regra) (por
   exemplo, "peça ao psicólogo responsável") para o modelo repassar ao
   usuário sem inventar.
6. **Frases-exemplo por skill.** `examples` opcional em `skills.yaml` com
   2 ou 3 frases de usuário, usadas na descrição da tool e nos testes.

## Fora de escopo

- Reconhecimento de nomes próprios no DLP (NER).
- Controle de acesso por nota do vault (precisa ser feito antes de dado
  real; é pré-requisito do item 3 para vínculo).
- Mudar o modelo de autenticação.

## Testes previstos

- Servidor MCP em memória lista as skills do cliente `instituto-afw`.
- Fato derivado: usuário sem vínculo é negado mesmo quando o argumento diz
  `vinculo_com_paciente: sim`.
- Roteiro em linguagem natural (frases do usuário em
  `docs/guia-teste-claude-desktop.md`) rodado contra o servidor de teste.

## Pergunta em aberto

`listar_skills` único ou uma tool MCP por skill? Recomendação: uma tool por
skill, gerada do `skills.yaml`, porque o cliente de chat já sabe lidar com
schemas tipados e a conversa fica mais natural.
