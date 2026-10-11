# Code Style & Conventions

> Reflete o que o código **já faz** (observado em 2026-10-11) e acrescenta, marcadas como *meta*, as poucas regras que ainda faltam. Mantenha o código novo parecido com o vizinho: consistência acima de novidade.

## 1. General Principles

- Código legível; funções focadas; sem abstração desnecessária.
- Reutilize componente/utilitário existente antes de criar outro.
- **Segurança por construção:** regra é dado, fato de segurança vem do motor, saída de LLM passa por rascunho. Ver [ARCHITECTURE.md](ARCHITECTURE.md) §8.
- Mudança pequena e focada; não toque arquivos sem relação com a tarefa.
- Resolva por *Protocol* e injeção por construtor, não por herança nem import de implementação concreta.

## 2. Naming

### Python
- **Módulos/funções/variáveis:** `snake_case`. **Classes:** `PascalCase`. **Constantes:** `UPPER_SNAKE` (`DEFAULT_ACTOR`, `SCHEMA_VERSION`).
- **Privados:** prefixo `_` (`_StrictModel`, `_render_url`).
- **Contratos:** substantivo + papel (`RuleProvider`, `AuditSink`, `VaultProvider`, `NoteSource`); implementações: `FileSystem*Provider`, `ObsidianVaultProvider`, `SQLUserStore`.
- **Exceções:** `...Error` com os dados do erro como atributos (`AuditChainError.line_number`, `ConfigLoadError.path`).
- **Tools MCP:** nome da tool = método do `BRMEngine`. Tools de plataforma e administração em português (`criar_usuario`, `listar_permissoes`); tools do vault com prefixo `vault_`; skills do cliente em `snake_case` português (`consultar_prontuario`).
- **Idioma:** identificadores técnicos em inglês; texto de produto (`message`, `reason`, `proximo_passo`), exemplos e comentários em português.

### TypeScript / React
- **Componentes:** `PascalCase`, um por arquivo, nome do arquivo = nome do componente (`DecisionBadge.tsx`).
- **Páginas:** `src/pages/<Nome>.tsx` (rótulos em português, caminhos em minúsculas: `/usuarios`).
- **Funções/variáveis:** `camelCase`; tipos/interfaces: `PascalCase`; constantes de módulo: `UPPER_SNAKE`.
- **Rotas do painel:** hash router (`#/simulador`).

### Arquivos, banco e API
- **Arquivos Python:** `snake_case.py`; testes `tests/test_<módulo>.py`.
- **Banco:** ver [DATABASE.md](DATABASE.md) §2. **YAML/JSON:** `snake_case`.
- **Rotas REST:** `/api/<recurso>` minúsculo e plural quando coleção; sub-recurso do simulador em português (`/api/simulador/...`). Detalhes em [API.md](API.md).

## 3. Folder Structure

- `mcp_engine/core/` núcleo e segurança; `mcp_engine/vault/` conhecimento; `audit/` trilha; `clients/<cliente>/` dado do cliente; `dashboard/` painel; `scripts/` demos e utilitários; `tests/` espelha os módulos.
- Cliente novo **não** muda código: só cria `clients/<cliente>/config/...`.
- Não crie camada nova (service/repository/…) sem necessidade: o padrão aqui é `Engine` + `Protocol` + adapters.

## 4. Imports

- **Python:** `from __future__ import annotations` no topo; ordem automática do `ruff` (regra `I`): stdlib → terceiros → pacote local; imports absolutos (`from mcp_engine.core.models import ...`). Imports só de tipo em `if TYPE_CHECKING:`.
- **TypeScript:** alias `@/` para `src/` (ex.: `@/components/Card`); `import type` para tipos (`verbatimModuleSyntax` ligado).
- Import tardio é aceito para dependência pesada (agno/anthropic no agente; `sentence-transformers` no embedding) — mantenha o custo fora do caminho de subida.

## 5. Components (React)

- Função + hooks; props tipadas inline; um componente = uma responsabilidade.
- Reutilize `Card`, `Empty`, `PageHeader`, `DecisionBadge`/`EffectBadge` antes de criar markup novo.
- Todo acesso à API passa por `src/api.ts` (um único `request<T>`, erros via `ApiError`). Não chame `fetch` em páginas.
- Estado de página: carregando → vazio → erro → sucesso ([DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) §12).
- *Meta:* nenhum componente depende de arquivo ignorado pelo git (ver [TESTING.md](TESTING.md) sobre `src/lib/`).

