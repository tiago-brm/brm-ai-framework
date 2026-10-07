# 018 — Simulador de governança no painel (spec)

**Status:** aprovada em 2026-10-07; implementada, ver
`docs/changelog/018-simulador-de-governanca.md`. Respostas às perguntas em
aberto: a faixa só avisa (não bloqueia abas) e a matriz não inclui o DLP.

**Origem:** pedido do usuário (2026-10-07). Trocar de perfil durante uma
apresentação é frágil no Claude Desktop, porque a identidade é fixada ao
iniciar o servidor MCP. O painel `dashboard/` já é restrito a `super_admin`
e já mostra regras, permissões, vault e auditoria. A proposta é dar ao
painel um modo "ver como", que executa ações e buscas no vault como outro
perfil, mostra a decisão na hora e deixa tudo registrado.

## O que o usuário vê

Nova aba **Simulador** no painel, com quatro áreas:

1. **Perfis.** Cartões com os usuários cadastrados (nome, papel, perfil
   `viewer`/`admin`/`super_admin`). Um clique define "executando como".
   Uma faixa colorida fixa mostra "Simulando como Camila · recepção", para
   que ninguém confunda com a sessão real do super_admin.
2. **Cenários.** Lista de cenas prontas (as mesmas da demo: abrir
   prontuário sem vínculo, declaração com sintomas, relatório para a
   escola, cobrança com CPF...). Um clique executa o cenário com o perfil
   escolhido. Há também um formulário livre: escolher a skill, preencher os
   parâmetros (gerados do `skills.yaml`) e executar.
3. **Resultado.** Cartão com a decisão (`allowed`, `denied`,
   `pending_approval`), a regra que decidiu, a mensagem, o `proximo_passo`,
   os fatos derivados que o motor calculou (por exemplo, `vinculo = nao`) e
   o DLP aplicado.
4. **Vault como esse perfil.** Caixa de busca que mostra o que o perfil
   recebe: nota completa, só ficha ou nada, com a contagem de notas
   ocultadas e as políticas aplicadas.

Uma segunda tela, **Matriz de governança**, cruza cenários (linhas) com
perfis (colunas) e mostra a decisão de cada célula, calculada na hora a
partir das regras e dos fatos derivados. É a imagem mais forte de
governança: quem pode o quê, em uma tabela.

A trilha de auditoria aparece ao lado, atualizada a cada execução.

## API (em `api_router.py`, todas restritas a `super_admin`)

| Rota | Função |
|---|---|
| `GET /api/simulador/skills` | skills do cliente com parâmetros (sem os derivados), descrição e exemplos |
| `GET /api/simulador/cenarios` | cenários do arquivo `demo_scenarios.yaml` do cliente (opcional) |
| `POST /api/simulador/executar` | `{perfil_email, skill, argumentos}`: executa como o perfil, em modo simulação |
| `POST /api/simulador/vault` | `{perfil_email, consulta}`: busca no vault como o perfil |
| `GET /api/simulador/matriz` | decisão por cenário e perfil, sem efeitos colaterais |

## Motor

- `BRMEngine.call_skill(..., simulacao=True)`: roda regras, fatos derivados,
  interceptor e DLP como hoje, mas **nunca dispara efeito externo** (por
  exemplo, o envio real de `enviar_email`). O resultado traz também os
  fatos derivados e o DLP aplicado.
- `BRMEngine.avaliar_skill(skill, argumentos)`: avalia só as regras e os
  fatos derivados, sem auditoria nem efeitos. Alimenta a matriz.
- A identidade simulada vale só durante a requisição (ContextVar), nunca
  altera a sessão do super_admin.
- Cada execução grava um evento `simulacao` com `executado_por`
  (super_admin real) e `como` (perfil simulado), além dos eventos normais
  da ação. Assim a trilha distingue simulação de uso real.
- Desligável por cliente: `simulador: false` em `client_config.yaml`
  (padrão `true`), porque o recurso permite agir como outra pessoa.
- Cenários de demonstração ficam em `clients/<cliente>/config/
  demo_scenarios.yaml`; sem o arquivo, a lista de cenários fica vazia e o
  formulário livre continua funcionando.

## Painel (`dashboard/src`)

- `pages/Simulador.tsx` e `pages/Matriz.tsx` (a aba "Matriz" atual de
  permissões é de ferramentas; a nova se chamará "Governança").
- Extensão de `api.ts` com os tipos e chamadas acima, no mesmo padrão das
  outras páginas, reaproveitando `Card`, `DecisionBadge` e `PageHeader`.
- Faixa de perfil simulado sempre visível, em cor própria.

## Fora de escopo

- Chat com o Claude dentro do painel. É uma evolução possível (o painel
  chamaria a API do Claude), mas é outra spec.
- Executar com efeito real em nome de outro perfil.
- Autenticação de mais de um super_admin ao mesmo tempo.

## Testes previstos

- Rotas retornam 403 para `admin` e `viewer`.
- O mesmo pedido dá decisões diferentes por perfil (vínculo, relatório,
  cobrança, painel).
- Simulação não chama `send_email` mesmo com SMTP configurado.
- Evento `simulacao` registrado com `executado_por` e `como`.
- A busca no vault simulada respeita `vault_access.yaml` do perfil.
- A matriz não escreve na trilha de auditoria.
- `simulador: false` devolve 404 nas rotas.
- Front: `npm run build` (tipos) e verificação visual com captura de tela
  em um Chrome sem interface.

## Perguntas em aberto

1. A faixa de perfil simulado deve bloquear as outras abas do painel
   (administração) enquanto estiver ativa? Recomendação: não, só avisar.
2. A matriz deve incluir o resultado do DLP? Recomendação: não, só regras
   e fatos derivados, porque o DLP depende do conteúdo dos argumentos.
