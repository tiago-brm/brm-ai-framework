"""Verifica um backup restaurado: integridade dos bancos e cadeia de hash da auditoria.

Uso: python scripts/verificar_restauracao.py <diretorio-restaurado> [cliente]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit.sink import AuditChainError, FileSystemAuditSink  # noqa: E402
from mcp_engine.core.health import checar_banco  # noqa: E402


def verificar(restaurado: Path, cliente: str | None = None) -> list[str]:
    pacotes = [p for p in restaurado.iterdir() if p.is_dir()]
    if cliente:
        pacotes = [p for p in pacotes if p.name == cliente]
    if len(pacotes) != 1:
        return [f"esperado exatamente um pacote de cliente em {restaurado}"]
    pacote = pacotes[0]
    dados = pacote / "dados"
    falhas: list[str] = []

    bancos = sorted(
        p for p in dados.iterdir() if p.suffix in {".db", ".sqlite", ".sqlite3"}
    )
    if not bancos:
        falhas.append("nenhum banco encontrado no backup")
    for banco in bancos:
        resultado = checar_banco(banco)
        print(f"banco {banco.name}: {resultado}")
        if resultado != "ok":
            falhas.append(f"integridade de {banco.name}: {resultado}")

    if (dados / "audit.jsonl").exists():
        try:
            eventos = sum(1 for _ in FileSystemAuditSink(pacote).iter_events("dados"))
            print(f"auditoria: cadeia integra ({eventos} eventos)")
        except AuditChainError as exc:
            falhas.append(str(exc))
    else:
        print("auditoria: sem audit.jsonl no backup")
    return falhas


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    falhas = verificar(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else None)
    if falhas:
        for f in falhas:
            print(f"FALHA: {f}", file=sys.stderr)
        return 1
    print("verificacao ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
