# 004 — Config Loader & Schema Versioning (spec)

**Ordem da spec (docs/spec.md §2.1, item 5):** "Versione o schema das configs
desde a v1. Coloque `schema_version: 1` no `rules.yaml` e `skills.yaml`.
Quando o Rule Builder visual existir na Fase 2, ele precisa gerar exatamente
esse schema — versionar agora evita migração dolorosa de configs manuais
depois." Os models (`RuleSet`, `SkillSet`) já validam `schema_version:
Literal[1]` desde o passo 001; este passo entrega a primeira implementação
concreta de `RuleProvider`/`SkillProvider` que lê esses YAMLs do disco.

## Escopo deste passo

Implementar `mcp_engine/core/config_loader.py` com dois adapters que
satisfazem os `Protocol`s definidos em `contracts.py`:

- `FileSystemRuleProvider(config_root: Path)` — `get_rules(client_id) -> RuleSet`
- `FileSystemSkillProvider(config_root: Path)` — `get_skills(client_id) -> SkillSet`

Layout de disco esperado (já descrito em spec.md §4):

```text
<config_root>/<client_id>/config/rules.yaml
<config_root>/<client_id>/config/skills.yaml
```

Dependência nova: `pyyaml` (`yaml.safe_load` — nunca `yaml.load` sem
`Loader`, que permite desserializar objetos Python arbitrários).

### `ConfigLoadError`

```python
class ConfigLoadError(Exception):
    def __init__(self, client_id: str, path: Path, cause: Exception) -> None:
        super().__init__(f"failed to load config for {client_id!r} from {path}: {cause}")
        self.client_id = client_id
        self.path = path
        self.__cause__ = cause
```

Envolve qualquer falha no caminho de carga — arquivo ausente, YAML
malformado, `schema_version` incompatível, campo desconhecido
(`extra="forbid"`), `client_id` do arquivo divergente do solicitado — num
único tipo de exceção, sempre com `path` e `client_id` no contexto (não só
"ValidationError: 1 validation error", que não diz qual cliente/arquivo).

### Comportamento de `get_rules`/`get_skills`

1. Monta o caminho `config_root / client_id / "config" / "rules.yaml"` (ou
   `skills.yaml`).
2. Lê e faz `yaml.safe_load`. Arquivo ausente ou YAML inválido →
   `ConfigLoadError`.
3. `RuleSet.model_validate(data)` / `SkillSet.model_validate(data)`.
   `ValidationError` do Pydantic (schema_version errado, campo extra,
   `client_id` ausente, `allowed_domains` vazia, etc.) → `ConfigLoadError`.
4. Compara o `client_id` do model já validado (não do dict cru — o passo 3
   garante que o campo existe) com o `client_id` recebido como argumento;
   divergência → `ConfigLoadError`. Protege contra copiar/colar o
   `rules.yaml` de um cliente para a pasta de outro sem atualizar o campo.
5. Sem cache: cada chamada relê o arquivo do disco (ver Decisões).

## Decisões

- **Sem cache em memória entre chamadas.** Fase 1 já assume "restart do
  engine para aplicar mudança de config" (spec.md §2, item 6 do roadmap não
  promete hot-reload). Cachear agora é otimização prematura que esconde bug
  real: se alguém editar `rules.yaml` e reiniciar o processo, quer ver a
  mudança refletida imediatamente, sem se perguntar se ficou um cache stale
  no meio. Reavaliar apenas se profiling real mostrar I/O de disco como
  gargalo — não aconteceu ainda com 1 cliente.

- **Valida `client_id` do arquivo contra o argumento, não confia só no
  path.** Um `rules.yaml` é editado à mão; é fácil copiar o arquivo de
  `clients/acme/` para `clients/beta/` sem trocar a linha `client_id: acme`
  lá dentro. Sem essa checagem, `beta` rodaria silenciosamente com as regras
  do `acme`. Falha alta no load é mais barato que descobrir em produção.

- **`ConfigLoadError` único, não uma exceção por tipo de falha.** Quem chama
  `get_rules()` (o `mcp_server.py`, ainda não implementado) só precisa saber
  "não consegui carregar config deste cliente, aborte o boot" — não importa
  se foi arquivo ausente ou schema inválido. A causa original fica em
  `__cause__` (mesmo padrão do `RuleEvaluationError` do passo 002) para quem
  quiser logar o detalhe.

- **`yaml.safe_load`, nunca `yaml.load` sem `Loader` explícito.** PyYAML sem
  `safe_load` desserializa tags Python arbitrárias (`!!python/object:...`) —
  um `rules.yaml` malicioso ou corrompido vira execução de código, a mesma
  classe de vulnerabilidade que o passo 002 já vetou para regras (`eval` de
  string livre). `safe_load` restringe a tipos YAML nativos.

- **Adapters recebem `config_root: Path` no construtor, não hardcoded.**
  Mesmo racional do passo 001 (Protocol, não implementação direta): testes
  usam `tmp_path` do pytest como `config_root` — diretório real, mas
  temporário e isolado por teste, sem precisar mockar filesystem. Produção
  passa o path real (`clients/`) na hora de montar o `mcp_server.py`.

## Fora de escopo (fica para depois)

- Migração entre `schema_version` (v1 → v2 quando existir) — Fase 2, quando
  o Rule Builder visual gerar um schema novo e for preciso migrar configs
  manuais existentes.
- Hot-reload sem restart do processo — não pedido pela spec nesta fase;
  reavaliar quando um cliente pedir explicitamente (mesmo gatilho do
  Control Plane, spec.md linha 56).
- `rest_builder.py` (Skills REST criadas dinamicamente pelo LLM) — item
  citado em spec.md §4 mas fora do MVP; hoje `skills.yaml` é só lido, nunca
  escrito pelo engine.
- Configs em JSON além de YAML — YAGNI; spec.md só menciona YAML/JSON como
  alternativas equivalentes, mas o layout de arquivo (§4) já fixa
  `rules.yaml`/`skills.yaml`. Adicionar JSON é uma linha (`yaml.safe_load`
  aceita JSON válido como subset de YAML) se algum cliente pedir.

## Critério de pronto

- `uv add pyyaml` limpo, sem conflito de dependências.
- `FileSystemRuleProvider`/`FileSystemSkillProvider` implementados,
  satisfazendo `isinstance(..., RuleProvider)` / `isinstance(..., SkillProvider)`.
- `tests/test_config_loader.py` com casos: load válido de rules.yaml e
  skills.yaml; arquivo ausente; YAML malformado; `schema_version` errado;
  campo extra; `client_id` do arquivo divergente do solicitado; sem cache
  (edita arquivo entre duas chamadas, segunda chamada reflete mudança).
- `clients/example/config/rules.yaml` e `skills.yaml` de exemplo,
  versionados no repo, servindo de referência/fixture.
- `uv run ruff check .` limpo.
- `uv run pytest -q` com novos testes passando.
