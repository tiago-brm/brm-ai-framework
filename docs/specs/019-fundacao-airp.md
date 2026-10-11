# 019 — Fundação AIRP: arquitetura alvo, princípios e roadmap (spec)

**Status:** proposta, aguardando aprovação (2026-10-09).

**Origem:** pedido do usuário (2026-10-09). O BRM Framework hoje é um gateway
MCP governado (regras, DLP, RBAC, auditoria, vault). Para sustentar um AIRP
(ERP operado por linguagem natural) falta a camada de execução e de dados.
Esta spec é o guarda-chuva das specs 020 a 029. A clínica (`clinica-psi`) é o
cliente piloto; o núcleo deve ser agnóstico de cliente.

## Princípios

1. **O LLM interpreta, o motor decide e executa.** O modelo propõe uma
   chamada de ferramenta com parâmetros tipados. Regras, fatos derivados e
   validação ficam no motor.
2. **Números só saem de ferramentas.** O Claude nunca soma, converte nem
   lembra valores. Toda consulta numérica é uma ferramenta sobre o ledger.
3. **Efeito exige confirmação.** Toda operação que grava ou envia passa por
   proposta, confirmação e, quando a regra pedir, aprovação de outro perfil
   (spec 022).
4. **Nada se apaga.** Correções são lançamentos novos que apontam para o
   original. A auditoria é encadeada por hash.
5. **Identidade por pessoa.** Todo evento registra quem pediu (spec 020).
6. **Negar por padrão.** Ferramenta sem permissão explícita é negada.
7. **Local primeiro.** Dados em SQLite e arquivos no volume do cliente; o
   que vai ao LLM é o mínimo necessário (agregados em vez de linhas).
8. **Caderno é a interface, motor é a autoridade.** Pessoas escrevem e leem
   tudo no SilverBullet; nada vale sem validação e aprovação no motor (030).
9. **Núcleo agnóstico.** O motor não conhece "clínica". Domínio entra por
   módulos habilitados por cliente (spec 027).

## Arquitetura alvo

```
Interfaces   Claude Desktop (MCP) · painel · (WhatsApp, Fase 2)
   │
Gateway MCP  identidade · RBAC default deny · regras JSONLogic · DLP entrada/saída
   │
Operações    proposta → confirmação → aprovação → execução (spec 022)
   │
Executor     handlers nativos (módulos) · conectores HTTP (spec 021)
   │
Dados        ledger SQLite · vault markdown · state.db · audit.jsonl
```

## Mapa de specs

| Spec | Tema | Depende de |
|---|---|---|
| 020 | Identidade, RBAC default deny, segredos e DLP de entrada | n/a |
| 021 | Executor de skills (nativo e HTTP) e SecretStore | 020 |
| 022 | Operações com confirmação e aprovação | 020, 021 |
| 023 | Módulo financeiro: ledger gerencial em SQLite | 021, 022 |
| 024 | Importador e exportador de planilhas | 023 |
| 025 | Segundo cérebro: vault, SilverBullet e memória | 020 |
| 026 | Conector BRM Agenda governado | 021, 022 |
| 027 | Cliente agnóstico: módulos e template de cliente | 021 |
| 028 | Empacotamento Docker, backup e caminho para a nuvem | todas |
| 029 | Avaliação de intenção e plano de testes da Fase 1 | todas |
| 030 | Caderno (SilverBullet) como interface de configuração de tudo | 020, 022, 025 |
| 031 | Gatilhos e automações declarativas | 021, 022, 030 |

## Marcos

- **M1 Segurança e execução:** 020, 021, 022, 027.
- **M2 Dados e caderno:** 023, 024, 025, 030.
- **M3 Automação, integração e empacotamento:** 031, 026, 028.
- **M4 Piloto:** 029 em execução paralela com as planilhas por 30 dias.

## Fase 2 (fora desta rodada, só registrada)

- NFS-e pela API do Emissor Nacional, atrás de uma interface
  `FiscalProvider`, com certificado A1 do contribuinte.
- Contas a receber derivadas das sessões realizadas na Agenda.
- Cobrança Pix/boleto com baixa automática, BI sob demanda, WhatsApp real,
  reconhecimento de nomes (NER) no DLP, control plane multi-tenant e Postgres.

## Fora de escopo

Contabilidade formal (plano de contas, DRE contábil, SPED), folha de
pagamento, conciliação bancária completa.

## Critério de pronto para a nuvem

Suite verde, E2E por HTTP/SSE, restauração de backup testada, decisão de
criptografia em repouso, avaliação de intenção acima dos limites da spec 029
e um mês de piloto sem divergência não explicada contra as planilhas.

## Perguntas em aberto

1. Repasse: **existe, com comissões diferenciadas e parametrizável**
   (respondido em 2026-10-09; modelado na spec 023). Falta a base de cálculo
   padrão (bruto ou líquido da taxa) e a tabela de percentuais.
2. Quantas secretárias e unidades geram planilhas por mês? (não bloqueia; o
   importador é agnóstico)
3. Regime tributário da clínica (relevante para a Fase 2)?
4. Hospedagem alvo na nuvem (afeta só a spec 028, não o núcleo)?

## AIRP como conceito novo

AIRP não é um ERP com chat por cima nem um chatbot com acesso a banco. É um
sistema de registro operado por linguagem natural, em que **a governança é
parte do produto**: o mesmo pedido gera decisões diferentes por pessoa, toda
ação tem rastro e o modelo de linguagem nunca é a fonte de um número nem a
autoridade de uma decisão. O método de desenho segue o `docs/spec.md`: cada
spec traz **Red Team** (como quebrar), **Blue Team** (controle) e
**Postmortem antecipado** (por que falharia em produção e o que já se faz
para evitar). Os pilares **God Mode** e **L-99** (definidos abaixo) entram como lentes do
mesmo desenho.

