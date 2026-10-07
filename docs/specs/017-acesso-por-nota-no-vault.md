# 017 — Controle de acesso por nota no vault (spec)

**Status:** aprovada em 2026-10-07; implementada, ver
`docs/changelog/017-acesso-por-nota-no-vault.md`. Diferença em relação ao
exemplo abaixo: aulas e cadastro de alunos ficam só para a equipe (o aluno
lê aulas pela skill `buscar_aulas`), e o registro documental não aparece na
ficha de identificação, só o prontuário.

**Origem:** a spec 016 e o changelog 016 deixaram registrado que
`vault_search` devolve qualquer nota a quem pergunta. Em 2026-10-07, uma
busca feita como `viewer` (recepção) devolveu o registro documental de dois
pacientes, com as hipóteses do psicólogo. As ações (`call_skill` e as tools
por skill) já passam por regras; a leitura do vault não passa.

## Problema

- `vault_search`, `vault_list_notes` e `vault_get_related` não olham quem
  está perguntando (`mcp_engine/core/mcp_server.py`).
- O RBAC por tool (`ROLE_TOOLS`) só decide se a tool inteira está liberada,
  não o que ela devolve.
- Com o Claude no chat, a busca é o primeiro passo de quase todo pedido
  ("a paciente com autocrítica..."), então o vazamento acontece antes de
  qualquer regra de ação.
- Escrever também não tem controle: `vault_add_note` aceita qualquer perfil.

## Desenho proposto

### 1. Políticas de leitura por nota (`vault_access.yaml`)

Novo arquivo por cliente, ao lado de `rules.yaml`, avaliado com o mesmo
JSONLogic sandboxado do `rule_evaluator`. Cada política vê estes fatos:

- `actor` (identidade logada) e `actor_role`;
- `note.<campo>` para cada campo do frontmatter da nota;
- `vinculo`: `true` quando `note.psicologo_responsavel == actor`.

Cada política tem um `effect`:

| effect | O que o chamador recebe |
|---|---|
| `allow` | a nota inteira |
| `metadata_only` | só os campos listados em `expose` (por exemplo `paciente_id`, `unidade`, `psicologo_responsavel`), sem conteúdo |
| `deny` | a nota não aparece, como se não existisse |

A primeira política que casar decide. Se nenhuma casar, vale
`default` (recomendado: `deny`).

Exemplo para o `instituto-afw`:

```yaml
schema_version: 1
client_id: instituto-afw
default: deny
policies:
  - id: registro-documental-so-responsavel
    when: { "and": [ { "==": [{ "var": "note.tipo" }, "registro-documental"] }, { "var": "vinculo" } ] }
    effect: allow
  - id: prontuario-e-resumo-com-vinculo
    when: { "and": [ { "in": [{ "var": "note.tipo" }, ["prontuario", "resumo"]] }, { "var": "vinculo" } ] }
    effect: allow
  - id: responsavel-tecnica-audita
    when: { "==": [{ "var": "actor_role" }, "super_admin"] }
    effect: allow
    audit: acesso_privilegiado
  - id: identificacao-para-recepcao
    when: { "in": [{ "var": "note.tipo" }, ["prontuario", "resumo", "registro-documental"]] }
    effect: metadata_only
    expose: [paciente_id, unidade, psicologo_responsavel]
  - id: material-de-ensino
    when: { "in": [{ "var": "note.tipo" }, ["aula", "faq", "modelo"]] }
    effect: allow
```

A segunda política com `vinculo` cobre o psicólogo responsável. A quinta
mostra à recepção só o necessário para achar o ID e o nome do psicólogo.

### 2. Onde o filtro entra

- `BRMEngine.vault_search`, `vault_list_notes` e `vault_get_related`
  filtram o resultado pelo perfil logado antes de devolver. A filtragem é
  no engine, então vale para Obsidian e Notion.
- Notas negadas saem do `total` e do resultado, sem sinalizar que existem.
- `vault_add_note` e os fluxos de draft passam a exigir política de escrita
  (`write:` na mesma estrutura); por padrão, só `admin` e `super_admin`.
- Leitura interna do motor (`_resolve_derived_facts`) continua sem filtro,
  porque lê só o frontmatter e nunca devolve o conteúdo.

### 3. Leitura mediada por skill (`vault_read`)

Algumas consultas legítimas de perfis restritos precisam de conteúdo que a
política geral não libera, por exemplo o aluno perguntando sobre uma aula ou
o interessado lendo o FAQ. Em vez de abrir o vault, a skill declara o que
pode ler:

```yaml
- name: buscar_aulas
  vault_read: { tipo: [aula] }
```

Depois que as regras da skill passam, o motor faz a busca restrita a esses
tipos e devolve os trechos no resultado da skill. O Claude usa a skill, não o
`vault_search`, para esse caso.

### 4. Auditoria

- Cada busca registra quantas notas foram devolvidas, quantas foram
  ocultadas e as políticas aplicadas, sem registrar o conteúdo.
- `audit: acesso_privilegiado` grava o acesso da responsável técnica a
  notas que não são dela.

## Fora de escopo

- Criptografia do vault em repouso.
- Reconhecimento de nomes próprios no DLP (NER).
- Políticas por campo dentro da mesma nota.

## Testes previstos

- Matriz de perfis (recepção, psicólogo responsável, outro psicólogo,
  responsável técnica, aluno) por tipo de nota (prontuário, resumo, registro
  documental, aula, FAQ): quem vê o quê.
- Busca como `viewer` não devolve o registro documental.
- `metadata_only` devolve só os campos de `expose` e nenhum conteúdo.
- `default: deny` esconde uma nota de tipo novo até alguém liberar.
- `vault_add_note` negado para `viewer`.
- Busca com Notion falso usa o mesmo filtro.
- A demo (`scripts/demo_clinica.py`) deixa de filtrar por conta própria.

## Perguntas em aberto

1. `default: deny` ou `default: allow` para clientes existentes?
   Recomendação: `deny` para clientes de saúde e `allow` para o `example`,
   para não quebrar o que já roda.
2. A recepção deve poder achar o paciente por nome? Recomendação: sim, via
   `metadata_only` com nome completo apenas se o Instituto aprovar, porque
   é dado identificável.
