"""Gestão das pessoas de um cliente: usuário, nome, token e PIN.

    uv run python scripts/pessoas.py criar ana@clinica.com.br --nome "Ana Souza" --papel secretaria
    uv run python scripts/pessoas.py lote pessoas.csv --saida tokens.txt
    uv run python scripts/pessoas.py listar
    uv run python scripts/pessoas.py nome ana@clinica.com.br "Ana Souza"
    uv run python scripts/pessoas.py novo-token ana@clinica.com.br
    uv run python scripts/pessoas.py redefinir-pin ana@clinica.com.br

Cada pessoa tem o seu usuário, o seu token (mostrado uma só vez) e o seu PIN (criado por ela no
primeiro acesso). O cliente e as pastas vêm de CLIENT_ID, CONFIG_ROOT e DATA_ROOT, ou dos
argumentos `--cliente`, `--config-root` e `--data-root`. Como na API, o banco de usuários fica em
`$BRM_DATA_DIR/<cliente>/users.db` quando BRM_DATA_DIR está definida: use o mesmo valor da API.
O CSV do `lote` tem as colunas `email,nome,papel`.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from audit.sink import FileSystemAuditSink  # noqa: E402
from mcp_engine.core.atributos_usuario import AtributosUsuarioStore  # noqa: E402
from mcp_engine.core.auth import Role  # noqa: E402
from mcp_engine.core.models import AuditDecision, AuditEvent  # noqa: E402
from mcp_engine.core.state import StateStore  # noqa: E402
from mcp_engine.core.user_store import (  # noqa: E402
    SQLUserStore,
    UserAlreadyExistsError,
    UserNotFoundError,
)

PAPEIS = {"secretaria": Role.VIEWER, "gestor": Role.ADMIN}
ATOR = "cli:pessoas"


class Contexto:
    def __init__(self, cliente: str, config_root: Path, data_root: Path) -> None:
        self.cliente = cliente
        self.usuarios = SQLUserStore(config_root, cliente)
        self.atributos = AtributosUsuarioStore(StateStore(data_root, cliente))
        self._auditoria = FileSystemAuditSink(data_root)

    def auditar(self, acao: str, **detalhes: str) -> None:
        self._auditoria.record(
            AuditEvent(
                client_id=self.cliente,
                occurred_at=datetime.now(UTC),
                actor=ATOR,
                action=acao,
                decision=AuditDecision.ALLOWED,
                details=detalhes,
            )
        )


def criar(ctx: Contexto, email: str, nome: str, papel: str) -> str:
    email = email.strip().lower()
    if papel not in PAPEIS:
        raise SystemExit(f"papel inválido: {papel!r} (use {' ou '.join(PAPEIS)})")
    try:
        _, token = ctx.usuarios.create_user(email, PAPEIS[papel])
    except UserAlreadyExistsError:
        raise SystemExit(f"já existe um usuário com o e-mail {email}") from None
    ctx.atributos.definir_nome("local", email, nome, por=ATOR)
    ctx.auditar("pessoa:criada", pessoa=email, nome=nome, papel=papel)
    return token


def listar(ctx: Contexto) -> None:
    print(f"{'e-mail':40} {'nome':28} {'papel':12} PIN")
    for u in ctx.usuarios.list_users():
        if u.identity == "anonymous":
            continue
        nome = ctx.atributos.nome("local", u.identity) or "—"
        pin = "definido" if ctx.atributos.pin_definido("local", u.identity) else "pendente"
        print(f"{u.identity:40} {nome:28} {u.role.value:12} {pin}")


def novo_token(ctx: Contexto, email: str) -> str:
    try:
        token = ctx.usuarios.rotate_token(email.strip().lower())
    except UserNotFoundError:
        raise SystemExit(f"usuário não encontrado: {email}") from None
    ctx.auditar("pessoa:token_trocado", pessoa=email.strip().lower())
    return token


def redefinir_pin(ctx: Contexto, email: str) -> None:
    email = email.strip().lower()
    if not ctx.atributos.redefinir_pin("local", email, por=ATOR):
        raise SystemExit(f"{email} não tem PIN definido")
    ctx.auditar("pessoa:pin_redefinido", pessoa=email)


def escrever_tokens(destino: Path, linhas: list[str]) -> None:
    with destino.open("a") as saida:
        saida.write("\n".join(linhas) + "\n")
    destino.chmod(0o600)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--cliente", default=os.getenv("CLIENT_ID"))
    ap.add_argument("--config-root", default=os.getenv("CONFIG_ROOT", str(ROOT / "clients")))
    ap.add_argument("--data-root", default=os.getenv("DATA_ROOT", str(ROOT / "data")))
    sub = ap.add_subparsers(dest="comando", required=True)
    c = sub.add_parser("criar")
    c.add_argument("email")
    c.add_argument("--nome", required=True)
    c.add_argument("--papel", required=True, choices=sorted(PAPEIS))
    c.add_argument("--saida", help="arquivo (0600) onde guardar o token; sem ele, imprime")
    lt = sub.add_parser("lote")
    lt.add_argument("csv")
    lt.add_argument("--saida", required=True, help="arquivo (0600) onde guardar os tokens")
    sub.add_parser("listar")
    nm = sub.add_parser("nome")
    nm.add_argument("email")
    nm.add_argument("nome")
    n = sub.add_parser("novo-token")
    n.add_argument("email")
    n.add_argument("--saida")
    r = sub.add_parser("redefinir-pin")
    r.add_argument("email")
    args = ap.parse_args()
    if not args.cliente:
        raise SystemExit("informe --cliente ou CLIENT_ID")

    ctx = Contexto(args.cliente, Path(args.config_root), Path(args.data_root))
    if args.comando == "criar":
        token = criar(ctx, args.email, args.nome, args.papel)
        entrega(args.saida, f"{args.nome} <{args.email}> token={token}", token)
    elif args.comando == "lote":
        linhas = []
        with Path(args.csv).open(newline="", encoding="utf-8") as arquivo:
            for linha in csv.DictReader(arquivo):
                token = criar(ctx, linha["email"], linha["nome"], linha["papel"].strip().lower())
                linhas.append(f"{linha['nome']} <{linha['email']}> token={token}")
        escrever_tokens(Path(args.saida), linhas)
        print(f"{len(linhas)} pessoas criadas. Tokens em {args.saida} (0600).")
    elif args.comando == "listar":
        listar(ctx)
    elif args.comando == "nome":
        email = args.email.strip().lower()
        if ctx.usuarios.get_by_email(email) is None:
            raise SystemExit(f"usuário não encontrado: {email}")
        ctx.atributos.definir_nome("local", email, args.nome, por=ATOR)
        ctx.auditar("pessoa:nome_alterado", pessoa=email, nome=args.nome)
        print("Nome atualizado.")
    elif args.comando == "novo-token":
        token = novo_token(ctx, args.email)
        entrega(args.saida, f"{args.email} token={token}", token)
    else:
        redefinir_pin(ctx, args.email)
        print("PIN redefinido. A pessoa cria um novo no próximo acesso.")


def entrega(saida: str | None, linha: str, token: str) -> None:
    if saida:
        escrever_tokens(Path(saida), [linha])
        print(f"Token salvo em {saida} (0600). Ele não pode ser recuperado depois.")
    else:
        print(f"Token (aparece só agora): {token}")


if __name__ == "__main__":
    main()
