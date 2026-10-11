from __future__ import annotations

import httpx
import pytest

from evals.ambiente import (
    FERRAMENTAS_ADMIN,
    FERRAMENTAS_VIEWER,
    USUARIOS,
    Ambiente,
)
from mcp_engine.core.models import AuditDecision

from .conftest import eventos, resultado, rodar, sessao_http


def _nomes(ferramentas) -> set[str]:
    return {t.name for t in ferramentas}


def test_token_valido_executa_e_audita_com_identidade_e_trace(
    ambiente: Ambiente, servidor_http: str
):
    async def cenario():
        async with sessao_http(servidor_http, ambiente.tokens["viewer"]) as s:
            return resultado(await s.call_tool("consultar_saldo", {"periodo": "hoje"}))

    res = rodar(cenario)
    assert res["allowed"] is True
    assert '"executado_por": "ana@avaliacao.test"' in res["result"]

    da_ana = [e for e in eventos(ambiente) if e.user_email == "ana@avaliacao.test"]
    assert da_ana
    assert all(e.trace_id for e in da_ana)
    assert any(
        e.action == "consultar_saldo" and e.decision is AuditDecision.ALLOWED for e in da_ana
    )


def test_token_invalido_ou_ausente_recebe_401(servidor_http: str):
    invalido = {"Authorization": "Bearer nao-existe"}
    assert httpx.get(f"{servidor_http}/sse", headers=invalido).status_code == 401
    assert httpx.get(f"{servidor_http}/sse").status_code == 401
    assert httpx.post(f"{servidor_http}/messages/?session_id=x", json={}).status_code == 401
    assert httpx.get(f"{servidor_http}/health").status_code == 200

    async def cenario():
        async with sessao_http(servidor_http, "nao-existe"):
            pass

    with pytest.raises((Exception, BaseExceptionGroup)):
        rodar(cenario)


def test_sem_permissao_nega_e_audita(ambiente: Ambiente, servidor_http: str):
    async def cenario():
        async with sessao_http(servidor_http, ambiente.tokens["viewer"]) as s:
            return resultado(
                await s.call_tool(
                    "estornar_lancamento", {"lancamento_id": "l-1", "motivo": "teste"}
                )
            )

    res = rodar(cenario)
    assert res["allowed"] is False
    assert "Acesso negado" in res["reason"]

    negados = [
        e
        for e in eventos(ambiente)
        if e.action == "estornar_lancamento" and e.decision is AuditDecision.DENIED
    ]
    assert len(negados) == 1
    assert negados[0].actor == "ana@avaliacao.test"
    assert negados[0].role == "viewer"
    assert negados[0].trace_id
    assert not any(e.action == "operacao:executada" for e in eventos(ambiente))


def test_lista_de_ferramentas_filtrada_por_perfil(ambiente: Ambiente, servidor_http: str):
    async def listar(perfil: str) -> set[str]:
        async with sessao_http(servidor_http, ambiente.tokens[perfil]) as s:
            return _nomes((await s.list_tools()).tools)

    viewer = rodar(lambda: listar("viewer"))
    admin = rodar(lambda: listar("admin"))
    super_admin = rodar(lambda: listar("super_admin"))

    assert viewer == FERRAMENTAS_VIEWER
    assert admin == FERRAMENTAS_ADMIN
    assert "estornar_lancamento" not in viewer
    assert "estornar_lancamento" in admin
    assert admin < super_admin
    assert "rotacionar_token" in super_admin


def test_propor_e_confirmar_skill_a2(ambiente: Ambiente, servidor_http: str):
    async def cenario():
        async with sessao_http(servidor_http, ambiente.tokens["viewer"]) as s:
            proposta = resultado(
                await s.call_tool(
                    "registrar_recebimento",
                    {"cliente_id": "c-001", "valor": 275, "data": "2026-03-14"},
                )
            )
            antes = [e.action for e in eventos(ambiente)]
            final = resultado(
                await s.call_tool("confirmar_operacao", {"operacao_id": proposta["operacao_id"]})
            )
            return proposta, antes, final

    proposta, antes, final = rodar(cenario)
    assert proposta["pendente"] is True
    assert proposta["status"] == "proposta"
    assert "275" in proposta["resumo"]
    assert "operacao:executada" not in antes
    assert final["status"] == "executada"
    assert '"valor": 275' in final["result"]

    acoes = [(e.action, e.user_email) for e in eventos(ambiente)]
    assert ("operacao:executada", "ana@avaliacao.test") in acoes


