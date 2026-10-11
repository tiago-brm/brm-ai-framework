"""Cria `clients/<id>` a partir de `clients/_template` (spec 027).

uv run python scripts/novo_cliente.py <id> --modulos resend,datajud
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from mcp_engine.core.config_loader import (
    FileSystemClientConfigProvider,
    FileSystemRuleProvider,
    FileSystemSkillProvider,
    FileSystemVaultAccessProvider,
)
from mcp_engine.core.user_store import SQLUserStore
from mcp_engine.modules import get_catalog_module, load_for_client

_ID = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
TEMPLATE = Path(__file__).resolve().parent.parent / "clients" / "_template"


def _read(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def _write(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def _aplicar_defaults(config_dir: Path, modulos: list[str]) -> None:
    skills = _read(config_dir / "skills.yaml")
    rules = _read(config_dir / "rules.yaml")
    for nome in modulos:
        defaults = get_catalog_module(nome).defaults_dir
        if defaults is None:
            continue
        if (defaults / "skills.yaml").is_file():
            skills["skills"].extend(_read(defaults / "skills.yaml").get("skills", []))
        if (defaults / "rules.yaml").is_file():
            rules["rules"].extend(_read(defaults / "rules.yaml").get("rules", []))
    _write(config_dir / "skills.yaml", skills)
    _write(config_dir / "rules.yaml", rules)


def validar(client_id: str, config_root: Path) -> None:
    config = FileSystemClientConfigProvider(config_root).get_client_config(client_id)
    FileSystemRuleProvider(config_root).get_rules(client_id)
    skills = FileSystemSkillProvider(config_root)
    skills.modules = load_for_client(
        client_id,
        config_root,
        config.modules,
        config.modulos_privados,
        config.domain_allowlist,
    )
    skills.domain_allowlist = config.domain_allowlist
    skills.get_skills(client_id)
    FileSystemVaultAccessProvider(config_root).get_vault_access(client_id)


def criar_cliente(client_id: str, modulos: list[str], config_root: Path) -> Path:
    if not _ID.match(client_id) or client_id.startswith("_"):
        raise ValueError(f"id de cliente inválido: {client_id!r}")
    destino = Path(config_root) / client_id
    if destino.exists():
        raise FileExistsError(f"cliente já existe: {destino}")
    for nome in modulos:
        get_catalog_module(nome)

    shutil.copytree(TEMPLATE, destino)
    try:
        config_dir = destino / "config"
        for arquivo in config_dir.glob("*.yaml"):
            texto = arquivo.read_text()
            texto = texto.replace("__CLIENT_ID__", client_id)
            texto = texto.replace("__VAULT_ROOT__", str(destino / "vault"))
            arquivo.write_text(texto)

        config = _read(config_dir / "client_config.yaml")
        config["modules"] = list(modulos)
        for nome in modulos:
            for dominio in get_catalog_module(nome).manifest.dominios_requeridos:
                if dominio not in config["domain_allowlist"]:
                    config["domain_allowlist"].append(dominio)
        _write(config_dir / "client_config.yaml", config)
        _aplicar_defaults(config_dir, modulos)

        validar(client_id, Path(config_root))
        SQLUserStore(Path(config_root), client_id)
    except Exception:
        quarentena = Path(tempfile.mkdtemp(prefix=f"brm-novo-cliente-{client_id}-"))
        shutil.move(str(destino), str(quarentena / client_id))
        raise
    return destino


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cria um cliente a partir do template.")
    parser.add_argument("client_id")
    parser.add_argument("--modulos", default="", help="módulos do catálogo, separados por vírgula")
    parser.add_argument("--destino", default="clients", type=Path)
    args = parser.parse_args(argv)
    modulos = [m for m in args.modulos.split(",") if m]
    try:
        destino = criar_cliente(args.client_id, modulos, args.destino)
    except Exception as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1
    print(f"cliente criado em {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
