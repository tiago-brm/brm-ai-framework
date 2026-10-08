"""Prepara os perfis da demo para uso no Claude Desktop (cliente instituto-afw).

No chat, quem é o usuário é definido ao iniciar o servidor MCP, pela variável
MCP_USER_EMAIL, e não muda durante a conversa. Este script cria os perfis da
demo no banco do cliente (idempotente) e imprime o trecho de configuração do
Claude Desktop para UM perfil.

Uso: uv run python scripts/preparar_demo_chat.py [perfil]
Perfis: wagner, ana, camila, helena, lucas, paulo, beatriz, rodrigo (padrão: wagner)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcp_engine.core.auth import Role  # noqa: E402
from mcp_engine.core.user_store import SQLUserStore  # noqa: E402

CLIENT_ID = "instituto-afw"
PERFIS = {
    "wagner": ("wagner@clinica-demo.com.br", Role.ADMIN, "psicólogo responsável"),
    "ana": ("ana.weis@clinica-demo.com.br", Role.ADMIN, "psicóloga e diretora"),
    "lucas": ("lucas.ribeiro@clinica-demo.com.br", Role.ADMIN, "psicólogo"),
    "paulo": ("paulo.tavares@clinica-demo.com.br", Role.ADMIN, "psicólogo"),
    "camila": ("camila.souza@clinica-demo.com.br", Role.VIEWER, "recepção"),
    "helena": ("helena.prado@clinica-demo.com.br", Role.SUPER_ADMIN, "responsável técnica"),
    "beatriz": ("beatriz.lima@aluno-demo.com.br", Role.VIEWER, "aluna da pós"),
    "rodrigo": ("rodrigo.azevedo@clinica-demo.com.br", Role.ADMIN, "supervisor"),
}


def main() -> None:
    escolha = sys.argv[1] if len(sys.argv) > 1 else "wagner"
    if escolha not in PERFIS:
        raise SystemExit(f"Perfil desconhecido: {escolha}. Opções: {', '.join(PERFIS)}")

    store = SQLUserStore(ROOT / "clients", CLIENT_ID)
    for nome, (email, role, _) in PERFIS.items():
        if store.get_by_email(email) is None:
            store.create_user(email, role, api_token=f"demo-{nome}")

    email, role, papel = PERFIS[escolha]
    config = {
        "mcpServers": {
            f"brm-instituto-{escolha}": {
                "command": "uv",
                "args": ["run", "--cwd", str(ROOT), "python", "-m", "mcp_engine.core.mcp_server"],
                "env": {
                    "CLIENT_ID": CLIENT_ID,
                    "CONFIG_ROOT": str(ROOT / "clients"),
                    "DATA_ROOT": str(ROOT / "data"),
                    "MCP_USER_EMAIL": email,
                },
            }
        }
    }
    print(f"Perfis da demo prontos no cliente {CLIENT_ID}.")
    print(f"Perfil deste trecho: {escolha} ({papel}, {role.value}).\n")
    print(json.dumps(config, indent=2, ensure_ascii=False))
    print("\nCole em ~/Library/Application Support/Claude/claude_desktop_config.json,")
    print("reinicie o Claude Desktop (Cmd+Q) e abra uma conversa nova.")
    print("Para outro perfil, rode de novo com o nome dele e reinicie.")
    print("\nPainel (dashboard): entre com o token demo-helena (super_admin).")
    print("Estes tokens são só do cliente de demonstração; não use em cliente real.")


if __name__ == "__main__":
    main()
