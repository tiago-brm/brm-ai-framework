"""Verificação "pré-voo" das demonstrações. Rode 15 minutos antes da reunião.

Executa as duas demos sem pausas e confere se terminam sem erro e com os
resultados esperados (permitidas, bloqueadas, em aprovação). Se algum número
mudar de propósito (regras novas), atualize ESPERADO.

Uso: uv run python scripts/preflight_demo.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANSI = re.compile(r"\x1b\[[0-9;]*m")

ESPERADO = {
    "demo_clinica.py": {"✓": 10, "✕": 9, "⏸": 5},
    "demo_casos_de_uso.py": {"✓": 5, "✕": 2, "⏸": 3},
}


def rodar(script: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), "--auto"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return proc.returncode, ANSI.sub("", proc.stdout + proc.stderr)


def main() -> int:
    falhas = 0
    for script, esperado in ESPERADO.items():
        codigo, saida = rodar(script)
        linhas = [ln for ln in saida.splitlines() if ln[:1] in esperado]
        contagem = {k: sum(1 for ln in linhas if ln.startswith(k)) for k in esperado}
        erro = codigo != 0 or "Traceback" in saida
        ok = not erro and contagem == esperado
        falhas += 0 if ok else 1
        marca = "OK    " if ok else "FALHOU"
        print(f"[{marca}] {script}: {contagem} (esperado {esperado})")
        if erro:
            print("        erro na execução; rode o script sozinho para ver o motivo")

    print()
    if falhas:
        print("Pré-voo com falhas. Não apresente antes de resolver.")
        return 1
    print("Pré-voo OK. Lembretes: fonte do terminal grande, notificações desligadas,")
    print("e abra a apresentação e o roteiro em abas separadas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
