from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

_SUFIXOS = {".db", ".sqlite", ".sqlite3"}


def listar_bancos(diretorios: Iterable[Path]) -> list[Path]:
    achados: dict[Path, None] = {}
    for diretorio in diretorios:
        base = Path(diretorio)
        if not base.is_dir():
            continue
        for caminho in sorted(base.iterdir()):
            if caminho.is_file() and caminho.suffix in _SUFIXOS:
                achados[caminho.resolve()] = None
    return list(achados)


def checar_banco(caminho: Path) -> str:
    try:
        conn = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True, timeout=5)
        try:
            linhas = [r[0] for r in conn.execute("PRAGMA integrity_check")]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return f"erro: {exc}"
    return "ok" if linhas == ["ok"] else "; ".join(str(x) for x in linhas[:3])


def verificar_integridade(diretorios: Iterable[Path]) -> dict[str, str]:
    """Confere todos os bancos, inclusive quando diretórios distintos usam o mesmo nome.

    A resposta de ``/health`` deliberadamente não expõe caminhos do cliente. Ainda assim,
    indexar só por ``Path.name`` fazia um banco homônimo sobrescrever o outro no dicionário e
    podia mascarar uma corrupção. Sufixos ordinal mantém o diagnóstico estável e não revela
    o caminho.
    """
    resultado: dict[str, str] = {}
    for caminho in listar_bancos(diretorios):
        nome = caminho.name
        chave = nome
        indice = 2
        while chave in resultado:
            chave = f"{nome} ({indice})"
            indice += 1
        resultado[chave] = checar_banco(caminho)
    return resultado


def tudo_ok(resultado: dict[str, str]) -> bool:
    return all(v == "ok" for v in resultado.values())
