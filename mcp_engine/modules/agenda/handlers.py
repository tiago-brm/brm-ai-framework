"""Handlers nativos do módulo `agenda`: leitura governada e sincronização."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from datetime import date
from typing import Any

from mcp_engine.core.executor import ExecucaoFalhou, Handler, HandlerContext
from mcp_engine.modules import ModuleHost
from mcp_engine.modules.agenda.provider import STATUS_AGENDA, AgendaErro
from mcp_engine.modules.agenda.sincronizacao import CHAVE_ULTIMA, executar_sincronizacao, ler_estado
from mcp_engine.modules.agenda.tempo import FUSO_PADRAO, limites_utc, para_local, parse_dia

GESTORES = ("admin", "super_admin")
MAX_DIAS = 62
MAX_ITENS = 100
_UUID = re.compile(r"^[0-9a-fA-F-]{36}$")


def _brl(centavos: int | None) -> str | None:
    if centavos is None:
        return None
    reais, cent = divmod(centavos, 100)
    return f"R$ {reais:,}".replace(",", ".") + f",{cent:02d}"


def _periodo(args: dict[str, Any], padrao_de: date, padrao_ate: date, maximo: int) -> tuple:
    try:
        de = parse_dia(args.get("de"), padrao_de)
        ate = parse_dia(args.get("ate"), max(de, padrao_ate) if args.get("de") else padrao_ate)
    except ValueError as exc:
        raise ExecucaoFalhou(str(exc)) from None
    if ate < de:
        raise ExecucaoFalhou("O período está invertido: 'ate' vem antes de 'de'.")
    if (ate - de).days >= maximo:
        raise ExecucaoFalhou(f"Período longo demais: no máximo {maximo} dias por consulta.")
    return de, ate


def _status(args: dict[str, Any]) -> list[str]:
    bruto = args.get("status")
    itens = [s.strip().upper() for s in str(bruto).split(",")] if bruto else []
    itens = [s for s in itens if s]
    if any(s not in STATUS_AGENDA for s in itens):
        raise ExecucaoFalhou(f"Status inválido. Use: {', '.join(STATUS_AGENDA)}.")
    return itens


def criar_handlers(
    host: ModuleHost,
    abrir_provider: Callable[[ModuleHost], Any],
    hoje: Callable[[], date],
) -> dict[str, Handler]:
    fuso = FUSO_PADRAO

    def sessoes(ctx: HandlerContext, args: dict[str, Any]) -> dict[str, Any]:
        dia = hoje()
        de, ate = _periodo(args, dia, dia, MAX_DIAS)
        profissional = args.get("profissional")
        if profissional and not _UUID.match(str(profissional)):
            raise ExecucaoFalhou("Identificador de profissional inválido (use o id da Agenda).")
        inicio, fim = limites_utc(de, ate, fuso)
        with abrir_provider(host) as provider:
            itens = provider.listar_agendamentos(
                de=inicio,
                ate=fim,
                status=_status(args),
                profissional_id=str(profissional) if profissional else None,
            )
        por_status = Counter(a.status for a in itens)
        resposta: dict[str, Any] = {
            "periodo": {"de": de.isoformat(), "ate": ate.isoformat(), "fuso": str(fuso)},
            "total": len(itens),
            "por_status": dict(por_status),
        }
        if ctx.role in GESTORES:
            resposta["sessoes"] = [
                {
                    "agenda_id": a.id,
                    "data": para_local(a.inicio_utc, fuso).date().isoformat(),
                    "hora": para_local(a.inicio_utc, fuso).strftime("%H:%M"),
                    "cliente": a.customer_name,
                    "profissional": a.staff_name,
                    "servico": a.service_name,
                    "valor": _brl(a.preco_centavos),
                    "duracao_min": a.duracao_min,
                    "status": a.status,
                }
                for a in itens[:MAX_ITENS]
            ]
            resposta["truncado"] = len(itens) > MAX_ITENS
        else:
            resposta["agregado"] = True
            resposta["por_dia"] = dict(
                sorted(
                    Counter(
                        para_local(a.inicio_utc, fuso).date().isoformat() for a in itens
                    ).items()
                )
            )
            resposta["por_servico"] = dict(Counter(a.service_name or "—" for a in itens))
            resposta["por_profissional"] = dict(Counter(a.staff_name or "—" for a in itens))
        return resposta

    def clientes(ctx: HandlerContext, args: dict[str, Any]) -> dict[str, Any]:
        busca = str(args.get("busca") or "").strip()
        if len(busca) > 80:
            raise ExecucaoFalhou("A busca excede 80 caracteres.")
        limite = int(args.get("limite") or 10) if str(args.get("limite") or "10").isdigit() else 10
        with abrir_provider(host) as provider:
            achados = provider.buscar_clientes(busca, min(limite, 20), 0)
        if ctx.role in GESTORES:
            return {
                "total": len(achados),
                "clientes": [
                    {
                        "id": c.id,
                        "nome": c.nome,
                        "telefone": ("****" + c.telefone[-4:]) if c.telefone else None,
                    }
                    for c in achados
                ],
            }
        return {"total": len(achados), "agregado": True}

    def servicos(ctx: HandlerContext, args: dict[str, Any]) -> dict[str, Any]:
        with abrir_provider(host) as provider:
            itens = provider.listar_servicos()
        return {
            "total": len(itens),
            "servicos": [
                {
                    "id": s.id,
                    "nome": s.nome,
                    "valor": _brl(s.preco_centavos),
                    "duracao_min": s.duracao_min,
                    "disponivel": s.disponivel,
                }
                for s in itens
            ],
        }

    def sessoes_sem_pagamento(ctx: HandlerContext, args: dict[str, Any]) -> dict[str, Any]:
        dia = hoje()
        de, ate = _periodo(args, dia.replace(day=1), dia, 366)
        api = host.module_api("financeiro")
        itens = api.sessoes_realizadas_sem_pagamento(de, ate, fuso)
        total = sum(i["valor_previsto_centavos"] or 0 for i in itens)
        resposta: dict[str, Any] = {
            "periodo": {"de": de.isoformat(), "ate": ate.isoformat()},
            "total": len(itens),
            "valor_previsto": _brl(total),
            "ultima_sincronizacao": ler_estado(host, CHAVE_ULTIMA),
        }
        if ctx.role in GESTORES:
            resposta["sessoes"] = [
                {**i, "valor_previsto": _brl(i["valor_previsto_centavos"])}
                for i in itens[:MAX_ITENS]
            ]
            resposta["truncado"] = len(itens) > MAX_ITENS
        else:
            resposta["agregado"] = True
            resposta["por_profissional"] = dict(Counter(i["profissional"] or "—" for i in itens))
        return resposta

    def sincronizar_agenda(ctx: HandlerContext, args: dict[str, Any]) -> dict[str, Any]:
        resultado = executar_sincronizacao(host)
        if not resultado["ok"]:
            raise AgendaErro(resultado["erro"])
        return resultado

    return {
        "sessoes": sessoes,
        "clientes": clientes,
        "servicos": servicos,
        "sessoes_sem_pagamento": sessoes_sem_pagamento,
        "sincronizar_agenda": sincronizar_agenda,
    }
