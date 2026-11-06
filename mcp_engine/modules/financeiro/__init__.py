"""Módulo `financeiro`: ledger gerencial em SQLite, regime de caixa (spec 023 e 024)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mcp_engine.core.acoes import ContextoAcao
from mcp_engine.core.executor import Handler, HandlerContext
from mcp_engine.core.leituras import tipo_leitura_protegida
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


class ConsultaRestritaArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consulta: str = Field(min_length=2, max_length=40, pattern=r"^[a-z_]+$")
    periodo: str | None = Field(default=None, max_length=30)
    filtros: dict[str, str | int] | None = None


def _tipo_consulta_restrita(host: ModuleHost, skills: tuple[str, ...]) -> Any:
    def ler(ctx: ContextoAcao, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        contexto = HandlerContext(
            client_id=ctx.client_id,
            actor=ctx.ator,
            role=ctx.papel,
            trace_id=ctx.trace_id,
            data_root=ctx.data_root,
        )
        r = consultar_financeiro(
            contexto,
            {k: v for k, v in args.items() if v is not None},
            lambda: _operacoes_do_modulo(host, skills),
        )
        return args["consulta"], {
            "consulta": args["consulta"],
            "periodo": args.get("periodo"),
            "resumo": r.get("texto"),
            "dados": r.get("dados"),
        }

    return tipo_leitura_protegida(
        nome="consulta_restrita",
        titulo="Consulta financeira restrita",
        descricao=(
            "Consulta ao ledger com comissão, repasse, faturamento ou relatório da clínica, que"
            " só o administrador pode ver. O chat não recebe os números: devolva o link; o"
            " administrador o abre, se identifica e vê o resultado na página. Informe `consulta`"
            " (por exemplo repasse_por_profissional, resumo_mensal, resultado_mensal,"
            " painel_financeiro), `periodo` (AAAA-MM ou AAAA-MM-DD..AAAA-MM-DD) e, se quiser,"
            " `filtros` (unidade, profissional)."
        ),
        esquema=ConsultaRestritaArgs,
        ler=ler,
        papel_minimo="admin",
        resumir=lambda a: (
            f"Consulta financeira restrita: {a['consulta']}"
            + (f" ({a['periodo']})" if a.get("periodo") else "")
        ),
    )


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
        host.registrar_tipo_acao(_tipo_consulta_restrita(host, skills))
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
