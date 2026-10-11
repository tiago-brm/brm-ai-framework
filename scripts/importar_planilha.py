"""Importa uma planilha .xlsx para o ledger financeiro (spec 024).

Dry-run por padrão; `--aplicar` grava. Sai com código 1 se a conciliação divergir.

    PYTHONPATH=. uv run python scripts/importar_planilha.py planilha.xlsx --cliente clinica-piloto
    PYTHONPATH=. uv run python scripts/importar_planilha.py planilha.xlsx \
        --cliente clinica-piloto --aplicar
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp_engine.modules.financeiro.db import ErroFinanceiro, hoje, preparar_banco
from mcp_engine.modules.financeiro.importers import importar, opcoes_padrao, reverter_lote
from mcp_engine.modules.financeiro.lancamentos import Quem
from mcp_engine.modules.financeiro.money import formatar_brl


def _texto(r: dict[str, Any]) -> str:
    n = r["linhas"]
    saida = [
        f"Importação {r['modo'].upper()} de {r['arquivo']} ({r['layout']}, {r['mes']}, "
        f"{r['unidade'] or '-'})",
        f"Linhas: {n['lidas']} lidas, {n['inseridas']} inseridas, {n['duplicadas']} duplicadas, "
        f"{n['pendentes']} pendentes, {n['ignoradas']} ignoradas.",
        "",
        f"{'mês':8} {'forma/grupo':14} {'unidade':14} {'planilha':>14} {'ledger':>14} "
        f"{'pendente':>14} {'diferença':>12}",
    ]
    for b in r["conciliacao"]:
        saida.append(
            f"{b['mes']:8} {b['forma']:14} {b['unidade']:14} "
            f"{formatar_brl(b['planilha_centavos']):>14} {formatar_brl(b['ledger_centavos']):>14} "
            f"{formatar_brl(b['pendente_centavos']):>14}"
            f" {formatar_brl(b['diferenca_centavos']):>12}"
        )
    if r["pendencias_por_motivo"]:
        saida += ["", "Pendências por motivo:"]
        saida += [f"  {k}: {v}" for k, v in r["pendencias_por_motivo"].items()]
    if r["avisos"]:
        saida += ["", "Avisos:"] + [f"  - {a}" for a in r["avisos"]]
    saida += ["", "CONCILIAÇÃO OK" if r["ok"] else "FALHA: conciliação diverge da planilha"]
    return "\n".join(saida)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Importa planilha .xlsx para o ledger financeiro.")
    p.add_argument("arquivo", type=Path, nargs="?")
    p.add_argument("--reverter-lote", type=int, help="estorna tudo o que o lote importou")
    p.add_argument("--cliente", default=os.getenv("CLIENT_ID"))
    p.add_argument("--data-root", type=Path, default=Path(os.getenv("DATA_ROOT", "data")))
    p.add_argument("--config-root", type=Path, default=Path(os.getenv("CONFIG_ROOT", "clients")))
    p.add_argument("--aplicar", action="store_true", help="grava no ledger (padrão: dry-run)")
    p.add_argument("--mes", help="AAAA-MM, quando a planilha não traz o mês")
    p.add_argument("--layout", choices=["secretarias", "financeiro"])
    p.add_argument("--por", default=f"importacao:{getpass.getuser()}")
    p.add_argument("--exigir-sem-pendencia", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    if not args.cliente:
        print("erro: informe --cliente ou CLIENT_ID", file=sys.stderr)
        return 2
    if args.arquivo is None and args.reverter_lote is None:
        print("erro: informe o arquivo ou --reverter-lote", file=sys.stderr)
        return 2
    try:
        banco = preparar_banco(args.data_root, args.cliente)
        if args.reverter_lote is not None:
            res = reverter_lote(banco, args.reverter_lote, Quem(args.por, "admin"), hoje=hoje())
            print(json.dumps(res, ensure_ascii=False))
            return 0
        opcoes = opcoes_padrao(
            Quem(args.por, "admin"),
            args.config_root / args.cliente / "config",
            aplicar=args.aplicar,
            exigir_sem_pendencia=args.exigir_sem_pendencia,
        )
        relatorio = importar(banco, args.arquivo, opcoes, mes=args.mes, layout=args.layout)
    except ErroFinanceiro as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(relatorio, ensure_ascii=False, indent=2) if args.json else _texto(relatorio))
    return 0 if relatorio["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
