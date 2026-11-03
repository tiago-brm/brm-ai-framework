from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from json_logic import jsonLogic
from pydantic import BaseModel, ConfigDict, Field

from audit.sink import FileSystemAuditSink
from mcp_engine.core.acoes import (
    AcaoRecusada,
    CentralAcoes,
    Preparo,
    Previa,
    TipoAcao,
)
from mcp_engine.core.atributos_usuario import AtributosUsuarioStore
from mcp_engine.core.auth import (
    ANONYMOUS_PROFILE,
    Role,
    UserProfile,
    current_user_var,
    ensure_trace_id,
    get_current_user,
    get_trace_id,
    resolve_stdio_identity,
    set_current_user,
    trace_id_var,
)
from mcp_engine.core.config_loader import (
    FileSystemClientConfigProvider,
    FileSystemDemoScenariosProvider,
    FileSystemRuleProvider,
    FileSystemSkillProvider,
    FileSystemVaultAccessProvider,
)
from mcp_engine.core.config_notas import OPERACAO_ATIVAR, Extensoes
from mcp_engine.core.config_notas_servico import Caderno
from mcp_engine.core.documentos import DocumentoErro, DocumentoStore, ServicoDocumentos
from mcp_engine.core.email_sender import EmailSendError, email_configured, send_email
from mcp_engine.core.executor import (
    EVENTOS_CHAVE,
    ExecucaoFalhou,
    HandlerContext,
    HandlerRegistry,
    SecretStore,
    emitir_chamada,
    run_http,
    run_native,
)
from mcp_engine.core.fatos import ContextoFatos, FatosIndisponiveis
from mcp_engine.core.gatilhos import Gatilhos
from mcp_engine.core.identidade import construir_resolvedor, get_identidade
from mcp_engine.core.interceptor import (
    intercept,
    redact_for_role,
    scan_request,
    scan_response,
)
from mcp_engine.core.models import (
    AuditDecision,
    AuditEvent,
    RuleEffect,
    RuleSet,
    SkillCall,
    SkillDefinition,
)
from mcp_engine.core.operacoes import (
    AGUARDANDO,
    CANCELADA,
    CONFIRMADA,
    EXECUTADA,
    EXPIRADA,
    FALHOU,
    PROPOSTA,
    RANK,
    REJEITADA,
    Operacao,
    OperacaoStore,
    digest_argumentos,
)
from mcp_engine.core.rbac_server import RBACFastMCP
from mcp_engine.core.rule_evaluator import evaluate
from mcp_engine.core.state import StateStore
from mcp_engine.core.user_store import SQLUserStore, UserAlreadyExistsError, UserNotFoundError
from mcp_engine.modules import (
    ModuleError,
    install_modules,
    load_for_client,
    segredos_ausentes,
)
from mcp_engine.vault.access import filter_notes
from mcp_engine.vault.document_generator import (
    build_document_agent,
    promote_document,
    propose_document,
)
from mcp_engine.vault.draft_store import DraftStore
from mcp_engine.vault.escrita_motor import EscritorMotor
from mcp_engine.vault.interpreter import (
    build_interpreter_agent,
    promote_draft,
    propose_draft,
)
from mcp_engine.vault.notion_adapter import NotionVaultProvider
from mcp_engine.vault.obsidian_adapter import ObsidianVaultProvider
from mcp_engine.vault.segundo_cerebro import SegundoCerebro

logger = logging.getLogger(__name__)

DEFAULT_ACTOR = "claude"

TOOLS_DO_NUCLEO = frozenset(
    {
        "call_skill", "query_audit_log", "vault_search", "vault_add_note", "vault_get_related",
        "vault_list_notes", "vault_interpret_and_propose", "vault_list_drafts",
        "vault_promote_draft", "vault_generate_document", "vault_promote_document",
        "consultar_dados", "deletar_banco", "criar_usuario", "listar_usuarios",
        "atualizar_perfil_usuario", "listar_permissoes", "confirmar_operacao",
        "aprovar_operacao", "rejeitar_operacao", "cancelar_operacao", "listar_operacoes",
        "rotacionar_token", "atualizar_permissao", "config_estado", "config_propor",
        "config_reverter", "config_rascunho", "memoria_registrar", "memoria_rascunhos",
        "memoria_promover", "gerar_relatorio_mensal", "criar_acao", "criar_lote_acoes",
        "status_da_acao", "tipos_de_acao", "documento_modelos", "documento_aprovar_modelo",
    }
)  # fmt: skip


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _render_url(url_template: str, arguments: dict[str, Any]) -> str:
    try:
        return url_template.format(**arguments)
    except (KeyError, IndexError):
        return url_template


_PY_TYPES: dict[str, Any] = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _skill_tool_description(skill: SkillDefinition) -> str:
    parts = [skill.description.strip()]
    if skill.examples:
        parts.append("Exemplos de pedido: " + " | ".join(skill.examples))
    parts.append(
        "A ação passa por regras do cliente e pode ser negada ou ficar aguardando "
        "aprovação humana; nesse caso explique o motivo e o proximo_passo ao usuário."
    )
    return "\n".join(parts)


def _make_skill_tool(engine: BRMEngine, skill: SkillDefinition) -> Any:
    params: list[inspect.Parameter] = []
    for pname, spec in skill.parameters.items():
        if pname in skill.derived_facts:
            continue
        spec = spec if isinstance(spec, dict) else {}
        py_type = _PY_TYPES.get(spec.get("type", "string"), str)
        required = bool(spec.get("required"))
        annotation: Any = py_type if required else py_type | None
        if spec.get("description"):
            annotation = Annotated[annotation, Field(description=spec["description"])]
        params.append(
            inspect.Parameter(
                pname,
                inspect.Parameter.KEYWORD_ONLY,
                default=inspect.Parameter.empty if required else None,
                annotation=annotation,
            )
        )

    def tool(**kwargs: Any) -> dict[str, Any]:
        arguments = {k: v for k, v in kwargs.items() if v is not None}
        return engine.call_skill(skill.name, arguments)

    tool.__signature__ = inspect.Signature(params, return_annotation=dict[str, Any])  # type: ignore[attr-defined]
    tool.__name__ = skill.name.replace("-", "_")
    return tool


class _ArgsSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill: str = Field(min_length=1, max_length=64)
    argumentos: dict[str, Any] = Field(default_factory=dict)


def _tipo_acao_skill(engine: BRMEngine) -> TipoAcao:
    """Tipo nativo `nucleo.executar_skill`: qualquer skill do cliente confirmada pela página.

    Na criação, as regras são avaliadas com quem pediu: negar não gera link; exigir aprovação,
    autonomia A3 ou skill irreversível tornam a ação sensível (liberação de gestor). Na execução,
    a skill roda com a identidade de quem confirmou, pelo mesmo pipeline do motor."""

    def _skill(args: dict[str, Any]) -> SkillDefinition:
        skill = engine._find_skill(args["skill"])
        if skill is None:
            raise AcaoRecusada(f"skill desconhecida: {args['skill']}")
        return skill

    def preparar(ctx: Any, args: dict[str, Any]) -> Preparo:
        skill = _skill(args)
        veredito = engine.avaliar_skill(skill.name, args["argumentos"])
        if veredito["efeito"] == "deny":
            raise AcaoRecusada(veredito["message"] or "As regras do cliente negam esta ação.")
        sensivel = skill.autonomia == "A3" or skill.irreversivel
        papel = skill.aprovador
        meta: dict[str, Any] = {}
        if veredito["efeito"] == "require_approval":
            sensivel = True
            meta["regra_id"] = veredito["rule_id"]
            regra = next((r for r in engine.rules.rules if r.id == veredito["rule_id"]), None)
            papel = (regra.aprovador if regra else None) or skill.aprovador
        return Preparo(sensivel=sensivel, papel_liberacao=papel, meta=meta)

    def resumir(args: dict[str, Any]) -> str:
        skill = engine._find_skill(args["skill"])
        return engine._resumo(skill, args["argumentos"]) if skill else args["skill"]

    def carregar(ctx: Any, args: dict[str, Any]) -> Previa:
        return Previa(
            resumo=resumir(args),
            dados={"skill": args["skill"], "argumentos": args["argumentos"]},
        )

    def executar(ctx: Any, args: dict[str, Any]) -> dict[str, Any]:
        resultado = engine._call_skill(
            args["skill"],
            dict(args["argumentos"]),
            idempotency_key=f"acao:{ctx.acao_id}:{ctx.item}",
            _aprovada=True,
            _regra_aprovada=ctx.meta.get("regra_id") if ctx.liberado_por else None,
            _confirmado_por=ctx.ator,
            _aprovado_por=ctx.liberado_por,
        )
        if not resultado.get("allowed"):
            raise ExecucaoFalhou(str(resultado.get("reason") or "a ação foi negada"))
        return {
            "resumo": "Executada.",
            "allowed": True,
            "result": resultado.get("result"),
            "payload_digest": resultado.get("payload_digest"),
        }

    return TipoAcao(
        nome="nucleo.executar_skill",
        titulo="Executar ação do cliente",
        descricao="Executa uma skill do cliente depois da confirmação na Central de Ações.",
        esquema=_ArgsSkill,
        resumir=resumir,
        carregar=carregar,
        executar=executar,
        preparar=preparar,
    )


