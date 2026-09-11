# 012 — Perfil super_admin e regras condicionadas por perfil (spec)

**Ordem da spec:** depois da 011 (persistência de usuários). Pedido do
usuário: um "super user (BRM)" com acesso irrestrito, capaz de criar outros
usuários e gerenciar perfis/permissões — hoje o RBAC só tem `admin`/`viewer`,
e nenhum dos dois pode trocar o perfil de alguém já cadastrado.

## Decisão de escopo: por cliente, não cross-client

Cogitado inicialmente como um "control plane" leve — um registro global de
staff da BRM operando em qualquer cliente. Descartado: a arquitetura atual é
deliberadamente **sem Control Plane centralizado na Fase 1**
(`docs/spec.md` §Fase 1) — cada cliente tem seu próprio `SQLUserStore`
isolado (`clients/<cliente>/config/users.db`), sem tabela ou serviço
compartilhado entre clientes. Introduzir acesso cross-client agora seria
antecipar a Fase 2 sem o sinal de demanda que o próprio `spec.md` define
como gatilho. `super_admin` é só **mais um perfil dentro do `Role` enum e do
`SQLUserStore` de cada cliente** — acesso total *daquele* cliente, não de
todos.

## Componentes

### `mcp_engine/core/auth.py`

- `Role` ganha `SUPER_ADMIN = "super_admin"`.
- `is_tool_allowed(role, tool_name)`: `role is Role.SUPER_ADMIN` retorna
  `True` incondicionalmente, **antes** de olhar `ROLE_TOOLS`. Isso garante
  "acesso a tudo" de verdade: cobre tools restritas que ainda não ganharam
  entrada explícita no dict, sem precisar lembrar de adicionar
  `Role.SUPER_ADMIN` toda vez que uma tool nova for restringida no futuro.
- `ROLE_TOOLS[Role.SUPER_ADMIN]` também lista as tools hoje restritas (mais
  a nova `atualizar_perfil_usuario`) — redundante com o bypass acima, mas
  necessário pra essas tools aparecerem certo em `tools/list` via o mesmo
  código que monta esse catálogo.

### `mcp_engine/core/user_store.py`

- `UserNotFoundError` (novo) — trocar perfil de e-mail inexistente não deve
  criar o usuário silenciosamente.
- `SQLUserStore.update_role(email, role) -> UserProfile` — troca o perfil de
  um usuário já cadastrado; levanta `UserNotFoundError` se não existir.
- `seed_demo_users` ganha um terceiro usuário: `brm@brm.com.br` /
  `super-admin-token-demo` / `Role.SUPER_ADMIN`.

### `mcp_engine/core/mcp_server.py`

- `BRMEngine._forbidden_super_admin_assignment(role_enum)` — guarda
  compartilhada por `criar_usuario` e `atualizar_perfil_usuario`: só um ator
  que já é `super_admin` pode atribuir o perfil `super_admin` a alguém
  (criar ou promover). `admin`/`viewer` continuam livres de atribuir entre
  si, como já era.
- `criar_usuario` — mensagem de perfil inválido passa a mencionar
  `super_admin` como opção válida; ganha a checagem acima.
- `atualizar_perfil_usuario(email, role)` (novo) — delega pro
  `SQLUserStore.update_role`; tool nova, restrita a `super_admin` (ver
  `ROLE_TOOLS`).
- `deletar_banco()` — a segunda checagem interna (`user.role is not
  Role.ADMIN`) virou `user.role not in (Role.ADMIN, Role.SUPER_ADMIN)`.
  Sem essa correção, um `super_admin` seria bloqueado por essa checagem
  redundante mesmo depois de passar pelo gate principal do
  `RBACFastMCP.call_tool` — bug que só apareceria em uso real, não nos
  testes que só cobriam `admin`/`viewer` antes.

### Regras condicionadas por perfil do ator (`actor_role`)

Pedido explícito do usuário: regras de negócio (`rules.yaml`) precisam
"enxergar" o perfil de quem está chamando, não só os argumentos da skill.
`BRMEngine.call_skill` agora inclui `"actor_role": get_current_user().role.value`
nos `facts` passados para `rule_evaluator.evaluate()`, ao lado de
`skill_name`/`actor`/`origem_sistema` já existentes. Uma regra em
`rules.yaml` pode agora fazer, por exemplo:

```yaml
- id: viewer-nao-consulta-saldo
  condition: { "==": [{"var": "actor_role"}, "viewer"] }
  effect: deny
  message: perfil viewer não pode executar esta skill
```

Isso é aditivo — `rule_evaluator.evaluate()` não mudou, `actor_role` é só
mais uma chave no dict de facts que o JSONLogic já recebia. Regras
existentes que não referenciam `actor_role` continuam se comportando
exatamente igual.

## Fora de escopo (registrado para depois, não decidido agora)

- **Permissão por tool persistida e editável (tabela em vez do dict
  `ROLE_TOOLS` fixo no código).** Necessário para um painel onde o
  `super_admin` liga/desliga acesso a qualquer tool sem deploy — inclusive
  `call_skill`/`vault_*`/`consultar_processo_datajud`, hoje livres pra
  qualquer perfil. Não implementado nesta spec.
- **Governança de promoção de draft de regra/skill por perfil**
  (`vault_promote_draft` hoje não checa perfil nenhum).
- **Dashboard (React/Vite) para o `super_admin`** gerenciar usuários,
  perfis, tools e visualizar regras/vault — desenhado como próximo passo,
  depende da tabela de permissões acima existir primeiro (o dashboard
  precisa de algo pra ler/escrever, não só do `Role` enum em memória).
- Hash de token, edição/desativação de usuário além de troca de perfil —
  já fora de escopo desde a spec 011.

## Critério de pronto

- `Role.SUPER_ADMIN` com acesso irrestrito, inclusive a tools futuras não
  listadas em `ROLE_TOOLS`.
- `atualizar_perfil_usuario` funcionando, restrita a `super_admin`.
- Ninguém além de `super_admin` consegue criar ou promover outro
  `super_admin`.
- `actor_role` disponível em `rules.yaml` via JSONLogic.
- `uv run ruff check .` e `uv run pytest -q` verdes.