def test_propor_e_aprovar_por_outro_perfil(ambiente: Ambiente, servidor_http: str):
    async def propor():
        async with sessao_http(servidor_http, ambiente.tokens["viewer"]) as s:
            return resultado(
                await s.call_tool(
                    "registrar_recebimento",
                    {"cliente_id": "c-002", "valor": 9000, "data": "2026-03-14"},
                )
            )

    async def agir(perfil: str, op: str):
        async with sessao_http(servidor_http, ambiente.tokens[perfil]) as s:
            return resultado(await s.call_tool("aprovar_operacao", {"operacao_id": op}))

    proposta = rodar(propor)
    assert proposta["status"] == "aguardando_aprovacao"
    assert proposta["rule_id"] == "recebimento-valor-alto"

    negado = rodar(lambda: agir("viewer", proposta["operacao_id"]))
    assert negado["allowed"] is False

    aprovada = rodar(lambda: agir("admin", proposta["operacao_id"]))
    assert aprovada["status"] == "executada"
    assert '"executado_por": "ana@avaliacao.test"' in aprovada["result"]

    aprovacoes = [
        e
        for e in eventos(ambiente)
        if e.action == "operacao:confirmada" and e.details.get("aprovado_por")
    ]
    assert aprovacoes[0].details["aprovado_por"] == USUARIOS["admin"][0]
    assert aprovacoes[0].details["proposto_por"] == USUARIOS["viewer"][0]


def test_aprovar_a_propria_operacao_e_negado(ambiente: Ambiente, servidor_http: str):
    async def cenario():
        async with sessao_http(servidor_http, ambiente.tokens["admin"]) as s:
            proposta = resultado(
                await s.call_tool(
                    "estornar_lancamento", {"lancamento_id": "l-100", "motivo": "duplicado"}
                )
            )
            propria = resultado(
                await s.call_tool("aprovar_operacao", {"operacao_id": proposta["operacao_id"]})
            )
        async with sessao_http(servidor_http, ambiente.tokens["admin2"]) as s:
            outro = resultado(
                await s.call_tool("aprovar_operacao", {"operacao_id": proposta["operacao_id"]})
            )
        return proposta, propria, outro

    proposta, propria, outro = rodar(cenario)
    assert proposta["status"] == "aguardando_aprovacao"
    assert propria["allowed"] is False
    assert outro["status"] == "executada"
    assert any(
        e.action == "operacao:aprovacao_negada" and e.details["motivo"] == "propria"
        for e in eventos(ambiente)
    )


def test_cadeia_de_auditoria_verificavel_ao_final(ambiente: Ambiente, servidor_http: str):
    async def cenario():
        async with sessao_http(servidor_http, ambiente.tokens["viewer"]) as s:
            await s.call_tool("consultar_saldo", {})
            await s.call_tool("estornar_lancamento", {"lancamento_id": "l-1", "motivo": "x"})
        async with sessao_http(servidor_http, ambiente.tokens["admin"]) as s:
            return resultado(await s.call_tool("query_audit_log", {}))

    consulta = rodar(cenario)
    assert consulta["total"] >= 2

    todos = eventos(ambiente)
    assert len(todos) >= 2
    assert all(e.user_email and e.role and e.trace_id for e in todos)
    traces = {e.trace_id for e in todos if e.user_email in {"ana@avaliacao.test"}}
    assert len(traces) == 1


def test_cadeia_adulterada_e_detectada(ambiente: Ambiente, servidor_http: str):
    from audit.sink import AuditChainError

    async def cenario():
        async with sessao_http(servidor_http, ambiente.tokens["viewer"]) as s:
            await s.call_tool("consultar_saldo", {})
            await s.call_tool("estornar_lancamento", {"lancamento_id": "l-1", "motivo": "x"})

    rodar(cenario)
    caminho = ambiente.data_root / "avaliacao" / "audit.jsonl"
    linhas = caminho.read_text().splitlines()
    assert len(linhas) >= 2
    linhas[0] = linhas[0].replace("ana@avaliacao.test", "outra@avaliacao.test")
    caminho.write_text("\n".join(linhas) + "\n")
    with pytest.raises(AuditChainError):
        eventos(ambiente)
