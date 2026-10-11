import importlib.util
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from audit.sink import FileSystemAuditSink
from mcp_engine.core.models import AuditDecision, AuditEvent

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "verificar_restauracao", ROOT / "scripts" / "verificar_restauracao.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _pacote(tmp_path: Path) -> Path:
    restaurado = tmp_path / "restaurado"
    dados = restaurado / "c1" / "dados"
    dados.mkdir(parents=True)
    conn = sqlite3.connect(dados / "state.db")
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.commit()
    conn.close()
    sink = FileSystemAuditSink(restaurado / "c1")
    for _ in range(3):
        sink.record(
            AuditEvent(
                client_id="dados",
                occurred_at=datetime.now(UTC),
                actor="claude",
                action="consultar_valor",
                decision=AuditDecision.ALLOWED,
            )
        )
    return restaurado


def test_backup_integro_passa(tmp_path: Path) -> None:
    assert _mod.verificar(_pacote(tmp_path), "c1") == []


def test_auditoria_adulterada_falha(tmp_path: Path) -> None:
    restaurado = _pacote(tmp_path)
    audit = restaurado / "c1" / "dados" / "audit.jsonl"
    linhas = audit.read_text().splitlines()
    linhas[1] = linhas[1].replace("consultar_valor", "outra_acao")
    audit.write_text("\n".join(linhas) + "\n")
    assert any("audit chain broken" in f for f in _mod.verificar(restaurado, "c1"))


def test_banco_corrompido_falha(tmp_path: Path) -> None:
    restaurado = _pacote(tmp_path)
    (restaurado / "c1" / "dados" / "state.db").write_bytes(b"nao e um sqlite" * 300)
    assert _mod.verificar(restaurado, "c1")
