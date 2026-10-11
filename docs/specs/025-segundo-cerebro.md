# 025 — Segundo cérebro: vault, editor web (SilverBullet) e memória (spec)

**Status:** implementada (2026-10-09). Ver "Como ficou a implementação" no fim.

**Origem:** decisão do usuário (2026-10-09): vault em markdown local, editor
web open source (SilverBullet) em Docker, agnóstico de cliente. O Obsidian
não roda como serviço; o motor lê arquivos, então o editor é intercambiável.

## Papel do SilverBullet

O SilverBullet é o **caderno de tudo**: único lugar onde pessoas escrevem e
leem conhecimento, regras, gatilhos e parâmetros. O motor BRM é a autoridade
que valida, aprova e executa. O mecanismo está na spec 030.

### O que não entra no caderno

Dado transacional ou cadastral de paciente e de agenda **não** é guardado no
SilverBullet. Fica nos sistemas de origem: agendamentos e cadastros na
Agenda, dinheiro no ledger. Motivos: markdown não tem transação, restrição de
integridade nem consulta; a credencial do editor é única e abriria tudo; e é
dado sensível (LGPD) duplicado. O caderno guarda texto: políticas, regras,
parâmetros aprovados, procedimentos, relatórios gerados. Conteúdo clínico
(evolução, prontuário) é decisão da Fase 2 e, se vier, usa as zonas da
spec 017.

## Estrutura do vault (agnóstica)

```
vault/
  _modelos/        modelos de nota e de documento
  politicas/       regras de negócio em texto (juros, estorno, NF, acordo)
  glossario/       termos e siglas do cliente (DEB, CRED, DINH, S/NF)
  procedimentos/   passo a passo (registrar pagamento, fechar o mês)
  cadastros/       descrições de unidades, profissionais, contas
  relatorios/      relatórios gerados pelo motor (somente leitura)
  memoria/         fatos duráveis aprovados
```

`clients/_template/vault/` traz essa estrutura vazia com notas-exemplo. Cada
nota tem frontmatter (`tipo`, `zona`, `atualizado`) que alimenta
`vault_access.yaml`.

## Acesso

- Zonas: `politicas`, `glossario`, `procedimentos` lidas por todos os
  perfis operacionais; `cadastros` por admin; `relatorios` por gestor e
  super_admin; `memoria` lida por todos, promovida só por gestor.
- A política padrão continua **negar** (spec 017).
- **Ressalva de segurança:** o SilverBullet edita arquivos direto e não passa
  pelo RBAC do MCP. Credencial do editor só para gestor/super_admin; os demais
  perfis usam o chat. O editor sobe em perfil Docker opcional (`editor`),
  atrás de usuário e senha, ouvindo só em `127.0.0.1` por padrão.
- **Correção após a pesquisa na documentação (2026-10-09):** o SilverBullet não
  tem permissão por pasta nem somente leitura por subpasta, e quem tem escrita pode
  rodar Space Lua no navegador de quem abrir o espaço. `sistema/` e `relatorios/`
  são protegidas por montagem `:ro` no Docker e pelo motor (hash e restauração), e
  a credencial de escrita é de gestor. Detalhes em
  `docs/runbooks/silverbullet-seguranca.md`.

## Relatórios gerados

`gerar_relatorio_mensal(periodo, unidade)` escreve uma nota em
`relatorios/` com números do ledger por **template determinístico**, nunca
redigidos pelo LLM. O vault indexa a nota; o Claude pode citá-la, mas a
fonte do número é o ledger.

## Memória

- **Conhecimento durável** (preferências, decisões, exceções combinadas):
  ferramenta `memoria_registrar` cria rascunho em `memoria/` que o gestor
  promove, reaproveitando o `DraftStore` das specs 009 e 017.
- **Contexto de sessão:** o servidor não guarda conversa. O estado em
  andamento é a tabela `operacao` (spec 022).
- Nada é aprendido sozinho: toda memória passa por rascunho e promoção.

## Red Team / Blue Team / Postmortem

| Risco (Red Team) | Controle (Blue Team) |
|---|---|
| Nota de memória com instrução maliciosa ("ignore as regras") | Memória é dado, nunca entra como regra; promoção humana; regras só em `rules.yaml` |
| Usuário edita política no editor web e muda o comportamento sem rastro | Editor restrito a gestor; mudanças em `politicas/` auditadas pelo watcher (hash da nota) |
| Relatório adulterado à mão no editor | `relatorios/` somente leitura no editor; hash do relatório gravado na auditoria |
| Editor exposto na internet | Escuta local por padrão; exposição só por proxy com TLS (spec 028) |
| Memória cresce sem controle e contamina respostas | Limite por nota, revisão periódica, expiração opcional |

