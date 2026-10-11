"""Conexão, transação, relógio e erros do ledger financeiro."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mcp_engine.core.executor import ExecucaoFalhou

FUSO = ZoneInfo("America/Sao_Paulo")
LEDGER_ARQUIVO = "financeiro.sqlite"

_MENSAGENS = {
    "imutavel": "Registro imutável: correções são feitas por estorno e novo lançamento.",
    "mes_fechado": (
        "Mês fechado: o lançamento é recusado. Faça o ajuste no mês corrente, "
        "com referência ao original."
    ),
    "estorno_acima_do_saldo": "O estorno excede o saldo ainda estornável do lançamento original.",
    "estorno_original_invalido": "Lançamento original inválido para estorno.",
    "repasse_incoerente": "Repasse incoerente com o lançamento.",
}


class ErroFinanceiro(ExecucaoFalhou):
    pass


def caminho_ledger(data_root: Path | str, client_id: str) -> Path:
    return Path(data_root) / client_id / LEDGER_ARQUIVO


def abrir(path: Path | str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def hoje() -> date:
    return datetime.now(FUSO).date()


def agora_iso() -> str:
    return datetime.now(FUSO).isoformat(timespec="seconds")


@contextmanager
def transacao(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except sqlite3.IntegrityError as exc:
        con.execute("ROLLBACK")
        raise traduzir(exc) from None
    except BaseException:
        con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")


def traduzir(exc: sqlite3.IntegrityError) -> ErroFinanceiro:
    texto = str(exc)
    for codigo, mensagem in _MENSAGENS.items():
        if codigo in texto:
            return ErroFinanceiro(mensagem)
    return ErroFinanceiro("Dado recusado pelo banco do ledger.")


def normalizar_nome(nome: str) -> str:
    base = unicodedata.normalize("NFKD", nome)
    base = "".join(c for c in base if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", base.lower())).strip()


def parse_data(valor: object) -> date:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor).strip()
    for padrao, formato in (
        (r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"),
        (r"\d{1,2}/\d{1,2}/\d{4}", "%d/%m/%Y"),
    ):
        if re.fullmatch(padrao, texto):
            try:
                return datetime.strptime(texto, formato).date()
            except ValueError:
                break
    raise ErroFinanceiro(f"Data inválida: {texto!r} (use AAAA-MM-DD ou DD/MM/AAAA).")


def parse_mes(valor: str) -> str:
    texto = str(valor).strip()
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", texto):
        raise ErroFinanceiro(f"Mês inválido: {texto!r} (use AAAA-MM).")
    return texto


def mes_de(d: date | str) -> str:
    return d.strftime("%Y-%m") if isinstance(d, date) else str(d)[:7]


def preparar_banco(data_root: Path | str, client_id: str) -> Path:
    """Cria e migra o banco do ledger fora do motor (CLI e testes); idempotente."""
    from mcp_engine.modules import ModuleHost, _migrate
    from mcp_engine.modules.financeiro import MODULE

    host = ModuleHost(
        module="financeiro",
        client_id=client_id,
        data_root=Path(data_root),
        handlers=None,  # type: ignore[arg-type]
        secrets=None,  # type: ignore[arg-type]
        add_vault_note=lambda *a, **k: None,
        audit=lambda *a, **k: None,
    )
    _migrate(host, MODULE.migrations)
    return caminho_ledger(data_root, client_id)
