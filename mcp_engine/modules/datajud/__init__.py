"""Módulo `datajud`: consulta de processos na API pública do CNJ."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp_engine.modules import BaseModule, ModuleHost
from mcp_engine.modules.datajud.client import DatajudError, consultar_processo


def render_processo_note(result: dict[str, Any]) -> str:
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


class DatajudModule(BaseModule):
    def install(self, host: ModuleHost) -> None:
        def consultar_processo_datajud(numero_processo: str) -> dict[str, Any]:
            """Consulta o andamento de um processo judicial em tempo real na API
            pública do CNJ (DataJud). Identifica automaticamente o tribunal a partir
            do número único do processo (ex.: 0000832-35.2018.4.01.3202), retorna
            classe, órgão julgador, grau, assuntos e movimentações, e grava uma nota
            estruturada no vault (vault_note_id na resposta)."""
            try:
                result = consultar_processo(numero_processo)
            except DatajudError as exc:
                host.audit("consultar_processo_datajud", False)
                return {"allowed": False, "reason": str(exc)}

            host.audit("consultar_processo_datajud", True)

            note_id = None
            if result.get("encontrado"):
                try:
                    note_id = host.add_vault_note(
                        f"Processo {result['numero_processo']}",
                        render_processo_note(result),
                        {
                            "tags": ["processo", "datajud"],
                            "numero_processo": result.get("numero_processo"),
                            "tribunal": result.get("tribunal"),
                            "classe": (result.get("classe") or {}).get("nome"),
                            "orgao_julgador": (result.get("orgao_julgador") or {}).get("nome"),
                            "grau": result.get("grau"),
                            "consultado_em": datetime.now(UTC).isoformat(),
                        },
                    )
                except Exception:
                    note_id = None

            return {"allowed": True, "reason": None, "vault_note_id": note_id, **result}

        host.register_tool("consultar_processo_datajud", consultar_processo_datajud)


MODULE = DatajudModule(Path(__file__).parent)