class BRMEngine:
    def __init__(
        self,
        client_id: str,
        config_root: Path,
        data_root: Path,
        vault_provider: Any | None = None,
        vault_watch: bool = False,
        interpreter_agent: Any | None = None,
        document_agent: Any | None = None,
        user_store: SQLUserStore | None = None,
        http_client: Any | None = None,
        secrets: SecretStore | None = None,
        skip_config_integrity: bool = False,
    ) -> None:
        self.client_id = client_id
        self._config_root = Path(config_root)
        self._data_root = Path(data_root)

        self._rule_provider = FileSystemRuleProvider(self._config_root)
        self._skill_provider = FileSystemSkillProvider(self._config_root)
        self._client_config_provider = FileSystemClientConfigProvider(self._config_root)
        self._audit_sink = FileSystemAuditSink(self._data_root)
        self._state = StateStore(self._data_root, client_id)
        self.extensoes = Extensoes()
        self.operacoes = OperacaoStore(self._state)
        self.gatilhos = Gatilhos(self)
        self.extensoes.registrar_tipo(self.gatilhos.tipo(), substituir=True)
        self.extensoes.provedores.registrar(
            "nucleo.operacoes",
            self._fatos_operacoes,
            ("operacoes_aguardando", "operacoes_aguardando_horas_max"),
        )
        self.handlers = HandlerRegistry()
        self.secrets = (secrets or SecretStore()).for_client(client_id)
        self._http_client = http_client

        # Eagerly load to catch config errors early. Also needed here (rather
        # than after vault_provider construction) since the vault provider
        # factory below reads client_config.vault_provider.
        self._ruleset = self._rule_provider.get_rules(client_id)
        self._client_config = self._client_config_provider.get_client_config(client_id)
        self._modules = load_for_client(
            client_id,
            self._config_root,
            self._client_config.modules,
            self._client_config.modulos_privados,
            self._client_config.domain_allowlist,
        )
        self._skill_provider.modules = self._modules
        self._skill_provider.domain_allowlist = self._client_config.domain_allowlist
        self._skillset = self._skill_provider.get_skills(client_id)
        self._vault_access = FileSystemVaultAccessProvider(self._config_root).get_vault_access(
            client_id
        )
        self._demo_scenarios = FileSystemDemoScenariosProvider(self._config_root)
        self._notes_snapshot: tuple[Any, ...] | None = None

        self._vault_provider = vault_provider or self._build_vault_provider()
        self._draft_store = DraftStore(self._config_root)
        self.user_store = user_store or SQLUserStore(self._config_root, client_id)
        cfg = self._client_config
        self.identidade = construir_resolvedor(
            cfg.identidade,
            self.user_store,
            self.secrets,
            domain_allowlist=cfg.domain_allowlist,
            auditar_recusa=self._auditar_recusa_identidade,
        )
        self.documentos = ServicoDocumentos(
            DocumentoStore(self._state, self._data_root, client_id),
            self._vault_root(),
            cfg.dados_do_emissor,
            self.auditar_evento,
        )
        self.extensoes.acoes.registrar(_tipo_acao_skill(self))
        self.acoes = CentralAcoes(
            client_id=client_id,
            config=cfg.central_acoes,
            state=self._state,
            atributos=AtributosUsuarioStore(
                self._state, cfg.central_acoes.pin_tentativas, cfg.central_acoes.pin_bloqueio_min
            ),
            registro=self.extensoes.acoes,
            auditar=self.auditar_evento,
            data_root=self._data_root,
            documentos=self.documentos,
            trace_id=get_trace_id,
            verificar_entrada=self._dlp_entrada_acao,
        )
        # Built lazily on first interpreter call: constructing agno's Agent is
        # cheap, but there's no reason to import agno/anthropic for engines
        # that never touch the interpreter.
        self._interpreter_agent = interpreter_agent
        self._document_agent = document_agent

        self.module_tools = install_modules(
            self._modules,
            client_id=client_id,
            data_root=self._data_root,
            registry=self.handlers,
            secrets=self.secrets,
            add_vault_note=self._module_add_note,
            audit=self._module_audit,
            extensoes=self.extensoes,
            leitor_operacoes=self.operacoes.contagens,
            leitor_documentos=self.documentos.emitidos,
        )
        self._iniciar_caderno(skip_config_integrity)
        self._validar_fatos_de_modulo()
        for ausente in segredos_ausentes(self._modules, self.secrets):
            logger.warning("módulo com segredo requerido ausente: %s", ausente)

        if vault_watch and hasattr(self._vault_provider, "start_watching"):
            try:
                if hasattr(self._vault_provider, "on_external_change"):
                    self._vault_provider.on_external_change = self._on_nota_externa
                self._vault_provider.start_watching(client_id)
            except Exception:
                pass

    def _vault_root(self) -> Path:
        if self._client_config.vault_root:
            return Path(self._client_config.vault_root).expanduser()
        return self._config_root / self.client_id / "vault"

    def _iniciar_caderno(self, skip_integrity: bool) -> None:
        vault_root = self._vault_root()
        self.escritor = EscritorMotor(self._state, vault_root, self._audit_sink, self.client_id)
        self.caderno = Caderno(self, self.extensoes, vault_root)
        self.segundo_cerebro = SegundoCerebro(
            client_id=self.client_id,
            vault_root=vault_root,
            drafts=self._draft_store,
            audit=self._audit_sink,
            client_config=self._client_config,
            extensoes=self.extensoes,
            escritor=self.escritor,
            reindex=lambda: self._vault_provider.reindex(self.client_id),
        )
        if self._client_config.config_via_notas:
            if not skip_integrity:
                self.caderno.verificar_integridade()
                self.gatilhos.reconciliar(self.caderno.verificar_snapshots())
            self.caderno.aplicar_no_motor()

    def _on_nota_externa(self, client_id: str, note_id: str) -> None:
        self.caderno.on_nota_alterada(note_id)

    def reserved_tool_names(self) -> frozenset[str]:
        return TOOLS_DO_NUCLEO | frozenset(self.module_tools)

    def _module_add_note(self, title: str, content: str, frontmatter: dict[str, Any]) -> str | None:
        return self._vault_provider.add_note(self.client_id, title, content, frontmatter).note_id

    def _module_audit(self, action: str, allowed: bool) -> None:
        decision = AuditDecision.ALLOWED if allowed else AuditDecision.DENIED
        self._record_audit(DEFAULT_ACTOR, action, decision)

    def auditar_evento(self, action: str, ok: bool, details: dict[str, Any]) -> None:
        self._audit_sink.record(
            AuditEvent(
                client_id=self.client_id,
                occurred_at=datetime.now(UTC),
                actor=self._actor(),
                action=action,
                decision=AuditDecision.ALLOWED if ok else AuditDecision.DENIED,
                details=details,
            )
        )

    def _auditar_recusa_identidade(self, provedor: str, motivo: str) -> None:
        self.auditar_evento(f"identidade:{provedor}:recusada", False, {"motivo": motivo})

    def _dlp_entrada_acao(self, argumentos: dict[str, Any]) -> str | None:
        scan = scan_request(argumentos, self._client_config, get_current_user().role)
        return None if scan.allowed and not scan.dlp_matches else (scan.reason or "DLP")

    def _build_vault_provider(self) -> Any:
        if self._client_config.vault_provider == "notion":
            return NotionVaultProvider(self._config_root, self._data_root, self._audit_sink)
        return ObsidianVaultProvider(self._config_root, self._data_root, self._audit_sink)

    @property
    def rules(self) -> RuleSet:
        return self._ruleset

    @staticmethod
    def _actor() -> str:
        """Identidade registrada na auditoria: a pessoa logada, ou "claude" sem login."""
        user = get_current_user()
        return DEFAULT_ACTOR if user.identity == ANONYMOUS_PROFILE.identity else user.identity

    @property
    def skills(self) -> tuple[SkillDefinition, ...]:
        return self._skillset.skills

    @property
    def rbac_default(self) -> str:
        return self._client_config.rbac_default

    @property
    def cors_origins(self) -> tuple[str, ...]:
        return self._client_config.cors_origins

    @property
    def cors_permitir_qualquer(self) -> bool:
        return self._client_config.cors_permitir_qualquer

    @property
    def client_context(self) -> str | None:
        return self._client_config.context

    @property
    def foco_trabalho(self) -> str | None:
        valor = self._client_config.foco_trabalho
        if isinstance(valor, str):
            return valor.strip() or None
        return FOCO_TRABALHO_PADRAO if valor else None

    @property
    def simulador_habilitado(self) -> bool:
        return self._client_config.simulador

    @contextmanager
    def vault_snapshot(self) -> Iterator[None]:
        """Reindexa uma vez e reaproveita as notas na resolução de fatos derivados."""
        self._vault_provider.reindex(self.client_id)
        self._notes_snapshot = tuple(self._vault_provider.list_notes(self.client_id))
        try:
            yield
        finally:
            self._notes_snapshot = None

    def demo_scenarios(self):
        return self._demo_scenarios.get_scenarios(self.client_id)

    def avaliar_skill(
        self, skill_name: str, arguments: dict[str, Any], ruleset: RuleSet | None = None
    ) -> dict[str, Any]:
        """Decisão das regras para uma skill, sem auditoria nem efeitos externos."""
        skill = self._find_skill(skill_name)
        if skill is None:
            return {"efeito": "deny", "rule_id": None, "message": "skill não encontrada"}
        derived = self._resolve_derived_facts(skill, arguments)
        nomes_modulo = self._nomes_de_fatos_de_modulo(skill)
        clean = {
            k: v
            for k, v in arguments.items()
            if k not in skill.derived_facts and k not in nomes_modulo
        }
        try:
            modulo = self._fatos_de_modulo(skill, clean)
        except FatosIndisponiveis as exc:
            return {"efeito": "deny", "rule_id": None, "message": str(exc)}
        facts: dict[str, Any] = {
            **clean,
            "skill_name": skill_name,
            "actor": self._actor(),
            "actor_role": get_current_user().role.value,
            "origem_sistema": "mcp",
            **derived,
            **modulo,
        }
        decision = evaluate(ruleset or self._ruleset, facts)
        return {
            "efeito": decision.effect.value,
            "rule_id": decision.rule_id,
            "message": decision.message,
            "proximo_passo": decision.next_step,
            "fatos_derivados": derived,
            "fatos_de_modulo": modulo,
        }

    def _resolve_derived_facts(
        self, skill: SkillDefinition, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Calcula, a partir do vault, os fatos que o chamador não pode informar."""
        if not skill.derived_facts:
            return {}

        if self._notes_snapshot is not None:
            notes = self._notes_snapshot
        else:
            try:
                self._vault_provider.reindex(self.client_id)
                notes = self._vault_provider.list_notes(self.client_id)
            except Exception:
                notes = ()

        actor = get_current_user().identity
        derived: dict[str, Any] = {}
        for name, spec in skill.derived_facts.items():
            wanted = {
                key: str(arguments.get(ref[1:], "")) if ref.startswith("$") else ref
                for key, ref in spec.vault_match.items()
            }
            note = next(
                (
                    n
                    for n in notes
                    if all(str(n.frontmatter.get(k)) == v for k, v in wanted.items())
                ),
                None,
            )
            if note is None or spec.field not in note.frontmatter:
                derived[name] = spec.if_missing
                continue

            raw = note.frontmatter[spec.field]
            if spec.transform == "equals_actor":
                derived[name] = spec.if_true if str(raw) == actor else spec.if_false
            elif spec.transform == "in":
                derived[name] = spec.if_true if str(raw) in spec.values else spec.if_false
            else:
                derived[name] = raw
        return derived

    def _fatos_operacoes(self, ctx: ContextoFatos, args: Any) -> dict[str, Any]:
        contagens = self.operacoes.contagens()
        return {
            "operacoes_aguardando": contagens["aguardando_aprovacao"],
            "operacoes_aguardando_horas_max": contagens["aguardando_mais_antiga_horas"],
        }

    def _validar_fatos_de_modulo(self) -> None:
        for skill in self._skillset.skills:
            erros = self.extensoes.provedores.validar_uso(
                skill.fatos_de_modulo, skill.parameters, skill.derived_facts
            )
            if erros:
                raise ModuleError(f"skill {skill.name!r}: {'; '.join(erros)}")

    def _fatos_de_modulo(self, skill: SkillDefinition, arguments: dict[str, Any]) -> dict[str, Any]:
        """Fatos calculados pelos provedores dos módulos que a skill declara (somente leitura)."""
        if not skill.fatos_de_modulo:
            return {}
        user = get_current_user()
        ctx = ContextoFatos(self.client_id, user.identity, user.role.value, self._data_root)
        return self.extensoes.provedores.calcular(skill.fatos_de_modulo, ctx, arguments)

    def _nomes_de_fatos_de_modulo(self, skill: SkillDefinition) -> frozenset[str]:
        return self.extensoes.provedores.fatos_de(skill.fatos_de_modulo)

    def verificar_saude_modulos(self) -> dict[str, dict[str, Any]]:
        """Executa as verificações de integridade registradas pelos módulos (para /health)."""
        resultados: dict[str, dict[str, Any]] = {}
        for nome, fn in sorted(self.extensoes.verificacoes.items()):
            try:
                bruto = fn()
                resultados[nome] = {**bruto, "ok": bool(bruto.get("ok"))}
            except Exception as exc:
                resultados[nome] = {"ok": False, "detalhe": type(exc).__name__}
        return resultados

    def _find_skill(self, skill_name: str):
        for skill in self._skillset.skills:
            if skill.name == skill_name:
                return skill
        return None

    def _record_audit(
        self,
        actor: str,
        action: str,
        decision: AuditDecision,
        rule_id: str | None = None,
    ) -> None:
        self._audit_sink.record(
            AuditEvent(
                client_id=self.client_id,
                occurred_at=datetime.now(UTC),
                actor=actor,
                action=action,
                decision=decision,
                rule_id=rule_id,
            )
        )

    def call_skill(
        self,
        skill_name: str,
        arguments: dict[str, Any],
        *,
        simulacao: bool = False,
        trace: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        _, token = ensure_trace_id()
        try:
            return self._call_skill(
                skill_name,
                arguments,
                simulacao=simulacao,
                trace=trace,
                idempotency_key=idempotency_key,
            )
        finally:
            if token is not None:
                trace_id_var.reset(token)

    def _call_skill(
        self,
        skill_name: str,
        arguments: dict[str, Any],
        *,
        simulacao: bool = False,
        trace: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        _aprovada: bool = False,
        _regra_aprovada: str | None = None,
        _confirmado_por: str | None = None,
        _aprovado_por: str | None = None,
        _sistema: bool = False,
    ) -> dict[str, Any]:
        actor = self._actor()

        skill = self._find_skill(skill_name)
        if skill is None:
            self._record_audit(actor, skill_name, AuditDecision.DENIED)
            return {
                "allowed": False,
                "reason": f"skill not found: {skill_name}",
                "result": "",
                "payload_digest": None,
            }

        derived = self._resolve_derived_facts(skill, arguments)
        for fact_name, fact_value in derived.items():
            if fact_name in arguments and str(arguments[fact_name]) != str(fact_value):
                self._audit_sink.record(
                    AuditEvent(
                        client_id=self.client_id,
                        occurred_at=datetime.now(UTC),
                        actor=actor,
                        action=f"{skill_name}:fato_divergente",
                        decision=AuditDecision.DENIED,
                        details={
                            "fact": fact_name,
                            "informado": str(arguments[fact_name]),
                            "derivado": str(fact_value),
                        },
                    )
                )
        nomes_modulo = self._nomes_de_fatos_de_modulo(skill)
        informados_modulo = {k: v for k, v in arguments.items() if k in nomes_modulo}
        arguments = {
            k: v
            for k, v in arguments.items()
            if k not in skill.derived_facts and k not in nomes_modulo
        }
        if trace is not None:
            trace["fatos_derivados"] = derived

        request_dlp = scan_request(arguments, self._client_config, get_current_user().role)
        if trace is not None and request_dlp.dlp_matches:
            trace["dlp_entrada"] = request_dlp.dlp_matches
        if not request_dlp.allowed:
            self._audit_sink.record(
                AuditEvent(
                    client_id=self.client_id,
                    occurred_at=datetime.now(UTC),
                    actor=actor,
                    action=skill_name,
                    decision=AuditDecision.DENIED,
                    details={"dlp_entrada": request_dlp.dlp_matches, "reason": request_dlp.reason},
                )
            )
            return {
                "allowed": False,
                "reason": request_dlp.reason,
                "result": "",
                "payload_digest": None,
            }
        arguments = request_dlp.arguments

        try:
            modulo = self._fatos_de_modulo(skill, arguments)
        except FatosIndisponiveis as exc:
            self._audit_sink.record(
                AuditEvent(
                    client_id=self.client_id,
                    occurred_at=datetime.now(UTC),
                    actor=actor,
                    action=f"{skill_name}:fatos_indisponiveis",
                    decision=AuditDecision.DENIED,
                    details={"provedor": exc.provedor},
                )
            )
            return {
                "allowed": False,
                "reason": "Não foi possível calcular os fatos desta operação; nada foi executado.",
                "result": "",
                "payload_digest": None,
            }
        for fact_name, fact_value in informados_modulo.items():
            if str(modulo.get(fact_name)) != str(fact_value):
                self._audit_sink.record(
                    AuditEvent(
                        client_id=self.client_id,
                        occurred_at=datetime.now(UTC),
                        actor=actor,
                        action=f"{skill_name}:fato_divergente",
                        decision=AuditDecision.DENIED,
                        details={
                            "fact": fact_name,
                            "informado": str(fact_value),
                            "derivado": str(modulo.get(fact_name)),
                        },
                    )
                )
        if trace is not None:
            trace["fatos_de_modulo"] = modulo

        facts: dict[str, Any] = {
            **arguments,
            "skill_name": skill_name,
            "actor": actor,
            "actor_role": get_current_user().role.value,
            "origem_sistema": "mcp",
            **derived,
            **modulo,
        }
        ruleset = self._ruleset
        if _regra_aprovada is not None:
            ruleset = RuleSet(
                schema_version=1,
                client_id=ruleset.client_id,
                rules=tuple(r for r in ruleset.rules if r.id != _regra_aprovada),
            )
        rule_decision = evaluate(ruleset, facts)

        if rule_decision.effect is RuleEffect.DENY:
            self._record_audit(actor, skill_name, AuditDecision.DENIED, rule_decision.rule_id)
            return {
                "allowed": False,
                "reason": rule_decision.message,
                "rule_id": rule_decision.rule_id,
                "proximo_passo": rule_decision.next_step,
                "result": "",
                "payload_digest": None,
            }

        if rule_decision.effect is RuleEffect.REQUIRE_APPROVAL and _aprovada:
            self._record_audit(actor, skill_name, AuditDecision.DENIED, rule_decision.rule_id)
            return {
                "allowed": False,
                "reason": "Uma regra adicional exige nova aprovação desta operação.",
                "rule_id": rule_decision.rule_id,
                "result": "",
                "payload_digest": None,
            }
        if rule_decision.effect is RuleEffect.REQUIRE_APPROVAL:
            self._record_audit(
                actor, skill_name, AuditDecision.PENDING_APPROVAL, rule_decision.rule_id
            )
            pending = {
                "allowed": False,
                "reason": "human approval required",
                "rule_id": rule_decision.rule_id,
                "message": rule_decision.message,
                "proximo_passo": rule_decision.next_step,
                "result": "",
                "payload_digest": None,
            }
            if not simulacao:
                op = self._propor_operacao(
                    skill,
                    arguments,
                    AGUARDANDO,
                    rule_decision.aprovador or skill.aprovador,
                    rule_decision.rule_id,
                )
                pending.update({"status": op.status, "operacao_id": op.id, "resumo": op.resumo})
            return pending

        nivel = skill.autonomia
        if (
            nivel == "A1"
            and skill.a1_condicao is not None
            and not jsonLogic(skill.a1_condicao, facts)
        ):
            nivel = "A2"
        if nivel in ("A2", "A3") and not _aprovada:
            if simulacao:
                payload = json.dumps({"status": "simulacao", "exigiria": nivel}, sort_keys=True)
                return {
                    "allowed": True,
                    "reason": None,
                    "result": payload,
                    "payload_digest": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                }
            status = PROPOSTA if nivel == "A2" and not _sistema else AGUARDANDO
            op = self._propor_operacao(skill, arguments, status, skill.aprovador, None)
            self._record_audit(actor, skill_name, AuditDecision.PENDING_APPROVAL)
            if status == PROPOSTA:
                passo = (
                    "Mostre o resumo ao usuário. Se ele concordar, chame confirmar_operacao "
                    "com este operacao_id."
                )
            else:
                passo = "Peça a um perfil autorizado que aprove com aprovar_operacao."
            return {
                "allowed": False,
                "reason": (
                    "confirmação necessária" if status == PROPOSTA else "aprovação necessária"
                ),
                "pendente": True,
                "status": op.status,
                "operacao_id": op.id,
                "resumo": op.resumo,
                "proximo_passo": passo,
                "result": "",
                "payload_digest": None,
            }

        if _sistema and nivel in ("A2", "A3") and not _aprovada:
            self._record_audit(actor, skill_name, AuditDecision.DENIED, rule_decision.rule_id)
            return {
                "allowed": False,
                "reason": "gatilho não executa ações A2 ou A3 sem pessoa",
                "result": "",
                "payload_digest": None,
            }

        if idempotency_key and not simulacao:
            cached = self._state.get_execucao(idempotency_key)
            if cached is not None:
                return {**cached, "idempotente_reutilizado": True}

        user_role = get_current_user().role
        if skill.executor.type == "mock":
            url = _render_url(skill.url_template or "", arguments)
            mock_response = json.dumps(
                {"status": "ok", "skill": skill_name, "arguments": arguments}, sort_keys=True
            )
            skill_call = SkillCall(
                skill_name=skill_name,
                url=url,
                method=skill.method,
                actor=actor,
                request_body=arguments,
                response_text=mock_response,
                response_status=200,
            )

            intercept_decision = intercept(skill_call, self._client_config, self._audit_sink)
            if trace is not None:
                trace["dlp"] = intercept_decision.dlp_matches
            if not intercept_decision.allowed:
                return {
                    "allowed": False,
                    "reason": intercept_decision.reason,
                    "result": "",
                    "payload_digest": None,
                }

            # Fase 1 é mocked-by-design (ver docs/spec.md) — enviar_email é a única
            # exceção: dispara um envio SMTP real quando SMTP_* está configurado,
            # sempre depois (nunca antes) do gate de DLP/allowlist acima.
            if skill_name == "enviar_email" and email_configured() and not simulacao:
                try:
                    send_result = send_email(
                        destinatario=str(arguments.get("destinatario", "")),
                        assunto=str(arguments.get("assunto", "")),
                        corpo=str(arguments.get("corpo", "")),
                    )
                except EmailSendError as exc:
                    self._record_audit(
                        actor, skill_name, AuditDecision.DENIED, rule_decision.rule_id
                    )
                    return {
                        "allowed": False,
                        "reason": f"falha ao enviar e-mail: {exc}",
                        "result": "",
                        "payload_digest": None,
                    }
                response_text = json.dumps(send_result, sort_keys=True)
            else:
                response_text = mock_response
        else:
            chamada = emitir_chamada(
                skill,
                arguments,
                actor,
                user_role.value,
                get_trace_id(),
                idempotency_key,
            )
            url = _render_url(skill.url_template or "", arguments)
            if skill.executor.type == "http":
                gate = intercept(
                    SkillCall(
                        skill_name=skill_name,
                        url=url,
                        method=skill.method,
                        actor=actor,
                        request_body=arguments,
                    ),
                    self._client_config,
                    self._audit_sink,
                )
                if not gate.allowed:
                    return {
                        "allowed": False,
                        "reason": gate.reason,
                        "result": "",
                        "payload_digest": None,
                    }
            else:
                self._record_audit(actor, skill_name, AuditDecision.ALLOWED, rule_decision.rule_id)

            if simulacao:
                outcome: dict[str, Any] = {"status": "simulacao", "skill": skill_name}
            else:
                try:
                    if skill.executor.type == "http":
                        outcome = run_http(chamada, url, self.secrets, self._http_client)
                    else:
                        outcome = run_native(
                            chamada,
                            self.handlers,
                            HandlerContext(
                                client_id=self.client_id,
                                actor=actor,
                                role=user_role.value,
                                trace_id=chamada.trace_id,
                                data_root=self._data_root,
                                idempotency_key=idempotency_key,
                                confirmado_por=_confirmado_por,
                                aprovado_por=_aprovado_por,
                            ),
                        )
                        eventos = outcome.pop(EVENTOS_CHAVE, None)
                        if eventos:
                            self.gatilhos.publicar_eventos(eventos)
                except ExecucaoFalhou as exc:
                    self._audit_sink.record(
                        AuditEvent(
                            client_id=self.client_id,
                            occurred_at=datetime.now(UTC),
                            actor=actor,
                            action=skill_name,
                            decision=AuditDecision.DENIED,
                            rule_id=rule_decision.rule_id,
                            details={"fase": "execucao", "motivo": exc.reason},
                        )
                    )
                    return {
                        "allowed": False,
                        "reason": exc.reason,
                        "result": "",
                        "payload_digest": None,
                    }

            response_text = json.dumps(outcome, sort_keys=True, default=str)
            scan = scan_response(response_text, self._client_config)
            if trace is not None:
                trace["dlp"] = scan.dlp_matches
            if not scan.allowed:
                self._audit_sink.record(
                    AuditEvent(
                        client_id=self.client_id,
                        occurred_at=datetime.now(UTC),
                        actor=actor,
                        action=skill_name,
                        decision=AuditDecision.DENIED,
                        details={"fase": "resposta", "dlp": scan.dlp_matches},
                    )
                )
                return {
                    "allowed": False,
                    "reason": scan.reason,
                    "result": "",
                    "payload_digest": None,
                }

        response_text = redact_for_role(
            response_text, self._client_config.dlp_patterns, get_current_user().role
        )

        response: dict[str, Any] = {
            "allowed": True,
            "reason": None,
            "result": response_text,
            "payload_digest": hashlib.sha256(response_text.encode("utf-8")).hexdigest(),
        }
        if skill.vault_read is not None:
            response["vault"] = self._skill_vault_read(skill, arguments)
        if idempotency_key and not simulacao:
            self._state.save_execucao(idempotency_key, skill_name, response)
        return response

    # ------------------------------------------------------------------
    # Operações com confirmação e aprovação (spec 022)
    # ------------------------------------------------------------------

    def _resumo(self, skill: SkillDefinition, arguments: dict[str, Any]) -> str:
        if skill.resumo:
            try:
                return skill.resumo.format_map(_SafeDict(arguments))
            except (ValueError, IndexError):
                pass
        detalhes = ", ".join(f"{k}: {v}" for k, v in sorted(arguments.items()))
        base = skill.description.strip().rstrip(".")
        return f"{base} ({detalhes})" if detalhes else base

    def _propor_operacao(
        self,
        skill: SkillDefinition,
        arguments: dict[str, Any],
        status: str,
        aprovador: str | None,
        regra_id: str | None,
    ) -> Operacao:
        user = get_current_user()
        return self.operacoes.criar(
            skill=skill.name,
            argumentos=arguments,
            resumo=self._resumo(skill, arguments),
            status=status,
            proposto_por=user.identity,
            proposto_papel=user.role.value,
            aprovador_papel=aprovador if status == AGUARDANDO else None,
            regra_id=regra_id,
            ttl_horas=self._client_config.operacao_ttl_horas,
        )

    def _audit_operacao(self, op: Operacao, estado: str, decision: AuditDecision, **extra: Any):
        self._audit_sink.record(
            AuditEvent(
                client_id=self.client_id,
                occurred_at=datetime.now(UTC),
                actor=get_current_user().identity,
                action=f"operacao:{estado}",
                decision=decision,
                rule_id=op.regra_id,
                payload_digest=op.digest_argumentos,
                details={
                    "operacao_id": op.id,
                    "skill": op.skill,
                    "proposto_por": op.proposto_por,
                    **extra,
                },
            )
        )

    @staticmethod
    def _negado(motivo: str, **extra: Any) -> dict[str, Any]:
        return {"allowed": False, "reason": motivo, **extra}

    def _carregar_operacao(self, operacao_id: str) -> Operacao | None:
        op = self.operacoes.get(operacao_id)
        if op is None:
            return None
        if op.status in (PROPOSTA, AGUARDANDO) and datetime.now(UTC) > op.expira_em:
            if self.operacoes.transicao(op.id, op.status, EXPIRADA):
                expirada = self.operacoes.get(op.id)
                if expirada is not None:
                    self._audit_operacao(expirada, EXPIRADA, AuditDecision.DENIED)
            return self.operacoes.get(op.id)
        return op

    def _executar_operacao(self, op: Operacao) -> dict[str, Any]:
        """Executa uma operação confirmada, como quem a propôs (chave = id da operação)."""
        if digest_argumentos(op.argumentos) != op.digest_argumentos:
            self.operacoes.transicao(op.id, CONFIRMADA, FALHOU, motivo="argumentos adulterados")
            self._audit_operacao(op, FALHOU, AuditDecision.DENIED, motivo="argumentos adulterados")
            return self._negado("A operação foi alterada depois de proposta e não será executada.")

        if op.skill == OPERACAO_ATIVAR:
            result = self.caderno.executar_ativacao(op)
        else:
            proponente = UserProfile(identity=op.proposto_por, role=Role(op.proposto_papel))
            token = set_current_user(proponente)
            try:
                result = self._call_skill(
                    op.skill,
                    dict(op.argumentos),
                    idempotency_key=op.id,
                    _aprovada=True,
                    _regra_aprovada=op.regra_id,
                    _confirmado_por=op.confirmado_por,
                    _aprovado_por=op.aprovado_por,
                )
            finally:
                current_user_var.reset(token)

        agora = datetime.now(UTC)
        if result.get("allowed"):
            self.operacoes.transicao(
                op.id, CONFIRMADA, EXECUTADA, resultado=result, executada_em=agora
            )
            self._audit_operacao(op, EXECUTADA, AuditDecision.ALLOWED)
            return {**result, "operacao_id": op.id, "status": EXECUTADA}
        motivo = str(result.get("reason") or "falha na execução")
        self.operacoes.transicao(op.id, CONFIRMADA, FALHOU, motivo=motivo)
        self._audit_operacao(op, FALHOU, AuditDecision.DENIED, motivo=motivo)
        return {**result, "operacao_id": op.id, "status": FALHOU}

    def confirmar_operacao(self, operacao_id: str) -> dict[str, Any]:
        user = get_current_user()
        op = self._carregar_operacao(operacao_id)
        if op is None:
            return self._negado("operação não encontrada")
        if op.status != PROPOSTA:
            return self._negado(f"operação não está aguardando confirmação (estado: {op.status})")
        if op.proposto_por != user.identity:
            self._audit_operacao(op, "confirmacao_negada", AuditDecision.DENIED)
            return self._negado("Somente quem propôs a operação pode confirmá-la.")
        if not self.operacoes.transicao(op.id, PROPOSTA, CONFIRMADA, confirmado_por=user.identity):
            return self._negado("operação já foi tratada")
        confirmada = self.operacoes.get(op.id)
        self._audit_operacao(confirmada, CONFIRMADA, AuditDecision.ALLOWED)
        return self._executar_operacao(confirmada)

    def aprovar_operacao(self, operacao_id: str) -> dict[str, Any]:
        user = get_current_user()
        op = self._carregar_operacao(operacao_id)
        if op is None:
            return self._negado("operação não encontrada")
        if op.status != AGUARDANDO:
            return self._negado(f"operação não está aguardando aprovação (estado: {op.status})")
        if RANK[user.role.value] < RANK.get(op.aprovador_papel or "admin", 1):
            self._audit_operacao(op, "aprovacao_negada", AuditDecision.DENIED, motivo="papel")
            return self._negado(f"Esta operação exige aprovação de perfil {op.aprovador_papel}.")
        if user.identity == op.proposto_por:
            self._audit_operacao(op, "aprovacao_negada", AuditDecision.DENIED, motivo="propria")
            return self._negado("Quem propôs a operação não pode aprová-la.")
        if not self.operacoes.transicao(op.id, AGUARDANDO, CONFIRMADA, aprovado_por=user.identity):
            return self._negado("operação já foi tratada")
        aprovada = self.operacoes.get(op.id)
        self._audit_operacao(
            aprovada, CONFIRMADA, AuditDecision.ALLOWED, aprovado_por=user.identity
        )
        return self._executar_operacao(aprovada)

    def rejeitar_operacao(self, operacao_id: str, motivo: str = "") -> dict[str, Any]:
        user = get_current_user()
        op = self._carregar_operacao(operacao_id)
        if op is None:
            return self._negado("operação não encontrada")
        if op.status != AGUARDANDO:
            return self._negado(f"operação não está aguardando aprovação (estado: {op.status})")
        if RANK[user.role.value] < RANK.get(op.aprovador_papel or "admin", 1):
            return self._negado(f"Esta operação exige perfil {op.aprovador_papel}.")
        if not self.operacoes.transicao(
            op.id, AGUARDANDO, REJEITADA, aprovado_por=user.identity, motivo=motivo or None
        ):
            return self._negado("operação já foi tratada")
        self._audit_operacao(op, REJEITADA, AuditDecision.DENIED, motivo=motivo or None)
        return {"allowed": True, "operacao_id": op.id, "status": REJEITADA}

    def cancelar_operacao(self, operacao_id: str) -> dict[str, Any]:
        user = get_current_user()
        op = self._carregar_operacao(operacao_id)
        if op is None:
            return self._negado("operação não encontrada")
        if op.status not in (PROPOSTA, AGUARDANDO):
            return self._negado(f"operação não pode ser cancelada (estado: {op.status})")
        if op.proposto_por != user.identity and user.role is not Role.SUPER_ADMIN:
            return self._negado("Somente quem propôs (ou super_admin) pode cancelar.")
        if not self.operacoes.transicao(op.id, op.status, CANCELADA):
            return self._negado("operação já foi tratada")
        self._audit_operacao(op, CANCELADA, AuditDecision.DENIED)
        return {"allowed": True, "operacao_id": op.id, "status": CANCELADA}

    def listar_operacoes(self, status: str | None = None) -> dict[str, Any]:
        user = get_current_user()
        visiveis = []
        for op in self.operacoes.listar(status):
            if op.status in (PROPOSTA, AGUARDANDO):
                op = self._carregar_operacao(op.id) or op
            if user.role is Role.SUPER_ADMIN or op.proposto_por == user.identity:
                visiveis.append(op)
            elif op.status == AGUARDANDO and RANK[user.role.value] >= RANK.get(
                op.aprovador_papel or "admin", 1
            ):
                visiveis.append(op)
        return {
            "total": len(visiveis),
            "operacoes": [
                {
                    "operacao_id": o.id,
                    "skill": o.skill,
                    "status": o.status,
                    "resumo": o.resumo,
                    "proposto_por": o.proposto_por,
                    "aprovador_papel": o.aprovador_papel,
                    "criado_em": o.criado_em.isoformat(),
                    "expira_em": o.expira_em.isoformat(),
                }
                for o in visiveis
            ],
        }

    def retomar_operacoes_orfas(self) -> int:
        """Retoma operações confirmadas que não terminaram (queda do processo).

        A chave de idempotência é o id da operação, então uma execução que já
        concluiu devolve o resultado guardado em vez de repetir o efeito.
        """
        retomadas = 0
        for op in self.operacoes.orfas():
            self._executar_operacao(op)
            retomadas += 1
        return retomadas

    def query_audit_log(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        action: str | None = None,
        decision: str | None = None,
    ) -> dict[str, Any]:
        if start_date:
            start = datetime.fromisoformat(start_date)
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
        else:
            start = None

        if end_date:
            end = datetime.fromisoformat(end_date)
            if end.tzinfo is None:
                end = end.replace(tzinfo=UTC)
        else:
            end = None

        decision_filter = AuditDecision(decision) if decision else None

        events: list[dict[str, Any]] = []
        for event in self._audit_sink.iter_events(self.client_id):
            if start is not None and event.occurred_at < start:
                continue
            if end is not None and event.occurred_at > end:
                continue
            if action is not None and event.action != action:
                continue
            if decision_filter is not None and event.decision != decision_filter:
                continue

            events.append(
                {
                    "occurred_at": event.occurred_at.isoformat(),
                    "actor": event.actor,
                    "action": event.action,
                    "decision": event.decision.value,
                    "rule_id": event.rule_id,
                }
            )

        return {"total": len(events), "events": events}

    def _filtered_notes(self, action: str, notes: Any) -> dict[str, Any]:
        user = get_current_user()
        result = filter_notes(self._vault_access, notes, user.identity, user.role.value)
        if self._vault_access is not None:
            details: dict[str, Any] = {
                "returned": len(result.visible),
                "hidden": result.hidden,
                "policies": result.policies,
            }
            if result.privileged:
                details["acesso_privilegiado"] = result.privileged
            self._audit_sink.record(
                AuditEvent(
                    client_id=self.client_id,
                    occurred_at=datetime.now(UTC),
                    actor=user.identity,
                    action=action,
                    decision=AuditDecision.ALLOWED,
                    details=details,
                )
            )
        return {
            "visible": result.visible,
            "hidden": result.hidden,
            "policies": result.policies,
        }

    def vault_search(self, query: str, limit: int = 10) -> dict[str, Any]:
        fetch = min(limit * 5, 50) if self._vault_access is not None else limit
        notes = self._vault_provider.search(self.client_id, query, fetch)
        filtered = self._filtered_notes("vault_search", notes)
        visible = filtered["visible"][:limit]
        response: dict[str, Any] = {
            "total": len(visible),
            "notes": [n.model_dump(mode="json") for n in visible],
        }
        if self._vault_access is not None:
            response["ocultas"] = filtered["hidden"]
            response["politicas"] = filtered["policies"]
        return response

    def vault_add_note(
        self, title: str, content: str, tags: list[str] | None = None
    ) -> dict[str, Any]:
        user = get_current_user()
        if self._vault_access is not None and user.role.value not in self._vault_access.write_roles:
            self._record_audit(user.identity, "vault_add_note", AuditDecision.DENIED)
            return {
                "allowed": False,
                "reason": "Seu perfil não pode criar notas no vault.",
                "proximo_passo": self._vault_access.write_denied_next_step,
            }
        frontmatter = {"tags": tags} if tags else None
        note = self._vault_provider.add_note(self.client_id, title, content, frontmatter)
        return note.model_dump(mode="json")

    def vault_get_related(self, note_id: str) -> dict[str, Any]:
        notes = self._vault_provider.get_related(self.client_id, note_id)
        visible = self._filtered_notes("vault_get_related", notes)["visible"]
        return {"total": len(visible), "notes": [n.model_dump(mode="json") for n in visible]}

    def vault_list_notes(self) -> dict[str, Any]:
        notes = self._vault_provider.list_notes(self.client_id)
        visible = self._filtered_notes("vault_list_notes", notes)["visible"]
        return {"total": len(visible), "notes": [n.model_dump(mode="json") for n in visible]}

    def _skill_vault_read(
        self, skill: SkillDefinition, arguments: dict[str, Any]
    ) -> list[dict[str, Any]]:
        spec = skill.vault_read
        if spec is None:
            return []
        query = str(arguments.get(spec.query_from, ""))
        if not query:
            return []
        notes = self._vault_provider.search(self.client_id, query, min(spec.limit * 5, 50))
        chosen = [n for n in notes if n.frontmatter.get("tipo") in spec.types][: spec.limit]
        return [
            {"note_id": n.note_id, "title": n.title, "content": n.content[:1500]} for n in chosen
        ]

    def _get_interpreter_agent(self) -> Any:
        if self._interpreter_agent is None:
            self._interpreter_agent = build_interpreter_agent()
        return self._interpreter_agent

    def vault_interpret_and_propose(self, note_id: str) -> dict[str, Any]:
        return propose_draft(
            self.client_id,
            note_id,
            self._vault_provider,
            self._client_config,
            self._audit_sink,
            self._draft_store,
            self._get_interpreter_agent(),
        )

    def vault_list_drafts(
        self, kind: str | None = None, status: str | None = None
    ) -> dict[str, Any]:
        kinds = [kind] if kind else ["rule", "skill", "document"]
        drafts: list[dict[str, Any]] = []
        for k in kinds:
            drafts.extend(self._draft_store.list_drafts(self.client_id, k, status))
        return {"total": len(drafts), "drafts": drafts}

    def _get_document_agent(self) -> Any:
        if self._document_agent is None:
            guia = self._vault_root() / "_modelos" / "guia.md"
            self._document_agent = build_document_agent(
                contexto=self._client_config.context,
                guia=guia.read_text(encoding="utf-8") if guia.is_file() else None,
            )
        return self._document_agent

    def _salvar_modelo(self, modelo_id: str, texto: str) -> str:
        pasta = self._vault_root() / "_modelos"
        pasta.mkdir(parents=True, exist_ok=True)
        destino = pasta / f"{modelo_id}.md"
        with open(destino, "x", encoding="utf-8") as arquivo:
            arquivo.write(texto)
        return f"_modelos/{destino.name}"

    def vault_generate_document(self, instrucoes: str) -> dict[str, Any]:
        return propose_document(
            self.client_id,
            instrucoes,
            self._client_config,
            self._audit_sink,
            self._draft_store,
            self._get_document_agent(),
            self._vault_provider,
        )

    def vault_promote_document(self, draft_id: str) -> dict[str, Any]:
        return promote_document(
            self.client_id,
            draft_id,
            self._draft_store,
            self._vault_provider,
            self._audit_sink,
            approved_by=self._actor(),
            salvar_modelo=self._salvar_modelo,
        )

    # ------------------------------------------------------------------
    # Central de Ações e emissor de documentos (spec 035)
    # ------------------------------------------------------------------

    def criar_acao(self, tipo: str, argumentos: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.acoes.criar(get_identidade(), [{"tipo": tipo, "argumentos": argumentos or {}}])

    def criar_lote_acoes(self, itens: list[dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(itens, list) or not all(isinstance(i, dict) for i in itens):
            return self._negado("itens deve ser uma lista de {tipo, argumentos}")
        return self.acoes.criar(get_identidade(), itens)

    def status_da_acao(self, acao_id: str) -> dict[str, Any]:
        return self.acoes.status(get_identidade(), acao_id)

    def tipos_de_acao(self) -> dict[str, Any]:
        tipos = self.extensoes.acoes.listar()
        return {
            "total": len(tipos),
            "tipos": [
                {
                    "tipo": t.nome,
                    "titulo": t.titulo,
                    "descricao": t.descricao,
                    "argumentos": t.esquema.model_json_schema().get("properties", {}),
                    "sensivel": t.sensivel,
                }
                for t in tipos
            ],
        }

    def documento_modelos(self) -> dict[str, Any]:
        if get_current_user().role not in (Role.ADMIN, Role.SUPER_ADMIN):
            return self._negado("Somente gestor consulta os modelos de documento.")
        modelos, erros = self.documentos.modelos()
        itens = []
        for modelo in modelos.values():
            resumo = modelo.resumo()
            resumo["aprovacao"] = self.documentos.store.aprovacao(modelo)
            itens.append(resumo)
        return {"total": len(itens), "modelos": itens, "erros": erros}

    def documento_aprovar_modelo(self, modelo_id: str) -> dict[str, Any]:
        user = get_current_user()
        if user.role not in (Role.ADMIN, Role.SUPER_ADMIN):
            self.auditar_evento("documento:modelo_aprovacao_negada", False, {"modelo": modelo_id})
            return self._negado("Somente gestor aprova modelos de documento.")
        try:
            modelo = self.documentos.modelo(modelo_id)
        except DocumentoErro as exc:
            return self._negado(str(exc))
        detalhes = {"modelo": modelo.id, "versao": modelo.versao, "hash": modelo.hash}
        self.auditar_evento("documento:modelo_aprovando", True, detalhes)
        if not self.documentos.store.aprovar_modelo(modelo, user.identity):
            self.auditar_evento("documento:modelo_aprovacao_negada", False, detalhes)
            return self._negado(
                "Esta versão já foi aprovada com outro conteúdo; aumente o campo 'versao'."
            )
        self.auditar_evento("documento:modelo_aprovado", True, detalhes)
        return {"allowed": True, **detalhes, "aprovado_por": user.identity}

    def config_estado(self) -> dict[str, Any]:
        """Estado de cada nota de configuração; também regenera as páginas de `sistema/`."""
        user = get_current_user()
        if user.role not in (Role.ADMIN, Role.SUPER_ADMIN):
            return self._negado("Somente gestor consulta o estado da configuração.")
        if not self.caderno.ativo:
            return self._negado("este cliente não usa configuração via notas")
        itens = self.caderno.estado()
        self.caderno.atualizar_paginas()
        notas = [{k: v for k, v in i.items() if k != "frontmatter"} for i in itens]
        return {"total": len(itens), "notas": notas}

    def config_propor(self, caminho: str) -> dict[str, Any]:
        return self.caderno.propor(get_current_user(), caminho)

    def config_reverter(self, caminho: str) -> dict[str, Any]:
        return self.caderno.reverter(get_current_user(), caminho)

    def config_rascunho(self, draft_id: str, kind: str) -> dict[str, Any]:
        return self.caderno.rascunho_de_draft(get_current_user(), draft_id, kind)

    def memoria_registrar(
        self,
        titulo: str,
        conteudo: str,
        categoria: str = "geral",
        expira_em: str | None = None,
    ) -> dict[str, Any]:
        return self.segundo_cerebro.memoria_registrar(
            get_current_user(), titulo, conteudo, categoria, expira_em
        )

    def memoria_rascunhos(self) -> dict[str, Any]:
        return self.segundo_cerebro.memoria_rascunhos(get_current_user())

    def memoria_promover(self, draft_id: str) -> dict[str, Any]:
        return self.segundo_cerebro.memoria_promover(get_current_user(), draft_id)

    def gerar_relatorio_mensal(
        self, periodo: str, unidade: str | None = None, gerador: str | None = None
    ) -> dict[str, Any]:
        return self.segundo_cerebro.gerar_relatorio_mensal(
            get_current_user(), periodo, unidade, gerador
        )

    def vault_promote_draft(self, draft_id: str, kind: str) -> dict[str, Any]:
        if self._client_config.config_via_notas and kind in ("rule", "skill"):
            return self._negado(
                "Este cliente configura regras e skills por notas aprovadas "
                "(config_rascunho e config_propor); a promoção direta está desativada."
            )
        return promote_draft(
            self.client_id,
            draft_id,
            kind,
            self._draft_store,
            self._rule_provider,
            self._skill_provider,
            self._audit_sink,
        )

    def consultar_dados(self) -> dict[str, Any]:
        """Tool de demonstração do RBAC — sem restrição, visível a
        viewer e admin. Não depende de nada além do próprio RBAC (o
        filtro de tools/list e o gate de tools/call já estão no
        RBACFastMCP; esta tool não precisa saber que RBAC existe)."""
        return {"allowed": True, "data": ["registro-001", "registro-002", "registro-003"]}

    def deletar_banco(self) -> dict[str, Any]:
        """Tool de demonstração do RBAC — restrita a admin. O gate
        principal é o RBACFastMCP.call_tool (nunca chega aqui se o perfil
        não tiver permissão), mas esta segunda checagem existe de
        propósito: mesmo que `deletar_banco()` seja chamado de algum outro
        caminho de código no futuro (não só via MCP), ela nunca executa a
        ação "destrutiva" sem reconferir o perfil em memória primeiro."""
        user = get_current_user()
        if user.role not in (Role.ADMIN, Role.SUPER_ADMIN):
            return {
                "allowed": False,
                "reason": (
                    f"Acesso negado: perfil '{user.role.value}' não pode executar 'deletar_banco'."
                ),
            }
        return {
            "allowed": True,
            "result": "banco de dados apagado (mock — nenhuma ação real executada)",
        }

    def _forbidden_super_admin_assignment(self, role_enum: Role) -> dict[str, Any] | None:
        """Ninguém promove a super_admin (acesso total) a não ser um
        super_admin — mesma lógica de "não atribuir um perfil igual ou
        acima do próprio" usada em sistemas de RBAC com hierarquia de
        perfis. admin/viewer continuam livres pra criar/rebaixar entre si."""
        if role_enum is Role.SUPER_ADMIN and get_current_user().role is not Role.SUPER_ADMIN:
            return {
                "allowed": False,
                "reason": "só um perfil super_admin pode atribuir o perfil super_admin",
            }
        return None

    def criar_usuario(self, email: str, role: str, api_token: str | None = None) -> dict[str, Any]:
        try:
            role_enum = Role(role)
        except ValueError:
            return {
                "allowed": False,
                "reason": f"perfil inválido: {role!r} (use 'admin', 'viewer' ou 'super_admin')",
            }

        if forbidden := self._forbidden_super_admin_assignment(role_enum):
            return forbidden

        try:
            profile, token = self.user_store.create_user(email, role_enum, api_token)
        except UserAlreadyExistsError:
            return {"allowed": False, "reason": f"usuário já existe: {email!r}"}
        except ValueError as exc:
            return {"allowed": False, "reason": str(exc)}

        return {
            "allowed": True,
            "identity": profile.identity,
            "role": profile.role.value,
            "api_token": token,
        }

    def listar_usuarios(self) -> dict[str, Any]:
        users = self.user_store.list_users()
        return {
            "total": len(users),
            "users": [{"identity": u.identity, "role": u.role.value} for u in users],
        }

    def atualizar_perfil_usuario(self, email: str, role: str) -> dict[str, Any]:
        """Troca o perfil de um usuário já existente. Restrita a
        super_admin (ver ROLE_TOOLS em auth.py) — é um poder maior que
        criar um usuário novo, então fica reservada ao topo da
        hierarquia."""
        try:
            role_enum = Role(role)
        except ValueError:
            return {
                "allowed": False,
                "reason": f"perfil inválido: {role!r} (use 'admin', 'viewer' ou 'super_admin')",
            }

        if forbidden := self._forbidden_super_admin_assignment(role_enum):
            return forbidden

        try:
            profile = self.user_store.update_role(email, role_enum)
        except UserNotFoundError:
            return {"allowed": False, "reason": f"usuário não encontrado: {email!r}"}

        return {"allowed": True, "identity": profile.identity, "role": profile.role.value}

    def listar_permissoes(self) -> dict[str, Any]:
        perms = self.user_store.list_permissions()
        return {
            "total": len(perms),
            "permissions": [
                {"role": p.role.value, "tool_name": p.tool_name, "allowed": p.allowed}
                for p in perms
            ],
        }

    def rotacionar_token(self, email: str) -> dict[str, Any]:
        """Gera um token novo para o usuário e invalida o anterior (super_admin)."""
        if get_current_user().role is not Role.SUPER_ADMIN:
            self._record_audit(self._actor(), "rotacionar_token", AuditDecision.DENIED)
            return {"allowed": False, "reason": "Somente super_admin pode rotacionar tokens."}
        try:
            token = self.user_store.rotate_token(email)
        except UserNotFoundError:
            return {"allowed": False, "reason": f"usuário não encontrado: {email!r}"}
        self._record_audit(self._actor(), "rotacionar_token", AuditDecision.ALLOWED)
        return {"allowed": True, "email": email, "api_token": token}

    def atualizar_permissao(self, role: str, tool_name: str, allowed: bool) -> dict[str, Any]:
        """Liga/desliga o acesso de `role` a `tool_name` — a mesma decisão
        que hoje é o dict fixo ROLE_TOOLS, agora persistida e editável em
        runtime. Restrita a super_admin (ver ROLE_TOOLS/tool_permissions)."""
        try:
            role_enum = Role(role)
        except ValueError:
            return {
                "allowed": False,
                "reason": f"perfil inválido: {role!r} (use 'admin', 'viewer' ou 'super_admin')",
            }

        self.user_store.set_permission(role_enum, tool_name, allowed)
        return {
            "allowed": True,
            "role": role_enum.value,
            "tool_name": tool_name,
            "is_allowed": allowed,
        }


FOCO_TRABALHO_PADRAO = (
    "Este conector é de uso exclusivo para o trabalho do cliente. Atenda apenas pedidos "
    "ligados à rotina dele e às ferramentas daqui. Para qualquer outro assunto (pessoal, "
    "entretenimento, tarefas escolares, programação, textos ou pesquisas sem relação com o "
    "trabalho), recuse com educação em uma frase e diga que este canal é só para o trabalho. "
    "Não use as ferramentas para outro fim."
)


def _server_instructions(engine: BRMEngine) -> str:
    lines = [f"Cliente: {engine.client_id}."]
    if engine.client_context:
        lines.append(engine.client_context.strip())
    lines.append(
        "Cada ação do cliente é uma tool própria, com parâmetros tipados. Entenda o pedido "
        "em linguagem natural e escolha a tool. Se o usuário citar uma pessoa, um documento "
        "ou um caso por nome ou descrição, use vault_search para achar o ID e passe o ID à "
        "tool; se houver mais de um candidato, pergunte ao usuário antes de agir. Nunca "
        "informe fatos de segurança por conta própria (vínculo, matrícula, frequência, idade): "
        "o motor os calcula. Se uma ação for negada ou ficar aguardando aprovação, explique o "
        "motivo e o proximo_passo ao usuário e não tente contornar a regra."
    )
    if engine.foco_trabalho:
        lines.append(engine.foco_trabalho)
    return " ".join(lines)


def build_server(engine: BRMEngine) -> RBACFastMCP:
    server = RBACFastMCP(
        "brm-engine",
        instructions=_server_instructions(engine),
        audit_sink=engine._audit_sink,
        client_id=engine.client_id,
        user_store=engine.user_store,
        rbac_default=engine.rbac_default,
    )

    @server.tool()
    def call_skill(skill_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Executa uma skill configurada do cliente pelo nome (as skills também
        existem como tools próprias, com parâmetros tipados; prefira estas). Toda
        chamada passa por regras de negócio, allowlist de domínio e DLP antes de
        executar; pode ser negada, exigir aprovação humana ou ser executada.
        Ex.: skill_name="enviar_email" com arguments={"destinatario","assunto","corpo"}
        quando o cliente tiver essa skill."""
        return engine.call_skill(skill_name, arguments)

    @server.tool()
    def query_audit_log(
        start_date: str | None = None,
        end_date: str | None = None,
        action: str | None = None,
        decision: str | None = None,
    ) -> dict[str, Any]:
        """Consulta a trilha de auditoria (hash-chained, append-only) do cliente:
        toda skill chamada, toda regra aplicada, todo draft gerado/promovido fica
        registrado aqui com decisão (allowed/denied/pending_approval) e motivo."""
        return engine.query_audit_log(
            start_date=start_date,
            end_date=end_date,
            action=action,
            decision=decision,
        )

    @server.tool()
    def vault_search(query: str, limit: int = 10) -> dict[str, Any]:
        """Busca híbrida (vetorial + lexical) no vault de conhecimento do cliente
        (modelos, protocolos, casos, aulas, contratos, conforme o negócio).
        Use para achar o ID de uma pessoa, caso ou documento citado por nome ou
        descrição, antes de chamar a tool da ação. Retorna as notas com conteúdo,
        frontmatter estruturado e links entre notas."""
        return engine.vault_search(query, limit)

    @server.tool()
    def vault_add_note(title: str, content: str, tags: list[str] | None = None) -> dict[str, Any]:
        """Cria uma nova nota markdown no vault do cliente."""
        return engine.vault_add_note(title, content, tags)

    @server.tool()
    def vault_get_related(note_id: str) -> dict[str, Any]:
        """Lista as notas do vault linkadas (backlinks) a uma nota específica."""
        return engine.vault_get_related(note_id)

    @server.tool()
    def vault_list_notes() -> dict[str, Any]:
        """Lista todas as notas do vault do cliente, sem filtro
        de busca — use vault_search quando quiser filtrar por conteúdo."""
        return engine.vault_list_notes()

    @server.tool()
    def vault_interpret_and_propose(note_id: str) -> dict[str, Any]:
        """Lê uma nota em config_drafts/ escrita em português descrevendo uma
        regra de negócio ou integração, e propõe um draft (HardRule em JSONLogic
        ou Skill) — nunca ativa direto, fica pendente até promote_draft."""
        return engine.vault_interpret_and_propose(note_id)

    @server.tool()
    def vault_list_drafts(kind: str | None = None, status: str | None = None) -> dict[str, Any]:
        """Lista drafts de regra/skill/documento gerados pelos agentes do
        cliente, com status (pending/approved/rejected). kind opcional:
        "rule", "skill" ou "document" — omitido, lista todos."""
        return engine.vault_list_drafts(kind, status)

    @server.tool()
    def vault_promote_draft(draft_id: str, kind: str) -> dict[str, Any]:
        """Promove um draft de regra ou skill pendente (id de vault_list_drafts)
        para produção — só depois disso passa a valer de verdade. Requer
        aprovação humana explícita e nomeada; nunca é automático. Para drafts
        de documento, use vault_promote_document."""
        return engine.vault_promote_draft(draft_id, kind)

    @server.tool()
    def vault_generate_document(instrucoes: str) -> dict[str, Any]:
        """Redige um modelo de documento padronizado (contrato, termo, declaração,
        checklist etc.) no estilo do segmento do cliente, com os campos que o emissor
        vai preencher declarados, e salva como draft pendente — nunca escreve direto no
        vault nem preenche dados de pessoas. Ex.: "gere um modelo de termo de consentimento"."""
        return engine.vault_generate_document(instrucoes)

    @server.tool()
    def vault_promote_document(draft_id: str) -> dict[str, Any]:
        """Promove um draft de documento pendente (id de vault_list_drafts,
        kind="document") para o vault de verdade — só depois de aprovação
        humana explícita e nomeada. Modelo com campos vai para _modelos/ e ainda
        precisa de documento_aprovar_modelo para ser usado na emissão."""
        return engine.vault_promote_document(draft_id)

    @server.tool()
    def config_estado() -> dict[str, Any]:
        """Lista as notas de configuração (regras, skills, gatilhos, parâmetros) com o
        estado de cada uma: ativa, alterada aguardando aprovação, pendente ou inválida.
        Só vale a versão aprovada. Restrita a gestor."""
        return engine.config_estado()

    @server.tool()
    def config_propor(caminho: str) -> dict[str, Any]:
        """Valida uma nota de configuração do caderno e abre a operação de ativação,
        com o resumo e o impacto gerados pelo motor. Outra pessoa precisa aprovar com
        aprovar_operacao; editar a nota não ativa nada."""
        return engine.config_propor(caminho)

    @server.tool()
    def config_reverter(caminho: str) -> dict[str, Any]:
        """Propõe a volta de uma nota de configuração à versão aprovada anterior
        (também exige aprovação de outra pessoa)."""
        return engine.config_reverter(caminho)

    @server.tool()
    def config_rascunho(draft_id: str, kind: str) -> dict[str, Any]:
        """Transforma um draft do interpretador (kind rule ou skill) em nota de
        configuração com status rascunho, para o gestor conferir no caderno."""
        return engine.config_rascunho(draft_id, kind)

    @server.tool()
    def memoria_registrar(
        titulo: str, conteudo: str, categoria: str = "geral", expira_em: str | None = None
    ) -> dict[str, Any]:
        """Registra um fato durável (preferência, decisão, exceção combinada) como
        rascunho de memória. Só passa a existir no vault depois que um gestor promover.
        Memória é dado: nunca vira regra."""
        return engine.memoria_registrar(titulo, conteudo, categoria, expira_em)

    @server.tool()
    def memoria_rascunhos() -> dict[str, Any]:
        """Lista os rascunhos de memória pendentes de promoção. Restrita a gestor."""
        return engine.memoria_rascunhos()

    @server.tool()
    def memoria_promover(draft_id: str) -> dict[str, Any]:
        """Promove um rascunho de memória para memoria/ no vault. Restrita a gestor."""
        return engine.memoria_promover(draft_id)

    @server.tool()
    def gerar_relatorio_mensal(
        periodo: str, unidade: str | None = None, gerador: str | None = None
    ) -> dict[str, Any]:
        """Gera o relatório mensal (AAAA-MM) em relatorios/ por template determinístico:
        os números vêm do módulo, nunca do modelo. Restrita a gestor."""
        return engine.gerar_relatorio_mensal(periodo, unidade, gerador)

    @server.tool()
    def criar_acao(tipo: str, argumentos: dict[str, Any] | None = None) -> dict[str, Any]:
        """Prepara uma ação (efeito ou dado pessoal) para a pessoa confirmar na Central de
        Ações e devolve só o link e um resumo. Mostre o link ao usuário; nada é executado aqui.
        Use identificadores (ids), nunca dados pessoais, nos argumentos; a exceção é um nome
        digitado pelo próprio usuário, quando o tipo pedir. Tipos e argumentos em tipos_de_acao.
        Ex.: tipo="nucleo.executar_skill",
        argumentos={"skill": "registrar_recebimento", "argumentos": {...}}."""
        return engine.criar_acao(tipo, argumentos)

    @server.tool()
    def criar_lote_acoes(itens: list[dict[str, Any]]) -> dict[str, Any]:
        """Como criar_acao, mas um único link para revisar e confirmar várias ações do mesmo
        pedido. itens = [{"tipo": ..., "argumentos": {...}}, ...]."""
        return engine.criar_lote_acoes(itens)

    @server.tool()
    def status_da_acao(acao_id: str) -> dict[str, Any]:
        """Estado de uma ação criada por esta conta: pendente, em_execucao, confirmada,
        expirada, falhou ou cancelada, com um resumo do resultado (sem dados pessoais)."""
        return engine.status_da_acao(acao_id)

    @server.tool()
    def tipos_de_acao() -> dict[str, Any]:
        """Lista os tipos de ação da Central de Ações deste cliente, com os argumentos."""
        return engine.tipos_de_acao()

    @server.tool()
    def documento_modelos() -> dict[str, Any]:
        """Lista os modelos de documento de _modelos/ com campos declarados e se a versão
        atual está aprovada para emissão. Restrita a gestor."""
        return engine.documento_modelos()

    @server.tool()
    def documento_aprovar_modelo(modelo_id: str) -> dict[str, Any]:
        """Aprova a versão atual de um modelo de documento para emissão (guarda o hash).
        Alterar o arquivo depois exige nova aprovação. Restrita a gestor."""
        return engine.documento_aprovar_modelo(modelo_id)

    @server.tool()
    def consultar_dados() -> dict[str, Any]:
        """Consulta dados de exemplo — disponível para qualquer perfil
        (viewer ou admin). Tool de demonstração do controle de acesso por
        perfil (RBAC), sem efeito colateral real."""
        return engine.consultar_dados()

    @server.tool()
    def deletar_banco() -> dict[str, Any]:
        """Apaga o banco de dados — ação destrutiva, restrita ao perfil
        admin. Tool de demonstração do RBAC: um perfil viewer não vê esta
        tool em tools/list, e mesmo chamando-a diretamente recebe "Acesso
        negado" sem que nada seja executado (mock — não apaga nada real)."""
        return engine.deletar_banco()

    @server.tool()
    def criar_usuario(email: str, role: str, api_token: str | None = None) -> dict[str, Any]:
        """Cria um usuário no banco de perfis do cliente (admin, viewer,
        super_admin). Gera um token de acesso automaticamente se não for
        informado — só é devolvido nesta chamada, não há como recuperá-lo
        depois. Restrita ao perfil admin; atribuir o perfil super_admin só
        é permitido a quem já é super_admin."""
        return engine.criar_usuario(email, role, api_token)

    @server.tool()
    def listar_usuarios() -> dict[str, Any]:
        """Lista os usuários cadastrados (identidade e perfil) — nunca
        inclui o token de acesso. Restrita ao perfil admin."""
        return engine.listar_usuarios()

    @server.tool()
    def atualizar_perfil_usuario(email: str, role: str) -> dict[str, Any]:
        """Troca o perfil (admin, viewer, super_admin) de um usuário já
        cadastrado. Restrita ao perfil super_admin — gerenciar permissões
        de quem já existe é um poder maior que só criar usuário novo, e
        atribuir super_admin exige já ser super_admin."""
        return engine.atualizar_perfil_usuario(email, role)

    @server.tool()
    def listar_permissoes() -> dict[str, Any]:
        """Lista todas as permissões de tool por perfil (role, tool_name,
        allowed) hoje cadastradas — a fonte de verdade viva por trás do
        RBAC. Restrita ao perfil super_admin."""
        return engine.listar_permissoes()

    @server.tool()
    def confirmar_operacao(operacao_id: str) -> dict[str, Any]:
        """Confirma e executa uma operação proposta (resultado de uma ação que
        pediu confirmação). Só quem propôs pode confirmar. Use o operacao_id
        devolvido pela ação, depois de mostrar o resumo ao usuário e ele concordar."""
        return engine.confirmar_operacao(operacao_id)

    @server.tool()
    def aprovar_operacao(operacao_id: str) -> dict[str, Any]:
        """Aprova e executa uma operação que aguarda aprovação de outro perfil.
        Quem propôs a operação não pode aprová-la."""
        return engine.aprovar_operacao(operacao_id)

    @server.tool()
    def rejeitar_operacao(operacao_id: str, motivo: str = "") -> dict[str, Any]:
        """Rejeita uma operação que aguarda aprovação, com o motivo."""
        return engine.rejeitar_operacao(operacao_id, motivo)

    @server.tool()
    def cancelar_operacao(operacao_id: str) -> dict[str, Any]:
        """Cancela uma operação pendente. Só quem propôs (ou super_admin)."""
        return engine.cancelar_operacao(operacao_id)

    @server.tool()
    def listar_operacoes(status: str | None = None) -> dict[str, Any]:
        """Lista as operações do usuário (e, para aprovadores, as que aguardam
        aprovação). status opcional: proposta, aguardando_aprovacao, executada..."""
        return engine.listar_operacoes(status)

    @server.tool()
    def rotacionar_token(email: str) -> dict[str, Any]:
        """Gera um token novo para um usuário e invalida o anterior. O token
        só é devolvido nesta chamada. Restrita ao perfil super_admin."""
        return engine.rotacionar_token(email)

    @server.tool()
    def atualizar_permissao(role: str, tool_name: str, allowed: bool) -> dict[str, Any]:
        """Liga ou desliga o acesso de um perfil (admin, viewer) a uma tool
        específica — em runtime, sem precisar de deploy. Cobre qualquer
        tool registrada no servidor, inclusive as hoje livres a todo mundo
        (call_skill, vault_*). Restrita ao
        perfil super_admin, que sempre tem acesso irrestrito por conta
        própria e não precisa de entrada aqui."""
        return engine.atualizar_permissao(role, tool_name, allowed)

    existing = {t.name for t in server._tool_manager.list_tools()}
    for tool_name, fn in engine.module_tools.items():
        if tool_name in existing:
            raise ValueError(f"tool de módulo conflita com tool do núcleo: {tool_name}")
        server.add_tool(fn, name=tool_name)
        existing.add(tool_name)
    for skill in engine.skills:
        if skill.name in existing:
            continue
        server.add_tool(
            _make_skill_tool(engine, skill),
            name=skill.name,
            description=_skill_tool_description(skill),
        )

    all_tools = [t.name for t in server._tool_manager.list_tools()]
    if engine.rbac_default == "deny":
        if engine.user_store.fresh_permissions:
            engine.user_store.seed_open_tools(all_tools)
        else:
            missing = engine.user_store.tools_without_permissions(all_tools)
            if missing:
                logger.warning(
                    "rbac_default=deny: tools sem permissão (negadas a viewer/admin): %s. "
                    "Rode scripts/migrar_permissoes.py ou conceda no painel.",
                    ", ".join(sorted(missing)),
                )

    return server


def main() -> None:
    # --- Modo stdio (local) ---------------------------------------------
    # Um processo stdio é uma sessão de um único usuário: o cliente MCP
    # (Claude Desktop, Claude Code CLI etc.) sobe este processo uma vez e
    # fala com ele por toda a sessão via stdin/stdout. Por isso a
    # identidade é resolvida UMA VEZ aqui, antes do loop bloqueante de
    # server.run() — ao contrário do modo HTTP/SSE (mcp_engine/core/
    # http_app.py), onde cada conexão pode ser um usuário diferente e a
    # identidade é resolvida por request, via middleware.
    #
    # Como rodar com um perfil específico:
    #   CLIENT_ID=example MCP_USER_EMAIL=socio@escritorio.com.br \
    #     uv run python -m mcp_engine.core.mcp_server
    #
    # Sem MCP_USER_EMAIL/MCP_API_TOKEN (ou credencial desconhecida no banco),
    # cai no perfil anônimo (viewer) — ver mcp_engine/core/auth.py.
    #
    # O engine é construído ANTES da resolução de identidade porque é ele
    # quem carrega o SQLUserStore (clients/<cliente>/config/users.db) que a
    # resolução consulta — diferente de versões anteriores desta função,
    # que resolviam a identidade contra um dicionário em memória sem
    # depender de nada do engine.
    engine = BRMEngine(
        client_id=os.getenv("CLIENT_ID", "example"),
        config_root=Path(os.getenv("CONFIG_ROOT", "clients")),
        data_root=Path(os.getenv("DATA_ROOT", "data")),
    )
    engine.retomar_operacoes_orfas()
    set_current_user(resolve_stdio_identity(engine.user_store))

    server = build_server(engine)
    server.run()


if __name__ == "__main__":
    main()
