# Documentação

Sistema de documentação no formato *Vibe Coding Blueprint*: **contexto antes do código**. Os documentos são a fonte de verdade compartilhada entre você e o agente de código — o agente lê, implementa, valida e mantém tudo alinhado com o código.

```text
AGENTS.md  (raiz)        # como o agente deve trabalhar (CLAUDE.md importa este arquivo)
docs/
├── PRD.md               # o que construímos: escopo, features F-xx, fases, métricas
├── ARCHITECTURE.md      # como funciona: componentes, fluxo, regras de dependência, decisões D-NNN
├── DATABASE.md          # dados: SQLite, YAML, auditoria, vault, índice
├── SECURITY.md          # o que proteger e como; lacunas conhecidas L-NN
├── CODE_STYLE.md        # como escrever código aqui
├── TESTING.md           # como saber que funciona; comandos; baseline
├── UX_FLOWS.md          # jornadas (conversa e painel) com estados de sucesso/falha
├── DESIGN_SYSTEM.md     # linguagem visual do painel
├── API.md               # contratos: tools MCP, REST, auditoria
└── apresentacao-clinica/   # material de apresentação (não faz parte do sistema de docs)
```

| Documento | Pergunta que responde |
|---|---|
| [PRD](PRD.md) | O que estamos construindo? |
| [ARCHITECTURE](ARCHITECTURE.md) | Como o sistema deve funcionar? |
| [DATABASE](DATABASE.md) | Que dado existe e como se relaciona? |
| [SECURITY](SECURITY.md) | O que precisa ser protegido e como? |
| [CODE_STYLE](CODE_STYLE.md) | Como o código deve ser escrito? |
| [TESTING](TESTING.md) | Como saberemos que funciona? |
| [UX_FLOWS](UX_FLOWS.md) | Como o usuário se move pelo produto? |
| [DESIGN_SYSTEM](DESIGN_SYSTEM.md) | Qual deve ser a aparência do painel? |
| [API](API.md) | Quais são os contratos? |
| [AGENTS](../AGENTS.md) | Como o agente de IA deve se comportar? |

## Como usar

1. Antes de implementar, peça ao agente para **ler os documentos relevantes**.
2. Substitua placeholders por decisões reais ou por *Open Questions* explícitas.
3. Implemente **uma feature coerente por vez**, com referência ao `F-xx` e critérios de aceite.
4. Valide o comportamento e **atualize a documentação no mesmo commit**.
5. Decisão técnica nova → linha `D-NNN` em [ARCHITECTURE](ARCHITECTURE.md) §9. Lacuna de segurança nova → [SECURITY](SECURITY.md) §10.

**Mantenha leve:** documente o que afeta a implementação; não duplique regra (linke); sem arquivos gigantes e desatualizados; ligue requisitos a resultados verificáveis.

## Convenção de identificadores

- `F-xx` feature ([PRD](PRD.md)) · `D-NNN` decisão ([ARCHITECTURE](ARCHITECTURE.md); mesma numeração das antigas specs, para que comentários "spec NNN" no código continuem resolvendo) · `L-NN` lacuna de segurança ([SECURITY](SECURITY.md)).

## Prompts prontos

> Adaptados ao projeto. Troque os `[colchetes]`.

**1 · Transformar uma ideia em documentação**
```text
Leia AGENTS.md e os docs relevantes em docs/ e o repositório.
Minha ideia: [ideia]. Usuários: [usuários]. Restrições: [restrições].
Atualize o PRD (feature F-xx com escopo MVP, non-goals, user stories, critérios de aceite e edge cases).
Marque suposições e decisões em aberto explicitamente. Não escolha stack nova nem implemente código ainda.
```

**2 · Implementar uma feature**
```text
Implemente [F-xx / feature] usando docs/PRD.md, ARCHITECTURE.md, SECURITY.md e API.md.
Leia o código existente primeiro. Liste arquivos afetados e um plano curto. Reuse os padrões atuais.
Respeite escopo, permissões, regras e DLP (as lentes BRM do AGENTS.md). Cubra a negação em teste.
Rode ruff e pytest, atualize a documentação afetada e relate: o que mudou, o que testou, o que NÃO testou, limitações.
```

**3 · Corrigir um bug**
```text
Comportamento observado: [o que acontece]. Esperado: [o que deveria]. Passos: [passos].
Inspecione o fluxo afetado e ache a causa raiz. Faça uma correção focada, com um teste que falhava antes.
Confira o comportamento adjacente. Não reescreva funcionalidades não relacionadas.
```

**4 · Revisar antes de entregar**
```text
Revise os arquivos alterados contra PRD, ARCHITECTURE, API e SECURITY (§9 e lacunas L-NN).
Verifique permissões, validação, estados de falha, migrações, auditoria sem dado sensível.
Reporte achados concretos com impacto e localização (arquivo:linha). Separe problemas verificados de dúvidas.
Não diga que um check passou se ele não foi executado.
```

**5 · Onboarding de cliente** — use a skill `.claude/skills/brm-onboarding` (guardrails primeiro, depois vault, regras e documentos por rascunho).

## Checklists

**Antes de implementar**
- [ ] Problema e usuário-alvo claros; MVP e non-goals escritos
- [ ] Fluxos críticos incluem estados de loading, vazio, sucesso e erro
- [ ] Arquitetura, dados e API concordam entre si
- [ ] Segurança e perfis explícitos; nenhuma lacuna `L-NN` piora
- [ ] AGENTS.md aponta para os documentos certos

**Durante**
- Uma feature coerente por vez; arquivos e critérios de aceite no pedido.
- Conflito relevante é resolvido, não escolhido em silêncio.
- Migrações e mudanças destrutivas revisadas antes de rodar.
- Segredos fora de prompts, repositório, screenshots e logs.

**Antes de entregar**
- [ ] Testes, lint e build aplicáveis rodados
- [ ] Fluxos críticos verificados quando necessário
- [ ] Permissões, entradas inválidas e falhas conferidas
- [ ] Arquivos alterados revisados contra edições não intencionais
- [ ] Docs refletem o comportamento implementado
- [ ] Checks não executados e limitações restantes registrados

## Histórico

As antigas `docs/spec.md`, `docs/specs/001–018`, `docs/changelog/`, `docs/guia-teste-claude-desktop.md` e `docs/SETUP-MCP-CLAUDE-CLI.md` foram substituídas por este conjunto (decisão D-019). O texto original permanece no git: `git show f7cda5b:docs/spec.md` (ou `git log -- docs/specs`). O knowledge graph em `graphify-out/` foi gerado em 2026-08-26 a partir dos documentos antigos e **está desatualizado**; regenere-o ou ignore-o.
