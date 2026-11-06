# Matriz de permissões da clínica (o que exige autorização e o que não)

Para rever depois. Cobre o cliente `clinica-piloto`. Atualizado em 2026-11-05.

## Como ler

O chat do Claude usa **uma conta única de canal, com papel `viewer`**. As pessoas (secretaria,
gestor) só se identificam na **página da Central de Ações**, pelo link, e confirmam com **PIN**.
Por isso, tudo que o chat faz por conta própria vale como `viewer`.

| Nível | O que significa |
|---|---|
| **Livre** | O chat executa direto, sem aprovação e sem identificar a pessoa. |
| **Confirmação (A2)** | O chat propõe e quem propôs confirma no chat. Sem PIN e sem identidade pessoal. |
| **Aprovação (A3)** | Outro perfil (administrador) precisa aprovar. |
| **Central + PIN** | Link, identificação da pessoa e PIN. Algumas exigem papel de administrador. |
| **Só admin** | Negado a quem não é administrador ou super_admin (regra dura ou trava no código). |

## 1. Consultas e leituras (livres para o chat)

| Skill | Nível | O que a recepção recebe |
|---|---|---|
| `consultar_financeiro` | Livre, com lista de permitidos | Só `a_receber`, `saldo_pacote`, `faltas_por_periodo`, `pendencias`, `sem_nf`, `ultimos_lancamentos`. Todo o resto é negado (regra `faturamento-so-admin`). |
| `listar_pendencias` | Livre | Pendências sem nome de paciente. |
| `agenda_sessoes` | Livre | Só totais por dia, serviço e profissional. Nomes e valores só para o gestor. |
| `agenda_clientes` | Livre | Só a contagem. Nomes só para o gestor. |
| `agenda_servicos` | Livre | Lista de serviços e preços. |
| `agenda_contratos_pendentes` | Livre | Só a contagem. Nomes só para o gestor. |
| `agenda_sessoes_sem_pagamento` | Livre | Quantidade e totais por profissional. O valor previsto só para o gestor. |
| `sincronizar_agenda` | Livre (A1) | Roda sem aprovação e escreve no espelho local (pacientes, sessões, pendências). |
| `documento_modelos`, `tipos_de_acao`, `status_da_acao`, `listar_operacoes` | Livre | Consulta de estado. |
| `vault_search`, `vault_list_notes`, `vault_get_related` | Livre, filtrado por nota | Só notas que a política de acesso do vault libera. Notas financeiras só para o administrador. |

## 2. Escritas que o chat propõe

| Skill | Nível | Observação |
|---|---|---|
| `registrar_despesa`, `anexar_comprovante`, `atualizar_status_nf`, `registrar_falta`, `registrar_pacote`, `resolver_pendencia` | Confirmação (A2) | A confirmação é no chat, sem PIN. Despesa retroativa e `resolver_pendencia` têm regra que exige o gestor. |
| `reabrir_mes` | Confirmação (A2) | A regra `reabrir-mes-so-super-admin` nega a todos, menos o super_admin. |
| `estornar_lancamento` | Aprovação (A3) | Outro perfil (admin) aprova. |
| `fechar_mes` | Aprovação (A3) | Outro perfil (admin) aprova. |
| `memoria_registrar` | Livre | Cria rascunho de memória. Só passa a valer quando o gestor promove. |

## 3. Central de Ações (todas pedem link + PIN)

