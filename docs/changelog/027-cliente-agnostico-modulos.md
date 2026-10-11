# 027 — Cliente agnóstico: módulos, capacidades por cliente e template

**O quê:** implementa a spec `docs/specs/027-cliente-agnostico-modulos.md`.

- `mcp_engine/modules/__init__.py`: contrato de módulo (`BaseModule`, manifesto
  `module.yaml`, `api_version`), catálogo estático (`_KNOWN`), módulos privados em
  `clients/<id>/modules/` (campo `modulos_privados`), `ModuleHost` com handlers com
  namespace, banco próprio `data/<cliente>/<modulo>.sqlite` com migrações e
  `SecretStore`. O módulo não recebe state.db nem user_store. `api_version`
  incompatível, módulo desconhecido, nome com travessia, colisão com o catálogo e
  domínio requerido fora da allowlist falham na carga.
- Opt-in duplo: habilitar um módulo não cria tools. Cada skill é adotada em
  `skills.yaml` com `usar: modulo.skill`; só `description`, `autonomia`, `aprovador`,
  `resumo`, `examples` e `executor.timeout_s` podem ser sobrescritos. Referência
  inexistente ou módulo não habilitado falha na carga.
- `SecretStore`: prioridade `BRM_SECRET_<CLIENTE>_<NOME>` e `<dir>/<cliente>/<nome>`
  antes do global; nome de segredo validado. A subida avisa de segredos requeridos
  ausentes.
- `SkillExecutor.corpo_fixo`: campos de corpo HTTP que o chamador não sobrescreve.
- Módulo `datajud`: `consultar_processo_datajud` saiu do núcleo e só existe nos clientes
  que habilitam o módulo (`clients/example` habilita).
- Módulo `resend`: skill `enviar_email` declarativa (POST `https://api.resend.com/emails`,
  Bearer, remetente por segredo), irreversível e A2.
- Núcleo agnóstico: `vault_access.yaml` ganhou `vinculo_campo`, `identificador_campo` e
  `write_denied_next_step` (padrões neutros); `clinica-psi` e `instituto-afw` os
  configuram. Termos de clínica, paciente e escritório saíram de `mcp_engine/core` e
  `mcp_engine/vault`, com teste de busca.
- `clients/_template/` e `scripts/novo_cliente.py <id> --modulos a,b`.
- `README.md` reescrito; `docs/runbooks/promover-capacidade.md`.

**Desvios e pendências**
- O `ModuleHost` expõe também `add_vault_note`, `audit` e `register_tool`, porque o
  `datajud` precisa gravar nota e registrar tool.
- `save_skills` (promoção de draft) grava as skills já resolvidas, perdendo o `usar:`.
- Os prompts do gerador de documentos e do interpretador seguem com texto jurídico.
- Worktree antigo em `.claude/worktrees/` não foi tocado. `scripts/mapa_do_sistema.py`
  (God Mode) e o módulo `nfse` (Fase 2) ficam para depois.