## Red Team / Blue Team / Postmortem (nível fundação)

| Risco (Red Team) | Controle (Blue Team) | Spec |
|---|---|---|
| Prompt injection: texto de paciente, nota ou comprovante instrui o modelo a pular regras | Regras e permissões vivem fora do modelo; o motor decide com fatos derivados do cadastro | 020, 022 |
| Modelo inventa ou converte valor | Valor só entra por campo tipado (`parse_brl`) e é mostrado no resumo de confirmação | 022, 023 |
| Usuário legítimo abusa do papel (lança, estorna, apaga rastro) | Segregação de funções, imutabilidade e auditoria por pessoa | 020, 022, 023 |
| Vazamento de dado sensível ao LLM | DLP bidirecional, consultas agregadas por padrão | 020, 023 |
| Segredo ou certificado exposto | SecretStore fora do repositório, nunca em log | 021 |
| Perda ou corrupção do ledger | WAL, backup cifrado, restauração testada | 028 |

**Postmortem antecipado.** O que mais derrubaria o piloto, em ordem:
(1) secretárias continuam na planilha e o ledger diverge, tratado por
operação paralela de 30 dias com reconciliação mensal (029);
(2) o modelo escolhe a ferramenta errada, tratado pela suíte de avaliação
(029); (3) demo vendida como produto, tratado por rotular o que é mock e por
trocar o mock pelo executor real (021); (4) backup nunca restaurado (028).

## Os cinco pilares como gate de desenho

Toda spec deve responder aos cinco pilares antes de ser aprovada, e toda
entrega só fecha com a evidência correspondente.

| Pilar | Pergunta | Evidência exigida |
|---|---|---|
| **Red Team** | Como isso é quebrado? | Tabela de riscos com teste adversarial para cada linha |
| **Blue Team** | Qual controle impede, detecta ou limita? | Controle implementado e testado, não só descrito |
| **Postmortem** | Por que falharia em produção? | Análise antecipada; após incidente real, `docs/postmortems/NNN.md` sem busca de culpado |
| **God Mode** | Dá para ver o sistema inteiro de cima, e onde a autonomia é segura? | `trace_id` ponta a ponta, painel panorâmico e nível de autonomia declarado por skill |
| **L-99** | Está no nível de um especialista sênior? | Invariantes explícitas, modos de falha enumerados, testes de propriedade, orçamentos de desempenho, runbook, e justificativa de que é a solução mais simples que resolve, com recursos atuais verificados |

### God Mode: autonomia graduada

God Mode no produto **não** significa o modelo agir sem controle. Significa
que cada skill declara um nível de autonomia, e quanto mais seguro o efeito,
menos atrito:

| Nível | Efeito | Comportamento |
|---|---|---|
| A0 | Somente leitura | Executa direto |
| A1 | Efeito pequeno e reversível, dentro de limites | Executa e devolve resumo; desfazível por estorno |
| A2 | Efeito relevante | Proposta e confirmação do usuário |
| A3 | Alto risco ou irreversível | Aprovação de outro perfil |

O `super_admin` (o "modo deus" real do sistema) continua podendo tudo, mas
nunca em silêncio: ver `break_glass` na spec 020.

### L-99: definição de pronto

Uma spec só é "pronta" quando traz: invariantes numeradas, modos de falha
com comportamento definido (fail-closed quando envolve dinheiro, dado
sensível ou auditoria), orçamento de desempenho, estratégia de migração e
reversão, e runbook de operação.

### L-99: simplicidade efetiva e recursos atuais

Maestria é resolver o problema real com o menor mecanismo que o resolve bem.
Regras de desenho, aplicadas a toda spec e a todo código:

1. **A solução mais simples que funciona de forma comprovada.** Antes de
   criar peça nova, tentar resolver com o que já existe no stack (SQLite,
   Pydantic, FastMCP, JSONLogic, o próprio motor de regras). Cada peça nova
   precisa de uma frase dizendo por que a existente não basta.
2. **Efetivo, não apenas enxuto.** Simples não pode custar segurança,
   rastro ou correção. Os invariantes e os modos de falha da spec não são
   negociáveis em nome de simplicidade.
3. **Recursos atuais, verificados.** Antes de implementar, conferir a
   documentação oficial e a versão estável corrente da biblioteca, do
   protocolo ou do recurso (MCP, FastMCP, Pydantic, SQLite, Docker, API do
   Emissor Nacional). Preferir o recurso nativo novo ao código próprio que o
   reimplementa. Não usar de memória.
4. **Atual não é experimental.** Usar versão estável; fixar a versão em
   `uv.lock` e no Dockerfile; registrar no changelog a versão adotada e o que
   foi verificado. Recurso em beta só com justificativa e plano de saída.
5. **Menos código para manter.** Remover o que um recurso atual substitui,
   adiar o que não é preciso agora (marcado como Fase 2) e preferir
   configuração a código.

### Revisão de simplicidade já aplicada a estas specs

| Spec | Simplificação |
|---|---|
| 021 | SecretStore da Fase 1 usa só variáveis de ambiente e Docker secrets, sem formato próprio |
| 023 | Tabelas `STRICT` e `CHECK` do SQLite fazem a validação no banco, em vez de código duplicado |
| 028 | O proxy com TLS só entra quando houver acesso remoto |

Cada item de implementação confere, antes de começar, a versão e a
documentação atuais do recurso que vai usar, e registra o resultado no
changelog da spec.