**Postmortem antecipado.** Um vault bagunçado vira ruído na busca. Estrutura
fixa, modelos de nota e uma revisão mensal listada em `procedimentos/`.

## Testes previstos

- Zonas respeitam `vault_access.yaml` por perfil.
- `gerar_relatorio_mensal` produz os mesmos números do ledger e é idempotente.
- Memória em rascunho não aparece na busca até ser promovida.
- Watcher reindexa alteração feita por editor externo.

## Fora de escopo

Sincronização com Notion, edição colaborativa em tempo real, memória
automática por conversa.

## God Mode / L-99

**God Mode.** Mapa de cobertura do conhecimento: relatório que cruza skills
com efeito e notas de política, apontando skill sem política documentada e
nota sem skill. Visão única do que o sistema "sabe" e do que falta.

**L-99.** Conhecimento de domínio é tratado como especialista:
frontmatter obrigatório com `fonte`, `revisado_por` e `revisado_em`; nota
sem revisão em 12 meses aparece como vencida. Políticas fiscais, clínicas
ou trabalhistas só são promovidas com revisor nomeado. Runbook: editor fora
do ar, vault corrompido, restauração de nota.

## Como ficou a implementação

**Entregue:** `mcp_engine/vault/segundo_cerebro.py` (conhecimento com revisão,
memória, geradores de relatório), `mcp_engine/vault/escrita_motor.py` (escrita do motor
em `sistema/` e `relatorios/` com hash e restauração), mapa de cobertura em
`sistema/mapa-de-cobertura.md`, template do vault completo (`clients/_template/vault`:
`sistema/`, modelos, exemplos e procedimento de revisão mensal) e zonas no
`vault_access.yaml` do template. Ferramentas MCP: `memoria_registrar`,
`memoria_rascunhos`, `memoria_promover`, `gerar_relatorio_mensal`.

**Decisões e desvios**

- **Zonas** vêm do campo `zona` no frontmatter; o template libera `politicas`,
  `glossario`, `procedimentos` e `memoria` a todos, `cadastros`, `sistema` e `relatorios`
  a admin e super_admin, e nega o resto (nota sem zona fica negada).
- **Revisão:** `fonte`, `revisado_por` e `revisado_em` são exigidos nas notas de
  conhecimento. A falta aparece como "incompleto" e a revisão com mais de 365 dias como
  "vencida" em `sistema/alertas.md`. Áreas reguladas (`area: fiscal|saude|trabalhista|
  juridica`) sem revisor ficam "bloqueado" e fora do mapa de cobertura. Não impede a
  leitura; o aviso é o controle.
- **Memória:** rascunho no `DraftStore` (tipo `memoria`), promoção por gestor para
  `memoria/`. Limites: 2000 caracteres por nota e 200 notas; DLP de entrada; bloco
  `brm-config` recusado; a pasta e o tipo `memoria` nunca viram configuração. Expiração
  opcional (`expira_em`) aparece nos alertas; a busca não esconde memória expirada.
- **Relatórios:** `Gerador(nome, descricao, dados, template)` registrado por módulo via
  `host.registrar_gerador_relatorio`. O texto vem de um `string.Template` fixo; os números
  vêm da função do módulo. A nota é idempotente, tem o hash na auditoria e é restaurada se
  editada à mão. O ledger ainda não registrou gerador; o teste usa um gerador de teste.
- **Mapa de cobertura:** a política cita as skills no campo `skills` do cabeçalho. "Com
  efeito" é autonomia diferente de A0, irreversível ou método diferente de GET.
- **Fora desta entrega:** parte da spec que dependia do ledger (números reais) e o
  perfil Docker do editor, que é da frente do Docker.

## Decisão de acesso ao SilverBullet na Fase 1 (2026-10-09)

A interface do SilverBullet sobe, mas o acesso é **somente do superadmin da
BRM**, em rede privada, para ver e manter regras, conhecimento e relatórios.
Os usuários da clínica não editam no SilverBullet na Fase 1: usam o chat e o
painel. O acesso do cliente ao caderno fica como evolução, depois de resolver
as pendências registradas em `docs/runbooks/silverbullet-seguranca.md`
(credencial por pessoa no modo multi-space, montagens `:ro` de `sistema/` e
`relatorios/`, e exposição por VPN ou proxy com TLS).
