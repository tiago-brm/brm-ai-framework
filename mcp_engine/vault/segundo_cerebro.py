"""Segundo cérebro (spec 025): conhecimento revisado, memória e relatórios.

Memória é dado: entra por rascunho, é promovida por um gestor e nunca vira
regra. Relatórios saem de geradores determinísticos registrados por módulos;
o texto é um template fixo e os números vêm do módulo, nunca do LLM.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from string import Template
from typing import Any

from mcp_engine.core.auth import Role, UserProfile
from mcp_engine.core.config_notas import escrever_atomico
from mcp_engine.core.contracts import AuditSink
from mcp_engine.core.interceptor import scan_request
from mcp_engine.core.models import AuditDecision, AuditEvent, ClientConfig
from mcp_engine.vault.draft_store import DraftStore
from mcp_engine.vault.escrita_motor import EscritorMotor
from mcp_engine.vault.markdown import serialize_note

CAMPOS_REVISAO = ("fonte", "revisado_por", "revisado_em")
DIAS_REVISAO = 365
AREAS_REGULADAS = frozenset({"fiscal", "saude", "trabalhista", "juridica"})
MEMORIA_MAX_CHARS = 2000
MEMORIA_MAX_NOTAS = 200
_PERIODO = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_UNIDADE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_SLUG = re.compile(r"[^a-z0-9]+")
GESTORES = (Role.ADMIN, Role.SUPER_ADMIN)


def _como_data(valor: Any) -> date | None:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def avaliar_conhecimento(cab: Mapping[str, Any], hoje: date) -> tuple[str, str]:
    """Estado de uma nota de conhecimento: `ok`, `incompleto`, `vencida` ou `bloqueado`."""
    faltando = [c for c in CAMPOS_REVISAO if not cab.get(c)]
    if faltando:
        regulada = str(cab.get("area", "")).lower() in AREAS_REGULADAS
        if regulada and "revisado_por" in faltando:
            return "bloqueado", "área regulada sem revisor nomeado"
        return "incompleto", f"frontmatter sem: {', '.join(faltando)}"
    revisado = _como_data(cab["revisado_em"])
    if revisado is None:
        return "incompleto", "revisado_em não é uma data"
    if (hoje - revisado).days > DIAS_REVISAO:
        return "vencida", f"revisada em {revisado.isoformat()} (mais de 12 meses)"
    return "ok", ""


@dataclass(frozen=True)
class Gerador:
    nome: str
    descricao: str
    dados: Callable[[str, str | None], Mapping[str, Any]]
    template: str


def renderizar(gerador: Gerador, periodo: str, unidade: str | None) -> str:
    dados = {k: str(v) for k, v in gerador.dados(periodo, unidade).items()}
    return Template(gerador.template).substitute(dados)


def _slug(texto: str) -> str:
    return _SLUG.sub("-", texto.lower()).strip("-")[:48] or "nota"


class SegundoCerebro:
    def __init__(
        self,
        *,
        client_id: str,
        vault_root: Path,
        drafts: DraftStore,
        audit: AuditSink,
        client_config: ClientConfig,
        extensoes: Any,
        escritor: EscritorMotor,
        reindex: Callable[[], Any],
    ) -> None:
        self._client_id = client_id
        self._root = Path(vault_root)
        self._drafts = drafts
        self._audit = audit
        self._cfg = client_config
        self._ext = extensoes
        self._escritor = escritor
        self._reindex = reindex

    def _auditar(self, ator: str, action: str, decisao: AuditDecision, **details: Any) -> None:
        self._audit.record(
            AuditEvent(
                client_id=self._client_id,
                occurred_at=datetime.now(UTC),
                actor=ator,
                action=action,
                decision=decisao,
                details=details,
            )
        )

    @staticmethod
    def _negado(motivo: str) -> dict[str, Any]:
        return {"allowed": False, "reason": motivo}

    def memoria_registrar(
        self,
        user: UserProfile,
        titulo: str,
        conteudo: str,
        categoria: str = "geral",
        expira_em: str | None = None,
    ) -> dict[str, Any]:
        if not titulo.strip() or len(titulo) > 120:
            return self._negado("título vazio ou maior que 120 caracteres")
        if not conteudo.strip() or len(conteudo) > MEMORIA_MAX_CHARS:
            return self._negado(f"conteúdo vazio ou maior que {MEMORIA_MAX_CHARS} caracteres")
        if "brm-config" in conteudo or "brm-config" in titulo:
            return self._negado("memória é dado e não pode conter blocos de configuração")
        if expira_em is not None and _como_data(expira_em) is None:
            return self._negado("expira_em deve ser uma data ISO")
        scan = scan_request({"titulo": titulo, "conteudo": conteudo}, self._cfg, user.role)
        if not scan.allowed:
            self._auditar(
                user.identity, "memoria_registrar", AuditDecision.DENIED, dlp=scan.dlp_matches
            )
            return self._negado(scan.reason or "bloqueado pelo DLP")
        draft_id = _slug(titulo)[:20] + "-" + datetime.now(UTC).strftime("%H%M%S%f")[:10]
        conteudo_draft = {
            "titulo": scan.arguments["titulo"],
            "conteudo": scan.arguments["conteudo"],
            "categoria": _slug(categoria),
            "expira_em": expira_em,
        }
        self._drafts.create(
            self._client_id,
            "memoria",
            draft_id,
            from_note_id="",
            generated_at=datetime.now(UTC).isoformat(),
            generated_by=user.identity,
            content=conteudo_draft,
        )
        self._auditar(user.identity, "memoria_registrar", AuditDecision.ALLOWED, draft_id=draft_id)
        return {"allowed": True, "draft_id": draft_id, "status": "pending"}

    def memoria_rascunhos(
        self, user: UserProfile, status: str | None = "pending"
    ) -> dict[str, Any]:
        if user.role not in GESTORES:
            return self._negado("Somente gestor lista os rascunhos de memória.")
        itens = self._drafts.list_drafts(self._client_id, "memoria", status)
        return {"total": len(itens), "rascunhos": itens}

    def memoria_promover(self, user: UserProfile, draft_id: str) -> dict[str, Any]:
        if user.role not in GESTORES:
            self._auditar(
                user.identity, "memoria_promover", AuditDecision.DENIED, draft_id=draft_id
            )
            return self._negado("Somente gestor promove memória.")
        draft = self._drafts.get_draft(self._client_id, "memoria", draft_id)
        if draft is None or draft["status"] != "pending":
            return self._negado("rascunho de memória inexistente ou já tratado")
        pasta = self._root / "memoria"
        if len(list(pasta.glob("*.md"))) >= MEMORIA_MAX_NOTAS:
            return self._negado(f"limite de {MEMORIA_MAX_NOTAS} memórias; revise e expire antigas")
        c = draft["content"]
        hoje = date.today().isoformat()
        cab: dict[str, Any] = {
            "tipo": "memoria",
            "zona": "memoria",
            "categoria": c["categoria"],
            "registrado_por": draft["generated_by"],
            "promovido_por": user.identity,
            "promovido_em": hoje,
            "fonte": "memoria_registrar",
            "revisado_por": user.identity,
            "revisado_em": hoje,
            "draft_id": draft_id,
        }
        if c.get("expira_em"):
            cab["expira_em"] = str(c["expira_em"])
        rel = f"memoria/{_slug(c['titulo'])}-{draft_id}.md"
        escrever_atomico(self._root / rel, serialize_note(c["titulo"], c["conteudo"], cab))
        self._drafts.mark_status(
            self._client_id, "memoria", draft_id, "approved", user.identity,
            datetime.now(UTC).isoformat(),
        )  # fmt: skip
        self._reindex()
        self._auditar(
            user.identity, "memoria_promover", AuditDecision.ALLOWED, draft_id=draft_id, nota=rel
        )
        return {"allowed": True, "nota": rel}

    def gerar_relatorio_mensal(
        self,
        user: UserProfile,
        periodo: str,
        unidade: str | None = None,
        gerador: str | None = None,
    ) -> dict[str, Any]:
        if user.role not in GESTORES:
            return self._negado("Somente gestor gera relatórios.")
        if not _PERIODO.match(periodo):
            return self._negado("periodo deve ser AAAA-MM")
        if unidade is not None and not _UNIDADE.match(unidade):
            return self._negado("unidade inválida")
        geradores = self._ext.geradores
        if gerador is None:
            if len(geradores) != 1:
                return self._negado(
                    f"informe o gerador: {', '.join(sorted(geradores)) or 'nenhum'}"
                )
            gerador = next(iter(geradores))
        ger = geradores.get(gerador)
        if ger is None:
            return self._negado(f"gerador desconhecido: {gerador!r}")
        try:
            corpo = renderizar(ger, periodo, unidade)
        except Exception as exc:
            self._auditar(user.identity, "relatorio_gerado", AuditDecision.DENIED, erro=str(exc))
            return self._negado(f"falha ao gerar o relatório: {exc}")
        sufixo = f"-{unidade}" if unidade else ""
        rel = f"relatorios/{ger.nome}-{periodo}{sufixo}.md"
        estado, hash_ = self._escritor.escrever(
            rel,
            f"Relatório {ger.nome} {periodo}",
            corpo,
            {
                "tipo": "relatorio",
                "zona": "relatorios",
                "gerador": ger.nome,
                "periodo": periodo,
                "unidade": unidade or "todas",
                "gerado": True,
                "somente_leitura": True,
            },
        )
        self._reindex()
        self._auditar(
            user.identity, "relatorio_gerado", AuditDecision.ALLOWED,
            caminho=rel, hash=hash_, estado=estado,
        )  # fmt: skip
        return {"allowed": True, "nota": rel, "hash": hash_, "estado": estado}
