"""Módulo `agenda`: leitura e sincronização da Agenda (spec 026, Fase 1 somente leitura)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from mcp_engine.core.executor import ExecucaoFalhou
from mcp_engine.modules import BaseModule, ModuleError, ModuleHost
from mcp_engine.modules.agenda.handlers import criar_handlers
from mcp_engine.modules.agenda.provider import (
    AgendaConfigInvalida,
    AgendaProvider,
    HttpAgendaProvider,
)
from mcp_engine.modules.agenda.sincronizacao import executar_sincronizacao
from mcp_engine.modules.agenda.tempo import FUSO_PADRAO

__all__ = ["MODULE", "AgendaModule", "executar_sincronizacao"]


def provider_http(host: ModuleHost, dominios: tuple[str, ...]) -> HttpAgendaProvider:
    try:
        chave = host.secrets.get("agenda_api_key")
        url = host.secrets.get("agenda_base_url")
    except ExecucaoFalhou:
        raise AgendaConfigInvalida(
            "A Agenda não está configurada neste cliente (faltam os segredos agenda_api_key e"
            " agenda_base_url)."
        ) from None
    return HttpAgendaProvider(url, chave, dominios=dominios)


class AgendaModule(BaseModule):
    provider_factory: Callable[[ModuleHost], AgendaProvider] | None = None

    @contextmanager
    def abrir_provider(self, host: ModuleHost) -> Iterator[AgendaProvider]:
        provider = (
            self.provider_factory(host)
            if self.provider_factory
            else provider_http(host, self.manifest.dominios_requeridos)
        )
        try:
            yield provider
        finally:
            fechar = getattr(provider, "fechar", None)
            if fechar:
                fechar()

    def install(self, host: ModuleHost) -> None:
        if "financeiro" not in host.modulos:
            raise ModuleError("o módulo 'agenda' exige o módulo 'financeiro' habilitado")

        def hoje() -> date:
            return datetime.now(FUSO_PADRAO).date()

        for nome, handler in criar_handlers(host, self.abrir_provider, hoje).items():
            host.handlers.register(nome, handler)


MODULE = AgendaModule(Path(__file__).parent)
