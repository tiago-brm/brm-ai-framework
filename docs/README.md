# Documentação — convenção de trabalho

Três pastas, cada uma com um papel distinto. O objetivo é que **nenhuma decisão
viva só na conversa**: tudo que foi decidido está em disco, numerado em sequência,
e pode ser lido de volta numa sessão nova sem reconstruir o contexto inteiro.

```
docs/
├── spec.md          # arquitetura geral (v2.1) — a fonte de verdade, não muda por passo
├── specs/           # 00N-<tema>.md — desenho PROPOSTO de cada passo, aprovado ANTES de codar
└── changelog/       # 00N-<tema>.md — o que foi REALMENTE implementado, escrito DEPOIS
```

## Ciclo por passo

1. **Spec** — escrevo `docs/specs/00N-<tema>.md` com o desenho do próximo passo:
   o que será construído, quais arquivos, quais decisões e por quê.
2. **Aprovação** — você lê e aprova (ou pede ajuste). O spec aprovado fica em disco.
3. **`/clear`** — assim que o spec é aprovado, limpa-se o contexto do modelo. Todo
   o desenho já está em `docs/specs/00N-*.md`; a discussão que levou até ele não
   precisa ser carregada adiante. É isso que impede o contexto de estourar no meio
   da implementação.
4. **Implementação** — codifico seguindo o spec aprovado, lendo-o do disco. Se a
   sessão acabar ou o contexto estourar, a sessão seguinte lê o spec e continua sem
   precisar rediscutir nada.
5. **Changelog** — escrevo `docs/changelog/00N-<tema>.md` com o que de fato saiu:
   arquivos, decisões tomadas no caminho, resultado de lint/testes.
6. **Graphify** — `graphify --update` incorpora spec + código + changelog ao
   knowledge graph (`graphify-out/`), que passa a responder consultas via
   `graphify query "..."` em vez de reler os documentos inteiros.

A numeração de `specs/` e `changelog/` é a mesma: `001-contracts.md` em specs é o
desenho aprovado, `001-contracts.md` em changelog é o relato do que foi feito.

## Por que essa separação

- **spec.md** é arquitetura de longo prazo — muda raramente, e só por decisão
  explícita.
- **specs/** é compromisso antes do código — é onde a discussão acontece, e o
  que evita implementar a coisa errada.
- **changelog/** é histórico honesto — inclui o que divergiu do spec e por quê.
