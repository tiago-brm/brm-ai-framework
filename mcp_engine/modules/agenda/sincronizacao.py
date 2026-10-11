"""Sincronização somente leitura Agenda -> ledger (spec 026)."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any

from mcp_engine.core.executor import ExecucaoFalhou
from mcp_engine.modules import ModuleHost
from mcp_engine.modules.agenda.provider import Agendamento, AgendaProvider

JANELA_INICIAL_DIAS = 90
MARGEM_CURSOR = timedelta(minutes=5)
CHAVE_CURSOR = "cursor_atualizado_em"
CHAVE_ULTIMA = "ultima_sincronizacao"
_FORMATO = "%Y-%m-%dT%H:%M:%SZ"

_STATUS_SESSAO = {"COMPLETED": "COMPLETED", "CANCELLED": "CANCELLED"}


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime(_FORMATO)


def _normalizar(nome: str) -> str:
    base = unicodedata.normalize("NFKD", nome)
    base = "".join(c for c in base if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", base.lower())).strip()


def _hash_telefone(telefone: str | None) -> str | None:
    digitos = re.sub(r"\D", "", telefone or "")
    if len(digitos) < 8:
        return None
    return hashlib.sha256(digitos[-11:].encode()).hexdigest()[:16]


def _abrir_estado(host: ModuleHost) -> sqlite3.Connection:
    con = host.connect()
    con.isolation_level = None
    con.row_factory = sqlite3.Row
    return con


def ler_estado(host: ModuleHost, chave: str) -> str | None:
    con = _abrir_estado(host)
    try:
        row = con.execute("SELECT valor FROM estado WHERE chave = ?", (chave,)).fetchone()
    finally:
        con.close()
    return row["valor"] if row else None


def _atualizar_espelho(
    est: sqlite3.Connection, novos: list[Agendamento]
) -> list[tuple[str, str, str]]:
    duplicidades: list[tuple[str, str, str]] = []
    vistos: set[str] = set()
    for a in reversed(novos):
        if a.customer_id in vistos:
            continue
        vistos.add(a.customer_id)
        norm = _normalizar(a.customer_name)
        tel = _hash_telefone(a.customer_phone)
        antes = est.execute(
            "SELECT nome_norm, tel_hash FROM cliente_espelho WHERE customer_id = ?",
            (a.customer_id,),
        ).fetchone()
        if antes and antes["nome_norm"] == norm and antes["tel_hash"] == tel:
            continue
        est.execute(
            "INSERT INTO cliente_espelho (customer_id, nome_norm, tel_hash) VALUES (?,?,?)"
            " ON CONFLICT (customer_id) DO UPDATE SET nome_norm = excluded.nome_norm,"
            " tel_hash = excluded.tel_hash",
            (a.customer_id, norm, tel),
        )
        if tel is None or not norm:
            continue
        for outro in est.execute(
            "SELECT customer_id FROM cliente_espelho WHERE nome_norm = ? AND customer_id <> ?"
            " AND tel_hash IS NOT NULL AND tel_hash <> ?",
            (norm, a.customer_id, tel),
        ):
            par = sorted((a.customer_id, outro["customer_id"]))
            duplicidades.append((par[0], par[1], a.customer_name))
    return duplicidades


def sincronizar(
    host: ModuleHost, provider: AgendaProvider, agora: datetime | None = None
) -> dict[str, Any]:
    agora = (agora or datetime.now(UTC)).astimezone(UTC)
    api = host.module_api("financeiro")

    cursor = ler_estado(host, CHAVE_CURSOR)
    if cursor:
        lidos = provider.listar_agendamentos(
            atualizado_desde=datetime.strptime(cursor, _FORMATO).replace(tzinfo=UTC) - MARGEM_CURSOR
        )
    else:
        lidos = provider.listar_agendamentos(de=agora - timedelta(days=JANELA_INICIAL_DIAS))

    est = _abrir_estado(host)
    try:
        est.execute("BEGIN IMMEDIATE")
        try:
            visto = {
                r["agenda_id"]: r["atualizado_em"]
                for r in est.execute("SELECT agenda_id, atualizado_em FROM agendamento_visto")
            }
            novos = sorted(
                (a for a in lidos if visto.get(a.id) != _iso(a.atualizado_em)),
                key=lambda a: (a.atualizado_em, a.id),
            )
            duplicidades = _atualizar_espelho(est, novos)
            resumo = _aplicar_no_ledger(api, novos, duplicidades, agora)
            est.executemany(
                "INSERT INTO agendamento_visto (agenda_id, atualizado_em) VALUES (?,?)"
                " ON CONFLICT (agenda_id) DO UPDATE SET atualizado_em = excluded.atualizado_em",
                [(a.id, _iso(a.atualizado_em)) for a in novos],
            )
            if lidos:
                novo_cursor = max(_iso(a.atualizado_em) for a in lidos)
                if cursor is None or novo_cursor > cursor:
                    _gravar(est, CHAVE_CURSOR, novo_cursor)
            _gravar(est, CHAVE_ULTIMA, _iso(agora))
            est.execute("COMMIT")
        except BaseException:
            est.execute("ROLLBACK")
            raise
    finally:
        est.close()
    return {"lidos": len(lidos), "ignorados": len(lidos) - len(novos), **resumo}


def _gravar(est: sqlite3.Connection, chave: str, valor: str) -> None:
    est.execute(
        "INSERT INTO estado (chave, valor) VALUES (?, ?)"
        " ON CONFLICT (chave) DO UPDATE SET valor = excluded.valor",
        (chave, valor),
    )


def _aplicar_no_ledger(
    api: Any,
    novos: list[Agendamento],
    duplicidades: list[tuple[str, str, str]],
    agora: datetime,
) -> dict[str, int]:
    pacotes: set[int] = set()
    with api.transacao() as led:
        for a in novos:
            paciente = led.paciente_da_agenda(a.customer_id, a.customer_name)
            profissional = (
                led.profissional_da_agenda(a.staff_id, a.staff_name) if a.staff_name else None
            )
            _, pacote = led.upsert_sessao(
                agenda_id=a.id,
                paciente_id=paciente,
                inicio_utc=_iso(a.inicio_utc),
                status_agenda=_STATUS_SESSAO.get(a.status, "SCHEDULED"),
                profissional_id=profissional,
                servico=a.service_name,
                preco_servico_centavos=a.preco_centavos,
            )
            if pacote is not None:
                pacotes.add(pacote)
        for a_id, b_id, nome in duplicidades:
            led.pendencia(
                "possivel_duplicidade_cliente",
                f"Possível cliente duplicado na Agenda: {nome} tem dois cadastros com telefones"
                " diferentes.",
                referencia=f"clientes:{a_id}:{b_id}",
                sugestao="Confira na Agenda e una os cadastros por lá; o ledger não funde sozinho.",
                detalhe={"clientes": [a_id, b_id]},
            )
        encerradas = led.encerrar_sem_desfecho_resolvidas(agora)
        sem_desfecho = led.gerar_pendencias_sem_desfecho(agora)
        cobrancas = led.gerar_cobrancas_por_sessao(pacotes)
    return {
        "processados": len(novos),
        "duplicidades": len(duplicidades),
        "sem_desfecho_novas": sem_desfecho,
        "sem_desfecho_encerradas": encerradas,
        "cobrancas_geradas": cobrancas,
    }


def executar_sincronizacao(host: ModuleHost, provider: AgendaProvider | None = None) -> dict:
    """Entrada do gatilho `sincronizar-agenda` (spec 031): devolve o resumo, nunca levanta
    erro da Agenda; a falha vai para a auditoria e para o retorno, sem URL nem segredo."""
    from mcp_engine.modules.agenda import MODULE

    try:
        if provider is None:
            with MODULE.abrir_provider(host) as aberto:
                resumo = sincronizar(host, aberto)
        else:
            resumo = sincronizar(host, provider)
    except ExecucaoFalhou as exc:
        host.audit("agenda.sincronizar", False)
        return {"ok": False, "erro": exc.reason}
    host.audit("agenda.sincronizar", True)
    return {"ok": True, **resumo}
