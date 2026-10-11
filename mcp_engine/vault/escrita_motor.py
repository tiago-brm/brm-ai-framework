"""Escrita do motor em pastas somente leitura no editor (`sistema/`, `relatorios/`).

Cada página gerada tem o hash gravado em `state.db`. Se alguém a editar à mão,
a divergência é auditada e o motor restaura o conteúdo gerado.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from mcp_engine.core.config_notas import escrever_atomico, sha256
from mcp_engine.core.contracts import AuditSink
from mcp_engine.core.models import AuditDecision, AuditEvent
from mcp_engine.core.state import PaginaGeradaRow, StateStore
from mcp_engine.vault.markdown import serialize_note

PASTAS_DO_MOTOR = ("sistema", "relatorios")
ATOR = "motor"


class EscritorMotor:
    def __init__(
        self, state: StateStore, vault_root: Path, audit: AuditSink, client_id: str
    ) -> None:
        self._engine = state.engine
        self._root = Path(vault_root)
        self._audit = audit
        self._client_id = client_id

    def _auditar(self, action: str, decision: AuditDecision, **details: Any) -> None:
        self._audit.record(
            AuditEvent(
                client_id=self._client_id,
                occurred_at=datetime.now(UTC),
                actor=ATOR,
                action=action,
                decision=decision,
                details=details,
            )
        )

    def escrever(
        self, rel: str, titulo: str, corpo: str, frontmatter: dict[str, Any]
    ) -> tuple[str, str]:
        partes = Path(rel).parts
        if not partes or partes[0] not in PASTAS_DO_MOTOR or ".." in partes:
            raise ValueError(f"o motor só escreve em {PASTAS_DO_MOTOR}: {rel!r}")
        texto = serialize_note(titulo, corpo, frontmatter)
        novo = sha256(texto)
        destino = self._root / rel
        estado = "criado"
        with Session(self._engine) as s:
            row = s.get(PaginaGeradaRow, rel)
            if destino.is_file():
                atual = sha256(destino.read_text(encoding="utf-8"))
                if atual == novo:
                    estado = "igual"
                elif row is not None and atual != row.hash:
                    estado = "restaurado"
                    self._auditar(
                        "pagina_gerada_editada_a_mao",
                        AuditDecision.DENIED,
                        caminho=rel,
                        hash_esperado=row.hash,
                        hash_encontrado=atual,
                    )
                else:
                    estado = "atualizado"
            if estado != "igual":
                escrever_atomico(destino, texto)
            if row is None:
                s.add(PaginaGeradaRow(caminho=rel, hash=novo))
            elif row.hash != novo:
                row.hash = novo
                row.atualizado_em = datetime.now(UTC)
            s.commit()
        return estado, novo
