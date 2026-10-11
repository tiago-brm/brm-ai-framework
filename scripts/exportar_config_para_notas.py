"""Gera notas de configuração (spec 030) a partir de rules.yaml e skills.yaml.

PYTHONPATH=. uv run python scripts/exportar_config_para_notas.py <cliente> [--dry-run]

Não altera os arquivos atuais nem sobrescreve notas existentes. Depois de
exportar, ative `config_via_notas: true` no client_config e aprove cada nota
(config_propor e aprovar_operacao): enquanto não aprovadas, as regras e skills
exportadas não valem no modo por notas.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

from mcp_engine.core.config_loader import FileSystemClientConfigProvider
from mcp_engine.core.config_notas import id_valido
from mcp_engine.core.models import Rule

PASSO_ORDEM = 10


def _nota(tipo: str, config_id: str, ordem: int | None, prosa: str, bloco: dict[str, Any]) -> str:
    cab: dict[str, Any] = {"tipo": tipo, "id": config_id}
    if ordem is not None:
        cab["ordem"] = ordem
    return (
        f"---\n{yaml.safe_dump(cab, sort_keys=False, allow_unicode=True)}---\n"
        f"{prosa.strip()}\n\n```brm-config\n"
        f"{yaml.safe_dump(bloco, sort_keys=True, allow_unicode=True)}```\n"
    )


def montar_notas(config_dir: Path) -> tuple[dict[str, str], list[str]]:
    notas: dict[str, str] = {}
    avisos: list[str] = []
    regras = yaml.safe_load((config_dir / "rules.yaml").read_text()) or {}
    for i, bruta in enumerate(regras.get("rules", []), start=1):
        regra = Rule.model_validate(bruta)
        if not id_valido(regra.id):
            avisos.append(f"regra {regra.id!r}: id fora do padrão (minúsculas, '-' e '_'); pulada")
            continue
        bloco = regra.model_dump(mode="json", exclude_none=True)
        notas[f"regras/{regra.id}.md"] = _nota(
            "regra", regra.id, i * PASSO_ORDEM, regra.description, bloco
        )
    skills = yaml.safe_load((config_dir / "skills.yaml").read_text()) or {}
    for entrada in skills.get("skills", []):
        nome = str(entrada["usar"]).partition(".")[2] if "usar" in entrada else entrada["name"]
        if not id_valido(nome):
            avisos.append(f"skill {nome!r}: id fora do padrão; pulada")
            continue
        descricao = entrada.get("description") or f"Skill {nome}."
        notas[f"skills/{nome}.md"] = _nota("skill", nome, None, descricao, dict(entrada))
    return notas, avisos


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cliente")
    ap.add_argument("--config-root", default="clients")
    ap.add_argument(
        "--vault-root", help="padrão: vault_root do cliente ou <config-root>/<cliente>/vault"
    )
    ap.add_argument("--destino", default="configuracao", help="subpasta do vault")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    raiz = Path(args.config_root)
    cfg = FileSystemClientConfigProvider(raiz).get_client_config(args.cliente)
    vault = Path(
        args.vault_root
        or (Path(cfg.vault_root).expanduser() if cfg.vault_root else raiz / args.cliente / "vault")
    )
    notas, avisos = montar_notas(raiz / args.cliente / "config")
    criadas = puladas = 0
    for rel, texto in notas.items():
        destino = vault / args.destino / rel
        if destino.exists():
            puladas += 1
            print(f"existe, mantida: {destino}")
            continue
        criadas += 1
        print(f"{'criaria' if args.dry_run else 'criada'}: {destino}")
        if not args.dry_run:
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text(texto, encoding="utf-8")
    for aviso in avisos:
        print(f"aviso: {aviso}")
    print(f"{criadas} nota(s), {puladas} já existiam, {len(avisos)} aviso(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
