# 027 — Cliente agnóstico: módulos e template de cliente (spec)

**Status:** implementada (2026-10-09). Ver `docs/changelog/027-cliente-agnostico-modulos.md`.

**Origem:** a clínica é o piloto, mas o harness deve servir a qualquer
cliente (a spec 004 já separa `clients/<id>`). Falta um modo formal de
acoplar domínio (financeiro, agenda, fiscal) sem poluir o núcleo.

## Módulos

```
mcp_engine/modules/<nome>/
  module.py          # Module: name, handlers, migrations, defaults
  handlers.py        # handlers nativos registrados no executor (spec 021)
  migrations/        # SQL numerado
  defaults/          # skills.yaml, rules.yaml e vault_access.yaml sugeridos
```

- `client_config.yaml: modules: [financeiro, agenda]` habilita por cliente.
- O núcleo nunca importa um módulo diretamente; carrega pelo registro.
- Um cliente sem módulos continua funcionando (só governa skills mock/HTTP).
- Os `defaults` são ponto de partida copiado para o cliente, não herança em
  tempo de execução: o cliente sempre vê seu próprio `skills.yaml`.

## Template e gerador

- `clients/_template/` com `config/`, `vault/` e README.
- `scripts/novo_cliente.py <id> --modulos financeiro,agenda` cria a pasta,
  copia os defaults, valida com o `config_loader` e inicializa o banco.
- Regras setoriais (CFP, jurídico) ficam em `clients/<id>` ou em pacotes
  opcionais `packs/<setor>/`, nunca no núcleo.

## Separação a garantir

| Nível | Conteúdo |
|---|---|
| Núcleo (`mcp_engine/core`) | identidade, RBAC, regras, DLP, auditoria, executor, operações |
| Módulo | tabelas, handlers e defaults de um domínio |
| Cliente | configuração, vault, dados, regras setoriais |

Teste de agnosticismo: uma busca por "clínica", "paciente" e "psicólogo" em
`mcp_engine/core` não pode retornar nada.

## Limpeza associada

- Atualizar o `README.md` (hoje diz "Ainda não implementado").
- Registrar no changelog a remoção de referências setoriais do núcleo.
- Worktree antigo em `.claude/worktrees/` fica fora do escopo; sinalizar.

## Red Team / Blue Team / Postmortem

| Risco (Red Team) | Controle (Blue Team) |
|---|---|
| Módulo malicioso ou com bug acessa dados de outro | Cada módulo recebe só a sessão do próprio banco; registro explícito, sem import dinâmico de caminho arbitrário |
| Default de módulo ativa permissão aberta | Defaults seguem default deny; validação do loader recusa ferramenta sem permissão |
| Mistura de dados entre clientes | Um processo por cliente, `CLIENT_ID` obrigatório, diretórios de dados separados |

**Postmortem antecipado.** O "agnóstico" costuma virar o piloto com outro
nome. O teste de busca no núcleo e a criação de um segundo cliente de
exemplo (mínimo, só com `financeiro`) provam o desacoplamento.

## Testes previstos

- Cliente sem módulos inicia e governa skills.
- Cliente com `financeiro` registra handlers e migra o banco.
- `novo_cliente.py` gera uma pasta que passa no `config_loader`.
- Núcleo livre de termos setoriais.

## Fora de escopo

Control plane, marketplace de módulos, versionamento independente de módulos.

## God Mode / L-99

**God Mode.** `scripts/mapa_do_sistema.py` gera, por cliente, o manifesto
completo: módulos, skills com autonomia, regras, matriz de permissões e
zonas do vault. É a visão panorâmica de "o que este cliente pode fazer e
quem pode".

**L-99.** O contrato de módulo é versionado (`api_version`); o núcleo recusa
módulo com versão incompatível. Testes de contrato valem para qualquer
módulo novo. Runbook: criar um cliente novo do zero em menos de 15 minutos,
atualizar módulo com reversão.

## Capacidades por cliente: catálogo compartilhado e capacidades privadas

