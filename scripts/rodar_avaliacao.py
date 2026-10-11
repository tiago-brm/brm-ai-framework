#!/usr/bin/env python3
"""Roda a avaliação de intenção (spec 029) e grava o relatório em evals/runs/.

    python scripts/rodar_avaliacao.py --backend falso
    EVAL_LIVE=1 ANTHROPIC_API_KEY=... EVAL_MODEL=... \\
        python scripts/rodar_avaliacao.py --backend anthropic
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from evals.ambiente import montar  # noqa: E402
from evals.runner import (  # noqa: E402
    ModeloAnthropic,
    ModeloFalso,
    carregar_casos,
    executar,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", choices=["falso", "anthropic"], default="falso")
    parser.add_argument("--modulo", help="roda só os casos deste módulo")
    parser.add_argument("--modelo", help="modelo do backend real (padrão: EVAL_MODEL)")
    parser.add_argument("--saida", type=Path, default=RAIZ / "evals" / "runs")
    args = parser.parse_args(argv)

    casos = carregar_casos(modulo=args.modulo)
    if not casos:
        print("nenhum caso encontrado", file=sys.stderr)
        return 2

    try:
        modelo = ModeloFalso() if args.backend == "falso" else ModeloAnthropic(args.modelo)
    except RuntimeError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        ambiente = montar(Path(tmp))
        relatorio = executar(modelo, ambiente, casos, args.saida)

    print(json.dumps({"modelo": relatorio["modelo"], **relatorio["metricas"]}, ensure_ascii=False))
    print(json.dumps(relatorio["aceite"], ensure_ascii=False))
    print(f"relatório: {relatorio['arquivo']}")
    return 0 if relatorio["aceite"]["aprovado"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
