"""Ambiente local de teste do cliente `clinica-piloto` com a Central de Ações.

    uv run python scripts/rodar_teste_local.py preparar
    uv run python scripts/rodar_teste_local.py api
    uv run python scripts/rodar_teste_local.py mcp

O conector do chat é um só (conta de canal); as pessoas (`secretaria`, `gestor`) entram pelo
token delas na página do link. Tudo fica em `$BRM_TESTE_DIR` (padrão `~/brm-teste`), fora do
repositório. A Agenda é simulada (`FakeAgendaProvider`) com dois pacientes; use `--agenda-real`
para usar os segredos `agenda_api_key` e `agenda_base_url` do ambiente.
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CLIENT_ID = "clinica-piloto"
TESTE = Path(os.getenv("BRM_TESTE_DIR", "~/brm-teste")).expanduser()
CLIENTES = TESTE / "clients"
DADOS = TESTE / "data"
PAINEL = os.getenv("BRM_TESTE_PAINEL", "http://localhost:5173")
PERFIS = {
    "chat": "chat@clinica-piloto.test",
    "secretaria": "secretaria@clinica-piloto.test",
    "gestor": "gestor@clinica-piloto.test",
}


def ambiente() -> None:
    os.environ.update(
        CLIENT_ID=CLIENT_ID,
        CONFIG_ROOT=str(CLIENTES),
        DATA_ROOT=str(DADOS),
        BRM_DATA_DIR=str(DADOS),
    )


def agenda_falsa() -> None:
    from mcp_engine.modules.agenda import MODULE
    from mcp_engine.modules.agenda.fake import FakeAgendaProvider
    from mcp_engine.modules.agenda.provider import (
        Agendamento,
        Endereco,
        FichaCliente,
        Responsavel,
    )

    agora = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)

    def sessao(id_, cliente, nome, fone, quando, status):
        return Agendamento(
            id_, cliente, nome, fone, "st1", "Ana Souza", "sv1", "Sessão individual",
            15000, 50, quando, status, agora, "web",
        )  # fmt: skip

    fake = FakeAgendaProvider(
        [
            sessao(
                "a1", "c1", "Maria Silva", "11999990001", agora - timedelta(days=1), "COMPLETED"
            ),
            sessao(
                "a2", "c2", "Pedro Alves", "11999990002", agora - timedelta(days=2), "COMPLETED"
            ),
            sessao(
                "a3", "c1", "Maria Silva", "11999990001", agora + timedelta(days=6), "CONFIRMED"
            ),
        ]
    )
    fake.fichas["c1"] = FichaCliente(
        "c1", "Maria Silva", "11999990001", "maria@exemplo.test", date(1990, 5, 1), "52998224725",
        Endereco("01001000", "Rua das Flores", "10", None, "Centro", "São Paulo", "SP"),
    )  # fmt: skip
    fake.fichas["c2"] = FichaCliente(
        "c2", "Pedro Alves", "11999990002", None, date(2014, 3, 8), None, None,
        (Responsavel("Carla Alves", None, "mãe", "11999990003", None, True),),
    )  # fmt: skip
    MODULE.provider_factory = lambda host: fake


def preparar() -> None:
    origem = ROOT / "clients" / CLIENT_ID
    destino = CLIENTES / CLIENT_ID
    if not destino.exists():
        shutil.copytree(origem, destino, ignore=shutil.ignore_patterns("*.db"))
        cfg = destino / "config" / "client_config.yaml"
        texto = cfg.read_text().replace(
            "vault_root: clients/clinica-piloto/vault", f"vault_root: {destino / 'vault'}"
        )
        texto = texto.replace("url_base: http://localhost:8000", f"url_base: {PAINEL}")
        cfg.write_text(texto)
    DADOS.mkdir(parents=True, exist_ok=True)
    ambiente()

    from mcp_engine.core.auth import Role, set_current_user
    from mcp_engine.core.mcp_server import BRMEngine, build_server

    engine = BRMEngine(CLIENT_ID, CLIENTES, DADOS)
    tokens = []
    for nome, papel in (
        ("chat", Role.VIEWER),
        ("secretaria", Role.VIEWER),
        ("gestor", Role.ADMIN),
    ):
        email = PERFIS[nome]
        if engine.user_store.get_by_email(email) is None:
            _, token = engine.user_store.create_user(email, papel)
            tokens.append(f"{nome}: {email} token={token}")
    nomes = [t.name for t in build_server(engine)._tool_manager.list_tools()]
    engine.user_store.seed_open_tools(nomes)
    agenda_falsa()
    set_current_user(engine.user_store.get_by_email(PERFIS["gestor"]))
    engine.documento_aprovar_modelo("contrato-sessao")
    if tokens:
        arquivo = TESTE / "tokens.txt"
        with arquivo.open("a") as saida:
            saida.write("\n".join(tokens) + "\n")
        arquivo.chmod(0o600)
        print(f"Tokens (aparecem só agora) salvos em {arquivo}")
    print(f"Pronto em {TESTE}. Modelo contrato-sessao aprovado.")


def api() -> None:
    ambiente()
    if "--agenda-real" not in sys.argv:
        agenda_falsa()
    import uvicorn

    uvicorn.run("mcp_engine.core.http_app:app", host="127.0.0.1", port=8000)


def mcp(perfil: str) -> None:
    ambiente()
    os.environ["MCP_USER_EMAIL"] = PERFIS[perfil]
    if "--agenda-real" not in sys.argv:
        agenda_falsa()
    from mcp_engine.core.mcp_server import main

    main()


if __name__ == "__main__":
    comando = sys.argv[1] if len(sys.argv) > 1 else ""
    if comando == "preparar":
        preparar()
    elif comando == "api":
        api()
    elif comando == "mcp":
        mcp("chat")
    else:
        sys.exit(__doc__)
