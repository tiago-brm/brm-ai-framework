# 018 — Simulador de governança no painel

**O quê:** implementa a spec `docs/specs/018-simulador-de-governanca.md`
(aprovada em 2026-10-07): o painel `dashboard/` ganha as abas **Simular**
(ver como outro perfil) e **Governança** (matriz de quem pode o quê).

- `mcp_engine/core/api_router.py`: rotas `/api/simulador/perfis`, `skills`,
  `cenarios`, `executar`, `vault` e `matriz`, todas restritas a
  `super_admin` e com 404 quando o cliente desliga o simulador. A
  identidade simulada vale só durante a requisição (ContextVar) e nunca
  altera a sessão do super_admin. Cada execução grava um evento
  `simulacao:<ação>` com `executado_por` e `como`.
- `mcp_engine/core/mcp_server.py`
  - `call_skill(..., simulacao=True, trace=...)`: mesmo caminho de regras,
    fatos derivados, interceptor e DLP, sem enviar e-mail de verdade, e
    devolve os fatos derivados e o DLP aplicado.
  - `avaliar_skill`: só regras e fatos derivados, sem auditoria nem
    efeitos. Alimenta a matriz.
  - `vault_snapshot`: reindexa uma vez e reaproveita as notas na matriz
    (de 9 s para 0,03 s com 17 cenários e 7 perfis).
  - `vault_search` informa `ocultas` e `politicas` quando há política de
    acesso.
  - A auditoria das ações passa a registrar a identidade da pessoa logada
    em vez de "claude"; sem login, continua "claude".
- `mcp_engine/core/models.py`, `config_loader.py`: `ClientConfig.simulador`
  (padrão `true`) e `DemoScenarios` (`demo_scenarios.yaml`, opcional).
- `mcp_engine/core/http_app.py`: aquece o índice do vault ao iniciar, para
  que o primeiro pedido do painel não espere o carregamento do modelo.
- `clients/instituto-afw/config/demo_scenarios.yaml`: 19 cenários e os
  rótulos dos perfis. Dois cenários dependem do DLP e ficam fora da matriz
  (`in_matrix: false`).
- `dashboard/src`: `pages/Simulador.tsx`, `pages/Governanca.tsx`, tipos e
  chamadas em `api.ts`, itens no menu. Um clique em outro perfil repete o
  último cenário e a última busca no vault.
- `scripts/preparar_demo_chat.py`: cria também os perfis da aluna e do
  supervisor e usa tokens fixos `demo-<nome>` (só no cliente de
  demonstração). O token do painel é `demo-helena`.

## Decisões sobre as perguntas em aberto

- A faixa de simulação só avisa e não bloqueia as outras abas.
- A matriz não inclui o DLP, que depende do conteúdo do pedido.

## O que ainda não existe

- Chat com o Claude dentro do painel.
- Executar com efeito real em nome de outro perfil.
- A matriz mostra o efeito das regras; cenários em que o resultado depende
  só do cadastro (matrícula, frequência) aparecem iguais para todos os
  perfis, porque a decisão vem do dado, não do perfil.

**Validação:** `tests/test_simulador.py` (16 testes), suíte completa com
281 testes e `ruff check .` limpos; `npm run build` do painel (tipos e
bundle) e capturas de tela em um Chrome sem interface, usando o motor real
do `instituto-afw`, sem erros de console.
