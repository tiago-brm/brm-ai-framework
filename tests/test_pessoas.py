"""Comando `scripts/pessoas.py`: usuário, nome, token e PIN por pessoa."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from mcp_engine.core.auth import Role

RAIZ = Path(__file__).resolve().parent.parent


def _carregar():
    spec = importlib.util.spec_from_file_location("pessoas_cli", RAIZ / "scripts" / "pessoas.py")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["pessoas_cli"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def cli(tmp_path: Path):
    modulo = _carregar()
    ctx = modulo.Contexto("cli", tmp_path / "cfg", tmp_path / "data")
    return modulo, ctx


def test_criar_define_papel_nome_e_token_valido(cli) -> None:
    modulo, ctx = cli
    token = modulo.criar(ctx, "Ana@Clinica.com.br", "Ana Souza", "secretaria")
    perfil = ctx.usuarios.get_by_token(token)
    assert perfil.identity == "ana@clinica.com.br" and perfil.role is Role.VIEWER
    assert ctx.atributos.nome("local", "ana@clinica.com.br") == "Ana Souza"
    gestor = modulo.criar(ctx, "gil@clinica.com.br", "Gil", "gestor")
    assert ctx.usuarios.get_by_token(gestor).role is Role.ADMIN


def test_criar_repetido_e_papel_invalido(cli) -> None:
    modulo, ctx = cli
    modulo.criar(ctx, "ana@c.com", "Ana", "secretaria")
    with pytest.raises(SystemExit, match="já existe"):
        modulo.criar(ctx, "ana@c.com", "Ana", "secretaria")
    with pytest.raises(SystemExit, match="papel inválido"):
        modulo.criar(ctx, "bia@c.com", "Bia", "admin")


def test_novo_token_invalida_o_antigo_e_redefinir_pin(cli) -> None:
    modulo, ctx = cli
    antigo = modulo.criar(ctx, "ana@c.com", "Ana", "secretaria")
    novo = modulo.novo_token(ctx, "ana@c.com")
    assert ctx.usuarios.get_by_token(antigo) is None
    assert ctx.usuarios.get_by_token(novo).identity == "ana@c.com"
    with pytest.raises(SystemExit, match="não tem PIN"):
        modulo.redefinir_pin(ctx, "ana@c.com")
    ctx.atributos.definir_pin("local", "ana@c.com", "2580", por="ana@c.com")
    modulo.redefinir_pin(ctx, "ana@c.com")
    assert ctx.atributos.pin_definido("local", "ana@c.com") is False


def test_operacoes_ficam_na_auditoria_sem_token(cli, tmp_path: Path) -> None:
    modulo, ctx = cli
    token = modulo.criar(ctx, "ana@c.com", "Ana", "secretaria")
    modulo.novo_token(ctx, "ana@c.com")
    texto = "".join(p.read_text() for p in (tmp_path / "data").rglob("audit.jsonl"))
    assert "pessoa:criada" in texto and "pessoa:token_trocado" in texto
    assert token not in texto


def test_lote_grava_tokens_com_permissao_restrita(cli, tmp_path: Path, monkeypatch) -> None:
    modulo, ctx = cli
    saida = tmp_path / "tokens.txt"
    modulo.escrever_tokens(saida, ["Ana <ana@c.com> token=abc"])
    assert saida.stat().st_mode & 0o777 == 0o600
