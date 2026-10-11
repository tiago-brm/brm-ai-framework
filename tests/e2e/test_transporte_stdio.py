from __future__ import annotations

import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from evals.ambiente import FERRAMENTAS_ADMIN, FERRAMENTAS_VIEWER, USUARIOS, Ambiente
from mcp_engine.core.models import AuditDecision

from .conftest import LANCADOR, RAIZ, eventos, resultado, rodar


def _parametros(ambiente: Ambiente, email: str | None) -> StdioServerParameters:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": str(RAIZ),
        "CLIENT_ID": "avaliacao",
        "CONFIG_ROOT": str(ambiente.config_root),
        "DATA_ROOT": str(ambiente.data_root),
        "HF_HUB_OFFLINE": "1",
    }
    if email:
        env["MCP_USER_EMAIL"] = email
    return StdioServerParameters(
        command=sys.executable, args=[str(LANCADOR)], env=env, cwd=str(RAIZ)
    )


async def _com_sessao(ambiente: Ambiente, email: str | None, fn):
    async with stdio_client(_parametros(ambiente, email)) as (leitura, escrita):
        async with ClientSession(leitura, escrita) as sessao:
            await sessao.initialize()
            return await fn(sessao)


def _stdio(ambiente: Ambiente, perfil: str | None, fn, timeout: float = 25):
    email = USUARIOS[perfil][0] if perfil else None
    return rodar(lambda: _com_sessao(ambiente, email, fn), timeout)


def test_stdio_lista_filtrada_e_execucao_auditada(ambiente: Ambiente):
    async def cenario(s):
        nomes = {t.name for t in (await s.list_tools()).tools}
        saldo = resultado(await s.call_tool("consultar_saldo", {"periodo": "hoje"}))
        negado = resultado(
            await s.call_tool("estornar_lancamento", {"lancamento_id": "l-1", "motivo": "x"})
        )
        return nomes, saldo, negado

    nomes, saldo, negado = _stdio(ambiente, "viewer", cenario)
    assert nomes == FERRAMENTAS_VIEWER
    assert saldo["allowed"] is True
    assert '"executado_por": "ana@avaliacao.test"' in saldo["result"]
    assert negado["allowed"] is False

    todos = eventos(ambiente)
    assert all(e.user_email == "ana@avaliacao.test" and e.trace_id for e in todos)
    assert any(
        e.action == "estornar_lancamento" and e.decision is AuditDecision.DENIED for e in todos
    )


def test_stdio_admin_ve_mais_ferramentas(ambiente: Ambiente):
    async def cenario(s):
        return {t.name for t in (await s.list_tools()).tools}

    assert _stdio(ambiente, "admin", cenario) == FERRAMENTAS_ADMIN


def test_stdio_identidade_desconhecida_vira_anonimo_sem_acesso(ambiente: Ambiente):
    async def cenario(s):
        nomes = {t.name for t in (await s.list_tools()).tools}
        return nomes

    nomes = _stdio(ambiente, None, cenario)
    assert "estornar_lancamento" not in nomes
    assert "aprovar_operacao" not in nomes


def test_stdio_propor_confirmar_e_cadeia_ao_final(ambiente: Ambiente):
    async def cenario(s):
        proposta = resultado(
            await s.call_tool(
                "agendar_atendimento",
                {"cliente_id": "c-001", "data": "2026-03-16", "hora": "14:00"},
            )
        )
        final = resultado(
            await s.call_tool("confirmar_operacao", {"operacao_id": proposta["operacao_id"]})
        )
        return proposta, final

    proposta, final = _stdio(ambiente, "viewer", cenario)
    assert proposta["status"] == "proposta"
    assert final["status"] == "executada"

    acoes = [e.action for e in eventos(ambiente)]
    assert acoes.index("operacao:confirmada") < acoes.index("operacao:executada")


def test_stdio_aprovacao_entre_dois_processos(ambiente: Ambiente):
    async def propor(s):
        return resultado(
            await s.call_tool(
                "estornar_lancamento", {"lancamento_id": "l-100", "motivo": "duplicado"}
            )
        )

    async def aprovar(s, op):
        return resultado(await s.call_tool("aprovar_operacao", {"operacao_id": op}))

    proposta = _stdio(ambiente, "admin", propor)
    assert proposta["status"] == "aguardando_aprovacao"

    propria = _stdio(ambiente, "admin", lambda s: aprovar(s, proposta["operacao_id"]))
    assert propria["allowed"] is False

    aprovada = _stdio(ambiente, "admin2", lambda s: aprovar(s, proposta["operacao_id"]))
    assert aprovada["status"] == "executada"

    todos = eventos(ambiente)
    executada = next(e for e in todos if e.action == "operacao:executada")
    assert executada.details["proposto_por"] == "bruno@avaliacao.test"
    assert executada.user_email == "caio@avaliacao.test"
