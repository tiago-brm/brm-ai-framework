from __future__ import annotations

import hashlib
import inspect
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from audit.sink import FileSystemAuditSink
from mcp_engine.core.auth import (
    ANONYMOUS_PROFILE,
    Role,
    get_current_user,
    resolve_stdio_identity,
    set_current_user,
)
from mcp_engine.core.config_loader import (
    FileSystemClientConfigProvider,
    FileSystemDemoScenariosProvider,
    FileSystemRuleProvider,
    FileSystemSkillProvider,
    FileSystemVaultAccessProvider,
)
from mcp_engine.core.datajud import DatajudError, consultar_processo
from mcp_engine.core.email_sender import EmailSendError, email_configured, send_email
from mcp_engine.core.interceptor import intercept, redact_for_role
from mcp_engine.core.models import (
    AuditDecision,
    AuditEvent,
    RuleEffect,
    RuleSet,
    SkillCall,
    SkillDefinition,
)
from mcp_engine.core.rbac_server import RBACFastMCP
from mcp_engine.core.rule_evaluator import evaluate
from mcp_engine.core.user_store import SQLUserStore, UserAlreadyExistsError, UserNotFoundError
from mcp_engine.vault.access import filter_notes
from mcp_engine.vault.document_generator import (
    build_document_agent,
    promote_document,
    propose_document,
)
from mcp_engine.vault.draft_store import DraftStore
from mcp_engine.vault.interpreter import (
    build_interpreter_agent,
    promote_draft,
    propose_draft,
)
from mcp_engine.vault.notion_adapter import NotionVaultProvider
from mcp_engine.vault.obsidian_adapter import ObsidianVaultProvider

DEFAULT_ACTOR = "claude"


