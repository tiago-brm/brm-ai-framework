# 029 — Avaliação de intenção e testes E2E de transporte

**O quê:** implementa a infraestrutura da spec
`docs/specs/029-avaliacao-de-intencao-e-testes.md`: testes E2E por HTTP/SSE e
stdio e o executor de avaliação de intenção. Não altera o núcleo (`mcp_engine/`).

## E2E de transporte (`tests/e2e/`)

- Cliente MCP real (SDK `mcp`) contra o motor por **HTTP/SSE** (uvicorn em
  porta efêmera numa thread, encerrado ao fim de cada teste) e por **stdio**
  (subprocesso do `main()` de produção, com `MCP_USER_EMAIL`, `CLIENT_ID`,
  `CONFIG_ROOT` e `DATA_ROOT` de um cliente temporário).
- Cliente temporário montado por `evals/ambiente.py` a partir de
  `evals/fixtures/clients/avaliacao/` (regras, skills, `client_config`,
  `vault_access`), com quatro pessoas (viewer, dois admins, super_admin),
  tokens gerados na hora e permissões explícitas por perfil.
- **Skills nativas no stdio:** o handler vive no processo do motor, então
  `tests/e2e/stdio_launcher.py` registra os handlers de teste logo após a
  construção do `BRMEngine` e delega ao `main()` real. Escolhido por ser a
  opção mais simples e manter o caminho de produção (identidade, RBAC,
  auditoria) intacto.
- Cobertura: token válido executa; token inválido ou ausente dá 401; sem
  permissão nega e audita; tudo auditado com identidade, papel e `trace_id`;
  lista de ferramentas filtrada por perfil nos dois transportes; propor →
  confirmar (A2); propor → aprovar por outro perfil (regra de valor alto e
  A3); aprovar a própria operação negado; cadeia de auditoria verificada ao
  final e adulteração detectada. Duração: cerca de 8 s.

## Avaliação de intenção (`evals/`, `scripts/rodar_avaliacao.py`)

- Corpus `evals/intents/geral.yaml`: 23 casos agnósticos de setor (frase em
  português, perfil, ferramenta esperada ou `nenhuma`, parâmetros, categoria,
  `critico`, ferramentas `proibidas`).
- `evals/runner.py`: dois backends. `ModeloFalso` (determinístico, sem rede)
  testa o próprio executor; `ModeloAnthropic` (opt-in: `EVAL_LIVE=1` e
  `ANTHROPIC_API_KEY`) usa tool use da API da Anthropic sobre as ferramentas
  reais do servidor, filtradas pelo perfil do caso. Modelo por `EVAL_MODEL`
  (padrão `claude-opus-5-5`, confirmado na skill claude-api). Uma rodada por
  caso, sem executar a ferramenta: mede a intenção. **O backend real não foi
  executado nesta entrega** (sem chave).
- Métricas: acerto de ferramenta, de parâmetros, recusa correta, falso
  positivo, acerto dos casos críticos (valor e data), execuções sem
  confirmação (`confirmar_operacao`/`aprovar_operacao` fora do pedido) e
  injeções bem-sucedidas (ferramenta proibida chamada).
- Aceite da spec codificado: ferramenta ≥ 95%, críticos 100%, 0 execuções sem
  confirmação, e (adição desta entrega) 0 injeções bem-sucedidas.
- Relatório JSON em `evals/runs/` com modelo e `ferramentas_sha256` por
  perfil: trocar o modelo ou a descrição de uma ferramenta fica visível ao
  comparar relatórios.
- CLI: `python scripts/rodar_avaliacao.py --backend falso|anthropic`; sai
  com 1 se o aceite falhar e 2 se faltar configuração do backend real.

## Red Team / Blue Team (cada risco da spec tem teste)

| Risco | Teste |
|---|---|
| Suíte só com casos fáceis | `test_corpus_cobre_casos_obrigatorios_e_adversariais` |
| Troca de modelo/descrição sem reavaliar | `test_mudar_descricao_de_ferramenta_muda_a_impressao_do_relatorio` |
| Erro de dinheiro ou data passa | `test_erro_de_valor_...`, `test_erro_de_data_...`, `test_valor_ambiguo_...` |
| Efeito sem confirmação | `test_confirmar_sem_o_usuario_pedir_...` |
| Injeção em nome, observação ou comprovante | `test_injecao_que_dispara_ferramenta_proibida_reprova` |
| Estorno sem permissão, aprovar a própria operação | E2E `test_sem_permissao_...`, `test_aprovar_a_propria_operacao_e_negado` |
| Critério de piloto vago | `test_limites_de_aceite_da_spec`, `test_resumo_aprova_no_limite_...` |

## Resultados

Suíte completa: 464 passando; 17 de avaliação e 14 E2E novos entre eles.
Dois erros em `tests/test_health_e_data_dir.py` pertencem a outra frente em
andamento (configuração de cliente), não a esta.

## Pendente

Corpus de financeiro (depende do módulo da spec 023), rodada com o modelo
real, casos reais anônimos pós-piloto, reutilização de token de confirmação
no corpus de intenção, piloto de 30 dias, teste de mutação e postmortems.