Decisão do usuário (2026-10-09): as capacidades (skills) são separadas por
cliente. Algumas são **compartilhadas** (por exemplo, a API do Resend para
e-mail, usada por vários clientes) e outras são **privadas** (a emissão de
NFS-e, por enquanto só da clínica).

### Duas origens de capacidade

| Origem | Onde mora | Quem enxerga |
|---|---|---|
| **Catálogo compartilhado** | `mcp_engine/modules/<nome>/` | Só os clientes que habilitam o módulo |
| **Privada do cliente** | `clients/<id>/modules/<nome>/` (mesmo contrato de módulo) | Somente aquele cliente |

Um módulo traz um manifesto `module.yaml` com: `name`, `version`,
`api_version`, `compartilhavel` (true ou false), lista de skills disponíveis,
`segredos_requeridos` e `dominios_requeridos`. Skills declarativas (HTTP)
podem vir em `skills.yaml` do próprio módulo, sem código Python; as que
precisam de lógica trazem handlers nativos.

### Habilitar e expor por cliente (opt-in duplo)

- `client_config.yaml`: `modules: [resend, datajud]` (catálogo) e
  `modulos_privados: [nfse]` (pasta do próprio cliente).
- Habilitar um módulo **não expõe** suas skills. Cada skill precisa ser
  adotada no `skills.yaml` do cliente:

```yaml
skills:
  - usar: resend.enviar_email
    autonomia: A2
    resumo: "E-mail para {destinatario}: {assunto}"
  - name: consultar_prontuario
    ...definição própria do cliente...
```

  `usar:` aceita sobrescrever `description`, `autonomia`, `aprovador`,
  `resumo`, `examples` e `executor.timeout_s`; não aceita ampliar
  `allowed_domains` além do que o cliente já permite.
- Referência a skill ou módulo inexistente falha na carga (config inválida),
  nunca em silêncio.

### Isolamento

- Um processo por cliente, como já é: um cliente nunca carrega o módulo
  privado de outro. Módulo privado só é carregado de
  `clients/<id>/modules/` do próprio cliente e só se listado em
  `modulos_privados`.
- **Segredos por cliente:** o `SecretStore` procura primeiro
  `BRM_SECRET_<CLIENTE>_<NOME>` (e `/run/secrets/<cliente>/<nome>`) e só
  depois o segredo global. A chave do Resend pode ser compartilhada ou
  própria de cada cliente por configuração, mas nunca vaza entre clientes por
  acidente.
- Na inicialização, o motor confere os `dominios_requeridos` do módulo contra
  a allowlist do cliente (falha fechada se faltar) e avisa de `segredos_
  requeridos` ausentes.
- Permissões (RBAC) e auditoria já são por cliente.

### Promoção de privada para compartilhada

Quando um segundo cliente precisar de uma capacidade privada, ela é movida
para o catálogo com revisão (checklist em `docs/runbooks/promover-capacidade.md`:
sem dado ou regra do cliente original, segredos parametrizados, testes
contratuais, `compartilhavel: true`). Antes disso ela permanece privada.

### Exemplos para o piloto

- `resend` (catálogo): e-mail transacional por HTTP, declarativo; substitui a
  exceção de `enviar_email` por SMTP no motor. Confirmar a API atual do
  Resend na documentação antes de fixar o contrato (regra L-99).
- `nfse` (privada de `clinica-psi`, Fase 2): emissão pela API do Emissor
  Nacional, atrás de `FiscalProvider`.
- `datajud` (catálogo): consulta pública do CNJ; habilitado só nos clientes
  que o usam.

### Testes adicionais

- Cliente A habilita `resend` e adota a skill; cliente B não habilita: B não
  tem a tool nem consegue adotá-la.
- Módulo privado de A não carrega em B, mesmo que B o liste.
- Habilitar sem adotar não cria tool.
- `usar:` com sobrescrita válida aplica; com ampliação de domínio é recusado.
- Segredo específico do cliente vence o global; um cliente não lê o do outro.
- Módulo com domínio requerido fora da allowlist falha na subida.
