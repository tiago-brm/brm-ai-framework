"""Módulo `financeiro`: ledger gerencial em SQLite, regime de caixa (spec 023 e 024)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp_engine.core.executor import Handler, HandlerContext
from mcp_engine.modules import BaseModule, ModuleHost
from mcp_engine.modules.financeiro import fatos
from mcp_engine.modules.financeiro.api import FinanceiroAPI
from mcp_engine.modules.financeiro.handlers import HANDLERS, consultar_financeiro
from mcp_engine.modules.financeiro.relatorios import criar_gerador


def _operacoes_do_modulo(host: ModuleHost, skills: tuple[str, ...]) -> dict[str, Any] | None:
    resumo = host.operacoes_agregadas()
    if resumo is None:
        return None
    aguardando = sum(
        estados.get("aguardando_aprovacao", 0)
        for skill, estados in resumo["por_skill"].items()
        if skill in skills
    )
    return {"aguardando_aprovacao": aguardando}


class FinanceiroModule(BaseModule):
    def module_api(self, host: ModuleHost) -> FinanceiroAPI:
        return FinanceiroAPI(host.data_root, host.client_id)

    def install(self, host: ModuleHost) -> None:
        skills = self.manifest.skills

        def consultar(ctx: HandlerContext, args: dict[str, Any]) -> dict[str, Any]:
            return consultar_financeiro(ctx, args, lambda: _operacoes_do_modulo(host, skills))

        for nome, handler in HANDLERS.items():
            registrado: Handler = consultar if nome == "consultar_financeiro" else handler
            host.handlers.register(nome, registrado)
        host.registrar_provedor_fatos(
            "recebimento", fatos.fatos_do_recebimento, fatos.FATOS_RECEBIMENTO
        )
        host.registrar_provedor_fatos("despesa", fatos.fatos_da_despesa, fatos.FATOS_DESPESA)
        host.registrar_provedor_fatos("situacao", fatos.fatos_da_situacao, fatos.FATOS_SITUACAO)
        host.registrar_verificacao_saude(
            "integridade",
            lambda: fatos.verificar_integridade_ledger(host.data_root, host.client_id),
        )
        # Hosts isolados (por exemplo, a API entre módulos em testes) não carregam o
        # caderno; no motor completo a extensão sempre está disponível.
        if host.extensoes is not None:
            host.registrar_gerador_relatorio(criar_gerador(host.data_root, host.client_id))


MODULE = FinanceiroModule(Path(__file__).parent)
