"""Sincroniza a Agenda com o ledger de um cliente, uma vez (spec 026), sem subir o motor.

CLIENT_ID=clinica-piloto uv run python scripts/sincronizar_agenda.py [--cliente ID]

Lê os segredos agenda_api_key e agenda_base_url do SecretStore do cliente. Somente leitura na
Agenda; idempotente. Saída em JSON; código 1 se a Agenda falhar.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from mcp_engine.core.config_loader import FileSystemClientConfigProvider
from mcp_engine.core.executor import HandlerRegistry, SecretStore
from mcp_engine.modules import (
    ModuleError,
    ModuleHost,
    NamespacedHandlers,
    _migrate,
    load_for_client,
)
from mcp_engine.modules.agenda import executar_sincronizacao

RAIZ = Path(__file__).resolve().parent.parent


def montar_host(client_id: str, config_root: Path, data_root: Path) -> ModuleHost:
    config = FileSystemClientConfigProvider(config_root).get_client_config(client_id)
    modulos = load_for_client(
        client_id,
        config_root,
        config.modules,
        config.modulos_privados,
        config.domain_allowlist,
    )
    if "agenda" not in modulos or "financeiro" not in modulos:
        raise ModuleError("o cliente precisa dos módulos 'agenda' e 'financeiro' habilitados")
    host = ModuleHost(
        module="agenda",
        client_id=client_id,
        data_root=data_root,
        handlers=NamespacedHandlers("agenda", HandlerRegistry()),
        secrets=SecretStore().for_client(client_id),
        add_vault_note=lambda *a, **k: None,
        audit=lambda nome, ok: print(f"auditoria: {nome} ok={ok}", file=sys.stderr),
        modulos=modulos,
    )
    ledger = ModuleHost(**{**host.__dict__, "module": "financeiro"})
    _migrate(ledger, modulos["financeiro"].migrations)
    _migrate(host, modulos["agenda"].migrations)
    return host


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sincroniza a Agenda com o ledger.")
    parser.add_argument("--cliente", default=os.getenv("CLIENT_ID"))
    parser.add_argument("--config-root", type=Path, default=RAIZ / "clients")
    parser.add_argument("--data-root", type=Path, default=RAIZ / "data")
    args = parser.parse_args(argv)
    if not args.cliente:
        print("erro: informe --cliente ou CLIENT_ID", file=sys.stderr)
        return 2
    try:
        host = montar_host(args.cliente, args.config_root, args.data_root)
        resultado = executar_sincronizacao(host)
    except Exception as exc:
        print(f"erro: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
    return 0 if resultado.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
