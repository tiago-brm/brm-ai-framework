# 022 — Operações com confirmação e aprovação (spec)

**Status:** proposta, aguardando aprovação (2026-10-09).

**Origem:** hoje `require_approval` só devolve `allowed:false` com um
`proximo_passo`. Não há fila, retomada, expiração nem aprovador identificado.
Para financeiro e agenda é preciso o padrão **propor, confirmar, executar**.

## Modelo

Tabela `operacao` em `data/<cliente>/state.db` (SQLite, SQLAlchemy):

| Campo | Observação |
|---|---|
| `id` | UUID |
| `skill`, `argumentos` | argumentos normalizados (JSON) |
| `resumo` | texto humano gerado pelo motor, não pelo LLM |
| `status` | `proposta`, `aguardando_aprovacao`, `confirmada`, `executada`, `falhou`, `expirada`, `cancelada`, `rejeitada` |
| `proposto_por`, `confirmado_por`, `aprovado_por` | `user_id` |
| `aprovador_papel` | papel exigido pela regra |
| `idempotency_key`, `digest_argumentos` | trava contra alteração |
| `criado_em`, `expira_em`, `executada_em`, `resultado_ref` | |

## Fluxo

1. O Claude chama a skill (ex.: `registrar_recebimento`). Para skills com
   `confirmacao: obrigatoria`, o motor **não executa**: valida, deriva fatos,
   avalia regras e grava a operação com `resumo` ("Recebimento de R$ 275,00,
   Maria, Pix Stone, Dra. Wagner, 09/10").
2. O Claude mostra o resumo ao usuário. O usuário confirma em linguagem
   natural e o Claude chama `confirmar_operacao(id)`.
3. Se a regra deu `allow`, a confirmação executa. Se deu `require_approval`,
   a operação passa a `aguardando_aprovacao` e só executa depois de
   `aprovar_operacao(id)` feita por perfil com o `aprovador_papel`.
4. `expira_em` padrão 24 horas; operação expirada não executa.

## Regras de segurança

- O `digest_argumentos` é conferido na confirmação; qualquer mudança
  invalida a operação.
- **Segregação:** `aprovado_por` precisa ser diferente de `proposto_por`.
  `confirmado_por` pode ser igual a `proposto_por`.
- Ferramentas novas: `confirmar_operacao`, `cancelar_operacao`,
  `listar_operacoes` (as do próprio usuário; super_admin vê todas),
  `aprovar_operacao`, `rejeitar_operacao`. Todas passam pelo RBAC da spec 020.
- Transição de estado vira evento de auditoria com `operacao_id`.
- Regras (`rules.yaml`) ganham `aprovador: <papel>` no efeito
  `require_approval`.
- Skills somente leitura (`confirmacao: nenhuma`) executam direto.

## Painel

Página **Aprovações** (React): lista pendentes, mostra o resumo e os fatos
derivados, botões aprovar e rejeitar com motivo. Reaproveita `Card` e
`DecisionBadge`.

## Testes previstos

- Skill com confirmação não executa antes de `confirmar_operacao`.
- Alterar argumento entre proposta e confirmação invalida a operação.
- `require_approval` bloqueia até aprovação por papel correto; o próprio
  proponente não aprova.
- Operação expirada não executa.
- Confirmar duas vezes executa uma vez (idempotência).
- Cada transição gera evento de auditoria.
- Rotas do painel exigem o papel apropriado.

## Fora de escopo

Notificação ativa ao aprovador (e-mail/WhatsApp), aprovação em múltiplos
níveis, delegação.

## Perguntas em aberto

1. Quem é aprovador padrão na clínica: responsável técnica, gestor
   administrativo ou ambos? Recomendação: papel `gestor` configurável.

## Red Team / Blue Team / Postmortem

| Risco (Red Team) | Controle (Blue Team) |
|---|---|
| Trocar o valor entre a proposta e a confirmação | `digest_argumentos` conferido na confirmação |
| O mesmo usuário propõe e aprova | Segregação: aprovador diferente do proponente |
| Confirmar uma operação antiga esquecida | Expiração e listagem de pendentes |
| Resumo de confirmação escrito pelo modelo omitir um detalhe | O `resumo` é gerado pelo motor a partir dos argumentos |
| Confirmação "automática" pelo modelo sem o usuário ler | A confirmação é uma ferramenta própria; regra pode exigir que o confirmador seja humano identificado (stdio com usuário ou token) |

**Postmortem antecipado.** Fadiga de confirmação: se tudo pede confirmação,
as pessoas confirmam sem ler. Só skills com efeito exigem confirmação, e o
resumo destaca o que mudou ou é atípico (valor fora do padrão, retroativo).

## God Mode / L-99

**God Mode.** Cada skill declara `autonomia: A0|A1|A2|A3` (spec 019).
`confirmacao: nenhuma` equivale a A0; A1 executa direto dentro de limites
configurados (por exemplo, valor até um teto e data até N dias atrás) e
devolve resumo com a instrução de como estornar; fora do limite a skill
sobe para A2 automaticamente. A3 sempre exige aprovação de outro perfil.
Nenhuma skill irreversível pode ser A0 ou A1 (validado no `config_loader`).

**L-99.**

- A máquina de estados é formal: transições válidas listadas e testadas com
  teste de propriedade (nenhuma sequência chega a `executada` sem passar
  pelos estados exigidos).
- Recuperação de queda: operação `confirmada` sem resultado após reinício é
  retomada por idempotência, nunca duplicada.
- Orçamento: confirmar e executar em menos de 500 ms para handlers locais.
- Runbook: operações órfãs, aprovador indisponível, expiração em massa.
