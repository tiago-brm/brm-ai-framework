# Runbook: promover uma capacidade privada para o catálogo

Use quando um segundo cliente precisar de uma capacidade que hoje é privada
(`clients/<id>/modules/<nome>/`). Antes disso ela permanece privada.

## Checklist de revisão

1. **Sem dado nem regra do cliente original.** Procure por nomes, domínios, CNPJ,
   textos e valores fixos do cliente em `skills.yaml`, `defaults/`, handlers e testes.
2. **Segredos parametrizados.** Todo segredo é lido por `${secret:nome}` ou
   `host.secrets`; nenhum valor literal. Liste em `segredos_requeridos`.
3. **Domínios declarados.** Liste em `dominios_requeridos` tudo o que o módulo chama.
4. **Sem termos setoriais no núcleo.** O teste de agnosticismo deve continuar verde.
5. **Testes contratuais.** Habilitar sem adotar não cria tool; `usar:` com
   sobrescrita proibida é recusado; handlers usam o namespace do módulo.
6. **Manifesto.** `module.yaml` com `compartilhavel: true`, `version` e `api_version`
   atuais.

## Passos

1. Mover a pasta com `mv`, sem apagar a original antes da revisão:
   `clients/<id>/modules/<nome>` para `mcp_engine/modules/<nome>/`.
2. Adicionar o módulo em `_KNOWN` (`mcp_engine/modules/__init__.py`) e criar o
   `__init__.py` com `MODULE`.
3. No cliente de origem, trocar `modulos_privados: [<nome>]` por `modules: [<nome>]`.
   As skills adotadas com `usar:` continuam iguais.
4. Rodar a suíte e o preflight. Para voltar atrás, mover a pasta de volta e restaurar
   `modulos_privados`.
5. No segundo cliente, habilitar o módulo, adotar as skills e conferir a allowlist.
