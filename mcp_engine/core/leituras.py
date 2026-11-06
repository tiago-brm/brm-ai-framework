"""Leitura protegida: base da Central de Ações para dado que só certos papéis podem ver.

O chat usa uma conta de canal sem papel de gestão, então ele nunca recebe o dado protegido. Ele só
prepara o pedido e devolve o link. A pessoa abre o link e se identifica; o papel dela é conferido
(`papel_minimo`), o dado é lido ao vivo e aparece só na página, e a confirmação com PIN fica no
registro de auditoria com o nome de quem viu. O resumo que volta ao chat nunca traz o conteúdo.

Todo tipo que precisar de nível de aprovação para ler usa esta fábrica: o módulo declara o papel
mínimo e, se quiser a liberação de um gestor antes de mostrar o dado, `sensivel` com
`papel_liberacao`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel

from mcp_engine.core.acoes import ContextoAcao, Previa, TipoAcao

Leitor = Callable[[ContextoAcao, dict[str, Any]], tuple[str, Mapping[str, Any]]]


def tipo_leitura_protegida(
    *,
    nome: str,
    titulo: str,
    descricao: str,
    esquema: type[BaseModel],
    ler: Leitor,
    papel_minimo: str = "admin",
    papel_liberacao: str = "admin",
    sensivel: bool = False,
    resumir: Callable[[dict[str, Any]], str] | None = None,
) -> TipoAcao:
    """`ler` devolve `(resumo, dados)` lidos ao vivo; os dados só vão para a página."""

    def carregar(ctx: ContextoAcao, args: dict[str, Any]) -> Previa:
        resumo, dados = ler(ctx, args)
        return Previa(resumo=resumo, dados=dados)

    def executar(ctx: ContextoAcao, args: dict[str, Any]) -> dict[str, Any]:
        return {"resumo": f"{titulo}: consulta liberada e vista por {ctx.ator}."}

    return TipoAcao(
        nome=nome,
        titulo=titulo,
        descricao=descricao,
        esquema=esquema,
        resumir=resumir or (lambda args: f"{titulo} (exige o administrador)"),
        carregar=carregar,
        executar=executar,
        papel_minimo=papel_minimo,
        papel_liberacao=papel_liberacao,
        sensivel=sensivel,
    )