def _render_processo_note(result: dict[str, Any]) -> str:
    classe = (result.get("classe") or {}).get("nome", "—")
    orgao = (result.get("orgao_julgador") or {}).get("nome", "—")
    assuntos = ", ".join(a.get("nome", "") for a in (result.get("assuntos") or []))
    movimentos = list(result.get("movimentos") or [])
    movimentos.sort(key=lambda m: m.get("dataHora", ""), reverse=True)

    linhas = [
        f"Consulta ao DataJud (CNJ) — tribunal `{result.get('tribunal')}`, "
        f"grau `{result.get('grau')}`.",
        "",
        f"- **Classe:** {classe}",
        f"- **Órgão julgador:** {orgao}",
        f"- **Assuntos:** {assuntos or '—'}",
        f"- **Ajuizamento:** {result.get('data_ajuizamento') or '—'}",
        "",
        "## Últimas movimentações",
    ]
    for m in movimentos[:10]:
        linhas.append(f"- {m.get('dataHora', '')} — {m.get('nome', '')}")

    return "\n".join(linhas) + "\n"


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
    ) -> None:
        self.client_id = client_id
        self._config_root = Path(config_root)
        self._data_root = Path(data_root)

        self._rule_provider = FileSystemRuleProvider(self._config_root)
        self._skill_provider = FileSystemSkillProvider(self._config_root)
        self._client_config_provider = FileSystemClientConfigProvider(self._config_root)
        self._audit_sink = FileSystemAuditSink(self._data_root)

        # Eagerly load to catch config errors early. Also needed here (rather
        # than after vault_provider construction) since the vault provider
        # factory below reads client_config.vault_provider.
        self._ruleset = self._rule_provider.get_rules(client_id)
        self._skillset = self._skill_provider.get_skills(client_id)
        self._client_config = self._client_config_provider.get_client_config(client_id)
        self._vault_access = FileSystemVaultAccessProvider(self._config_root).get_vault_access(
            client_id
        )
        self._demo_scenarios = FileSystemDemoScenariosProvider(self._config_root)
        self._notes_snapshot: tuple[Any, ...] | None = None

        self._vault_provider = vault_provider or self._build_vault_provider()
        self._draft_store = DraftStore(self._config_root)
        self.user_store = user_store or SQLUserStore(self._config_root, client_id)
        # Built lazily on first interpreter call: constructing agno's Agent is
        # cheap, but there's no reason to import agno/anthropic for engines
        # that never touch the interpreter.
        self._interpreter_agent = interpreter_agent
        self._document_agent = document_agent

        if vault_watch and hasattr(self._vault_provider, "start_watching"):
            try:
                self._vault_provider.start_watching(client_id)
            except Exception:
                pass

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
    def client_context(self) -> str | None:
        return self._client_config.context

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

    def avaliar_skill(self, skill_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Decisão das regras para uma skill, sem auditoria nem efeitos externos."""
        skill = self._find_skill(skill_name)
        if skill is None:
            return {"efeito": "deny", "rule_id": None, "message": "skill não encontrada"}
        derived = self._resolve_derived_facts(skill, arguments)
        clean = {k: v for k, v in arguments.items() if k not in skill.derived_facts}
        facts: dict[str, Any] = {
            "skill_name": skill_name,
            "actor": self._actor(),
            "actor_role": get_current_user().role.value,
            "origem_sistema": "mcp",
            **clean,
            **derived,
        }
        decision = evaluate(self._ruleset, facts)
        return {
            "efeito": decision.effect.value,
            "rule_id": decision.rule_id,
            "message": decision.message,
            "proximo_passo": decision.next_step,
            "fatos_derivados": derived,
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
        arguments = {k: v for k, v in arguments.items() if k not in skill.derived_facts}
        if trace is not None:
            trace["fatos_derivados"] = derived

        facts: dict[str, Any] = {
            "skill_name": skill_name,
            "actor": actor,
            "actor_role": get_current_user().role.value,
            "origem_sistema": "mcp",
            **arguments,
            **derived,
        }
        rule_decision = evaluate(self._ruleset, facts)

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

        if rule_decision.effect is RuleEffect.REQUIRE_APPROVAL:
            self._record_audit(
                actor, skill_name, AuditDecision.PENDING_APPROVAL, rule_decision.rule_id
            )
            return {
                "allowed": False,
                "reason": "human approval required",
                "rule_id": rule_decision.rule_id,
                "message": rule_decision.message,
                "proximo_passo": rule_decision.next_step,
                "result": "",
                "payload_digest": None,
            }

        url = _render_url(skill.url_template, arguments)
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
                self._record_audit(actor, skill_name, AuditDecision.DENIED, rule_decision.rule_id)
                return {
                    "allowed": False,
                    "reason": f"falha ao enviar e-mail: {exc}",
                    "result": "",
                    "payload_digest": None,
                }
            response_text = json.dumps(send_result, sort_keys=True)
        else:
            response_text = mock_response

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
        return response

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
                "proximo_passo": "Peça a um psicólogo ou à responsável técnica.",
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
            self._document_agent = build_document_agent()
        return self._document_agent

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
        )

    def vault_promote_draft(self, draft_id: str, kind: str) -> dict[str, Any]:
        return promote_draft(
            self.client_id,
            draft_id,
            kind,
            self._draft_store,
            self._rule_provider,
            self._skill_provider,
            self._audit_sink,
        )

    def consultar_processo_datajud(self, numero_processo: str) -> dict[str, Any]:
        actor = DEFAULT_ACTOR
        try:
            result = consultar_processo(numero_processo)
        except DatajudError as exc:
            self._record_audit(actor, "consultar_processo_datajud", AuditDecision.DENIED)
            return {"allowed": False, "reason": str(exc)}

        self._record_audit(actor, "consultar_processo_datajud", AuditDecision.ALLOWED)

        note_id = None
        if result.get("encontrado"):
            try:
                note = self._vault_provider.add_note(
                    self.client_id,
                    title=f"Processo {result['numero_processo']}",
                    content=_render_processo_note(result),
                    frontmatter={
                        "tags": ["processo", "datajud"],
                        "numero_processo": result.get("numero_processo"),
                        "tribunal": result.get("tribunal"),
                        "classe": (result.get("classe") or {}).get("nome"),
                        "orgao_julgador": (result.get("orgao_julgador") or {}).get("nome"),
                        "grau": result.get("grau"),
                        "consultado_em": datetime.now(UTC).isoformat(),
                    },
                )
                note_id = note.note_id
            except Exception:
                # Consulta já teve sucesso e foi retornada; falha ao gravar no
                # vault não deve mascarar o resultado real da consulta.
                note_id = None

        return {"allowed": True, "reason": None, "vault_note_id": note_id, **result}

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
                    f"Acesso negado: perfil '{user.role.value}' não pode "
                    "executar 'deletar_banco'."
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
    return " ".join(lines)


def build_server(engine: BRMEngine) -> RBACFastMCP:
    server = RBACFastMCP(
        "brm-engine",
        instructions=_server_instructions(engine),
        audit_sink=engine._audit_sink,
        client_id=engine.client_id,
        user_store=engine.user_store,
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
        """Consulta a trilha de auditoria (hash-chained, append-only) do escritório:
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
    def vault_add_note(
        title: str, content: str, tags: list[str] | None = None
    ) -> dict[str, Any]:
        """Cria uma nova nota markdown no vault jurídico do escritório."""
        return engine.vault_add_note(title, content, tags)

    @server.tool()
    def vault_get_related(note_id: str) -> dict[str, Any]:
        """Lista as notas do vault linkadas (backlinks) a uma nota específica."""
        return engine.vault_get_related(note_id)

    @server.tool()
    def vault_list_notes() -> dict[str, Any]:
        """Lista todas as notas do vault jurídico do escritório, sem filtro
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
        escritório, com status (pending/approved/rejected). kind opcional:
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
        """Redige um documento jurídico padronizado (modelo de petição,
        contestação, contrato, checklist etc.) a partir de uma instrução em
        português, e salva como draft pendente — nunca escreve direto no vault.
        Ex.: "gere um modelo de notificação extrajudicial de cobrança"."""
        return engine.vault_generate_document(instrucoes)

    @server.tool()
    def vault_promote_document(draft_id: str) -> dict[str, Any]:
        """Promove um draft de documento pendente (id de vault_list_drafts,
        kind="document") para o vault de verdade — só depois de aprovação
        humana explícita e nomeada."""
        return engine.vault_promote_document(draft_id)

    @server.tool()
    def consultar_processo_datajud(numero_processo: str) -> dict[str, Any]:
        """Consulta o andamento de um processo judicial em tempo real na API
        pública do CNJ (DataJud). Identifica automaticamente o tribunal a partir
        do número único do processo (ex.: 0000832-35.2018.4.01.3202), retorna
        classe, órgão julgador, grau, assuntos e movimentações, e grava uma nota
        estruturada no vault jurídico (vault_note_id na resposta)."""
        return engine.consultar_processo_datajud(numero_processo)

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
        """Cria um usuário no banco de perfis do escritório (admin, viewer,
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
    def atualizar_permissao(role: str, tool_name: str, allowed: bool) -> dict[str, Any]:
        """Liga ou desliga o acesso de um perfil (admin, viewer) a uma tool
        específica — em runtime, sem precisar de deploy. Cobre qualquer
        tool registrada no servidor, inclusive as hoje livres a todo mundo
        (call_skill, vault_*, consultar_processo_datajud). Restrita ao
        perfil super_admin, que sempre tem acesso irrestrito por conta
        própria e não precisa de entrada aqui."""
        return engine.atualizar_permissao(role, tool_name, allowed)

    existing = {t.name for t in server._tool_manager.list_tools()}
    for skill in engine.skills:
        if skill.name in existing:
            continue
        server.add_tool(
            _make_skill_tool(engine, skill),
            name=skill.name,
            description=_skill_tool_description(skill),
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
    set_current_user(resolve_stdio_identity(engine.user_store))

    server = build_server(engine)
    server.run()


if __name__ == "__main__":
    main()