| Ação | Quem pode abrir | Observação |
|---|---|---|
| `agenda.atualizar_status_sessao` | Qualquer perfil | Cancelar exige liberação do gestor e motivo. |
| `agenda.emitir_contrato_sessao` | Qualquer perfil | Modelo precisa estar aprovado pelo gestor. |
| `agenda.registrar_pagamento` | Qualquer perfil | Baixa na hora se o valor bate com o saldo; senão fica aguardando a gerência. |
| `agenda.emitir_recibo_pagamento` | Qualquer perfil | PDF do recibo a partir do modelo aprovado. CPF mascarado. |
| `nucleo.executar_skill` | Qualquer perfil | Executa uma skill A2 ou A3 aprovada na página. |
| `agenda.confirmar_pagamento` | **Só admin** | Dá baixa em pagamento que aguardava conferência. |
| `agenda.definir_crp_profissional` | **Só admin** | Grava o CRP no cadastro do profissional. |
| `agenda.definir_corte_bonus_indicacao` | **Só admin** | Data de corte do bônus de indicação. |
| `agenda.definir_aptidao_bonus_indicacao` | **Só admin** | Aptidão de cada profissional ao bônus. |
| `agenda.consultar_bonus_indicacao` | **Só admin** | Leitura protegida: o dado só aparece na página. |
| `financeiro.consulta_restrita` | **Só admin** | Leitura protegida: repasse, faturamento, resultado e relatórios aparecem só na página. |

## 4. Só administrador (negado ao chat)

- **Comissão e ganhos:** `repasse_por_profissional`, `simular_repasse`, `parte_clinica`,
  `taxas_por_meio` (regra `comissao-so-admin` e trava no código). Nem o próprio profissional vê o seu repasse.
- **Faturamento e relatórios:** `resumo_mensal`, `por_forma`, `por_conta`, `por_unidade`,
  `por_profissional`, `resultado_mensal`, `despesas_por_categoria`, `painel_financeiro`
  (regra `faturamento-so-admin`).
- **Bônus de indicação:** regra `bonus-indicacao-so-admin`.
- **Planilhas:** a skill `exportar_planilha` foi retirada da clínica. A regra `planilha-so-admin`
  continua como trava.
- **Núcleo (travas no código):** `query_audit_log`, `vault_interpret_and_propose`,
  `vault_generate_document`, `vault_promote_draft`, `vault_promote_document`,
  `documento_aprovar_modelo`, `memoria_promover`, `gerar_relatorio_mensal`, `vault_add_note`.
- **Administração de usuários:** `criar_usuario`, `listar_usuarios`, `atualizar_permissao`,
  `atualizar_perfil_usuario`, `rotacionar_token`, `deletar_banco`, `aprovar_operacao`,
  `rejeitar_operacao`.

## 5. Para decidir depois

1. **A2 sem PIN:** o chat pode confirmar uma escrita (despesa, falta, pacote) em nome do usuário,
   sem identificar a pessoa. A alternativa é mover essas escritas para a Central, como já foi feito
   com pagamento e contrato.
2. **`sem_nf` e `ultimos_lancamentos` na lista da recepção:** trazem valores. Para fechar mais,
   tire os dois da regra `faturamento-so-admin` em `rules.yaml`.
3. **`sincronizar_agenda` (A1):** roda sem aprovação e escreve no espelho local.
4. **Gatilhos (automações):** não foram revisados. Quem recebe qualquer relatório automático
   com dado financeiro também deve ser administrador.
5. **Dado no momento de abrir o link:** os números da leitura protegida aparecem na página assim
   que o administrador se identifica; o PIN vem na confirmação. Exigir o PIN antes de mostrar os
   dados é uma mudança no núcleo da Central.

## Onde está cada controle

| Controle | Arquivo |
|---|---|
| Regras duras | `clients/clinica-piloto/config/rules.yaml` |
| Skills habilitadas e autonomia | `clients/clinica-piloto/config/skills.yaml` |
| Leitura de notas do vault | `clients/clinica-piloto/config/vault_access.yaml` |
| Política em texto (restrita ao admin) | `clients/clinica-piloto/vault/politicas/confidencialidade-financeira.md` |
| Fábrica de leitura protegida | `mcp_engine/core/leituras.py` |
| Travas de papel no núcleo | `mcp_engine/core/mcp_server.py` (`_exigir_gestor`) |