## 6. Functions

- Faça uma coisa lógica, com nome claro e pouca complexidade.
- Anotações de tipo em toda assinatura pública; retorno de tool = `dict[str, Any]` com `allowed` quando a ação pode ser negada.
- Modelos de dados são `pydantic.BaseModel` herdando `_StrictModel` (imutável, sem campo extra); contratos são `Protocol` `@runtime_checkable`.
- I/O síncrono no motor (SQLAlchemy sync, arquivos). Só introduza `async` na borda FastAPI/MCP, onde o framework exige.

## 7. Error Handling

- **Use:** exceção própria com contexto para falha de configuração/integração (`ConfigLoadError`, `NotionConfigError`, `VaultRootNotFoundError`, `AuditChainError`); falhe **rápido** na construção do engine.
- **Tools de negócio não levantam para o usuário:** devolvem `{"allowed": False, "reason": "...", "proximo_passo": "..."}` e **auditam** a negativa.
- **Fail-closed:** erro ao avaliar regra não vira `allow`.
- **Não:** engolir exceção sem comentário e sem justificativa (os poucos `except Exception: pass` existentes — aquecimento do índice e início do watcher — são best-effort; ao escrever um novo, diga no código por que ignorar é seguro); expor stack trace ao modelo ou ao usuário; usar mensagem de erro para vazar dado sensível.
- REST: `HTTPException` com `detail` em português (`400` ação negada, `403` perfil, `404` recurso/simulador desligado).

## 8. Comments

Explique **por quê**, trade-offs e comportamento não óbvio — o repositório já faz isso bem. Referencie decisões como `D-NNN` ([ARCHITECTURE.md](ARCHITECTURE.md) §9). Não comente o que o código diz. Remova comentário que ficou falso quando mudar o código.

## 9. Logging

- Não há biblioteca de logging de aplicação; a **auditoria** (`AuditSink`) é o registro de negócio e segurança.
- Nunca grave senha, token, chave de API, dado pessoal sensível ou conteúdo de nota (regras em [SECURITY.md](SECURITY.md) §7).
- *Meta:* se for necessário log operacional, use `logging` da stdlib com `logger = logging.getLogger(__name__)`, sem `print` fora de `scripts/`.

## 10. Formatting

- **Python:** `ruff` (`uv run ruff check .`), regras `E`, `F`, `I`, linha **100**, alvo `py311`. Não há `ruff format` nem type checker configurados — *meta:* adotar `ruff format` e `pyright`/`mypy` (hoje as anotações não são verificadas).
- **TypeScript:** `oxlint` (`npm run lint`) com `react/rules-of-hooks` como erro; `tsc -b` no `npm run build` (flags estritas: `noUnusedLocals`, `noUnusedParameters`, `erasableSyntaxOnly`). Sem Prettier configurado: siga o estilo do arquivo (aspas duplas, ponto e vírgula, 2 espaços).
- **Tailwind 4:** classes utilitárias inline; cores só pelos tokens ([DESIGN_SYSTEM.md](DESIGN_SYSTEM.md)).

## 11. Do

- Reuse padrões existentes; mantenha mudanças focadas.
- Siga as convenções do projeto; remova código morto.
- Adicione teste junto da mudança (`tests/test_<módulo>.py`) e atualize a documentação afetada.
- Injete dependências no construtor para poder testar com fakes.
- Registre decisão nova em [ARCHITECTURE.md](ARCHITECTURE.md) §9.

## 12. Don't

- Criar utilitário duplicado ou introduzir dependência sem necessidade.
- Ignorar a arquitetura: falar direto com arquivo/banco/API de vault fora de um adapter.
- Usar `eval`/`exec`, condição em string livre, ou aceitar fato de segurança vindo do modelo.
- Escrever saída de LLM direto em `rules.yaml`/`skills.yaml`/vault sem passar por `DraftStore`.
- Mexer em `audit.jsonl` à mão, ou comitar `users.db`, `data/`, `.env`, token ou chave.
- Alterar arquivos sem relação com a tarefa; reformatar em massa.
