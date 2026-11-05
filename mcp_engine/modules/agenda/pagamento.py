"""Registro de pagamento na Agenda (tipo de ação da Central de Ações).

O dinheiro da clínica é da Agenda: o framework não guarda o recebimento. O chat prepara o pedido
com o nome do paciente, o valor e a forma; a pessoa confere na página e confirma com o PIN, e o
framework grava na Agenda com ator e trace_id. A referência externa é derivada da ação, então
repetir a confirmação nunca duplica o pagamento. Se o valor bate com um saldo em aberto a Agenda
baixa na hora e devolve o recibo; se não bate, o pagamento fica aguardando conferência e a gerência
o confirma por outra ação (`confirmar_pagamento`), que baixa e emite o recibo.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import datetime, time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mcp_engine.core.acoes import ContextoAcao, Previa, TipoAcao
from mcp_engine.core.executor import ExecucaoFalhou
from mcp_engine.modules import ModuleHost
from mcp_engine.modules.agenda.provider import Cliente
from mcp_engine.modules.agenda.sincronizacao import _normalizar
from mcp_engine.modules.agenda.tempo import FUSO_PADRAO, parse_dia

FORMAS = {
    "dinheiro": "CASH",
    "especie": "CASH",
    "cash": "CASH",
    "pix": "PIX",
    "cartao": "CARD",
    "credito": "CARD",
    "debito": "CARD",
    "card": "CARD",
    "transferencia": "TRANSFER",
    "ted": "TRANSFER",
    "doc": "TRANSFER",
    "transfer": "TRANSFER",
}
ROTULOS = {"CASH": "dinheiro", "PIX": "Pix", "CARD": "cartão", "TRANSFER": "transferência"}
MAX_CENTAVOS = 1_000_000_00


class RegistrarPagamentoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paciente: str | None = Field(default=None, min_length=2, max_length=80)
    cliente_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    valor: str = Field(min_length=1, max_length=20)
    forma: str = Field(min_length=2, max_length=30)
    data: str | None = Field(default=None, min_length=10, max_length=10)
    observacao: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _regras(self) -> RegistrarPagamentoArgs:
        if (self.paciente is None) == (self.cliente_id is None):
            raise ValueError("informe paciente ou cliente_id (um dos dois)")
        return self


def _forma(texto: str) -> str:
    codigo = FORMAS.get(_normalizar(texto).replace(" ", ""))
    if codigo is None:
        raise ExecucaoFalhou(
            "Forma de pagamento desconhecida (dinheiro, Pix, cartão ou transferência)."
        )
    return codigo


_COM_VIRGULA = re.compile(r"^(\d{1,3}(\.\d{3})+|\d+),(\d{1,2})$")
_INTEIRO = re.compile(r"^\d+$")
_COM_PONTO = re.compile(r"^(\d+)\.(\d{1,2})$")


def formatar_brl(centavos: int) -> str:
    reais, cent = divmod(centavos, 100)
    return f"R$ {reais:,}".replace(",", ".") + f",{cent:02d}"


def _centavos(texto: str) -> int:
    limpo = re.sub(r"\s+", "", texto)
    limpo = limpo[2:] if limpo[:2].upper() == "R$" else limpo
    if (m := _COM_VIRGULA.match(limpo)) is not None:
        inteiro, decimais = m.group(1).replace(".", ""), m.group(3)
    elif _INTEIRO.match(limpo):
        inteiro, decimais = limpo, ""
    elif (m := _COM_PONTO.match(limpo)) is not None:
        inteiro, decimais = m.group(1), m.group(2)
    else:
        raise ExecucaoFalhou(f"Valor inválido: {texto!r} (use, por exemplo, 200 ou 1.250,50).")
    centavos = int(inteiro) * 100 + int(decimais.ljust(2, "0") or 0)
    if not 0 < centavos <= MAX_CENTAVOS:
        raise ExecucaoFalhou("O valor deve ser maior que zero e dentro do limite permitido.")
    return centavos


def _quando(texto: str | None) -> datetime:
    agora = datetime.now(FUSO_PADRAO)
    if not texto:
        return agora
    try:
        dia = parse_dia(texto, agora.date())
    except ValueError:
        raise ExecucaoFalhou("Data inválida (use AAAA-MM-DD).") from None
    if dia > agora.date():
        raise ExecucaoFalhou("A data do pagamento não pode ser futura.")
    if dia == agora.date():
        return agora
    return datetime.combine(dia, time(12, 0), tzinfo=FUSO_PADRAO)


def _resolver(provider: Any, args: dict[str, Any]) -> Cliente:
    if args.get("cliente_id"):
        achados = [c for c in provider.buscar_clientes("", 50, 0) if c.id == args["cliente_id"]]
        if not achados:
            raise ExecucaoFalhou("Paciente não encontrado na Agenda.")
        return achados[0]
    alvo = _normalizar(args["paciente"])
    encontrados = provider.buscar_clientes(args["paciente"], 20, 0)
    exatos = [c for c in encontrados if _normalizar(c.nome) == alvo]
    candidatos = exatos or [c for c in encontrados if alvo in _normalizar(c.nome)]
    if not candidatos:
        raise ExecucaoFalhou("Paciente não encontrado na Agenda pelo nome informado.")
    if len({c.id for c in candidatos}) > 1:
        raise ExecucaoFalhou(
            "Mais de um paciente corresponde a esse nome; informe o nome completo."
        )
    return candidatos[0]


def _brl(valor: Any) -> str:
    return formatar_brl(round(float(valor or 0) * 100))


def _reais(valor: Any) -> int | None:
    try:
        return round(float(valor) * 100)
    except (TypeError, ValueError):
        return None


def descrever_recibo(recibo: Any) -> dict[str, Any]:
    if not isinstance(recibo, dict):
        return {}
    numero = recibo.get("number") or recibo.get("receipt_number")
    return {"recibo": recibo, "recibo_numero": numero} if numero else {"recibo": recibo}


def _texto_baixa(sujeito: str, resposta: dict[str, Any]) -> str:
    recibo = resposta.get("receipt")
    numero = recibo.get("number") if isinstance(recibo, dict) else None
    texto = f"{sujeito} registrado e baixado na Agenda"
    alocado = resposta.get("allocated_amount")
    credito = resposta.get("unallocated_amount")
    if alocado is not None and credito is not None:
        texto += f": {_brl(alocado)} aplicado às faturas e {_brl(credito)} como crédito"
    texto += "."
    if numero:
        texto += f" Recibo nº {numero} emitido."
    return texto


def tipo_registrar_pagamento(
    host: ModuleHost, abrir_provider: Callable[[ModuleHost], Any]
) -> TipoAcao:
    def resumir(args: dict[str, Any]) -> str:
        quem = args.get("paciente") or "o paciente informado"
        return f"Registrar na Agenda o pagamento de {args['valor']} ({args['forma']}) de {quem}"

    def carregar(ctx: ContextoAcao, args: dict[str, Any]) -> Previa:
        centavos = _centavos(args["valor"])
        forma = _forma(args["forma"])
        quando = _quando(args.get("data"))
        with abrir_provider(host) as provider:
            cliente = _resolver(provider, args)
            try:
                saldo = provider.saldo_cliente(cliente.id)
            except ExecucaoFalhou:
                saldo = None
        dados: dict[str, Any] = {
            "paciente": cliente.nome,
            "valor": formatar_brl(centavos),
            "forma": ROTULOS[forma],
            "data": f"{quando:%d/%m/%Y}",
        }
        if saldo:
            dados["saldo_vencido"] = _brl(saldo.get("outstanding"))
            dados["a_vencer_no_mes"] = _brl(saldo.get("accumulating"))
            dados["credito"] = _brl(saldo.get("credit"))
            bate = centavos in {_reais(saldo.get("outstanding")), _reais(saldo.get("accumulating"))}
            dados["aviso"] = (
                "O valor bate com o saldo do paciente: a Agenda dá baixa na hora e emite o recibo."
                if bate
                else "O valor não bate com o saldo de nenhuma fatura: a Agenda guarda o pagamento"
                " aguardando conferência da gerência, que dá a baixa e emite o recibo. Se for"
                " maior que o saldo, o excedente vira crédito do paciente."
            )
        if args.get("observacao"):
            dados["observacao"] = args["observacao"]
        return Previa(
            resumo=(
                f"Pagamento de {formatar_brl(centavos)} em {ROTULOS[forma]} de {cliente.nome},"
                f" em {quando:%d/%m/%Y}"
            ),
            dados=dados,
        )

    def executar(ctx: ContextoAcao, args: dict[str, Any]) -> dict[str, Any]:
        centavos = _centavos(args["valor"])
        forma = _forma(args["forma"])
        quando = _quando(args.get("data"))
        with abrir_provider(host) as provider:
            cliente = _resolver(provider, args)
            marca = hashlib.sha256(f"{cliente.id}|{centavos}|{forma}".encode()).hexdigest()[:8]
            resposta = provider.registrar_pagamento(
                cliente.id,
                centavos,
                forma,
                recebido_em=quando,
                referencia=f"brm-{ctx.acao_id}-{ctx.item}-{marca}",
                nota=args.get("observacao"),
                ator=ctx.ator,
                trace_id=ctx.trace_id,
            )
        valor = formatar_brl(centavos)
        pendente = resposta.get("status") == "PENDING_REVIEW"
        if resposta.get("duplicate"):
            texto = f"O pagamento de {valor} já estava registrado na Agenda."
        elif pendente:
            texto = (
                f"Pagamento de {valor} enviado à Agenda; ficou aguardando conferência da gerência"
                " (o valor não bate com nenhum saldo), sem recibo até lá."
            )
        else:
            texto = _texto_baixa(f"Pagamento de {valor}", resposta)
        return {
            "resumo": texto,
            "pagamento_id": resposta.get("payment_id") or resposta.get("id"),
            "status_agenda": resposta.get("status"),
            **descrever_recibo(resposta.get("receipt")),
        }

    return TipoAcao(
        nome="registrar_pagamento",
        titulo="Registrar pagamento na Agenda",
        descricao=(
            "Registra na Agenda o pagamento recebido de um paciente (o dinheiro da clínica fica"
            " na Agenda). Informe `paciente` (nome completo como o usuário digitou), `valor` em"
            " reais, `forma` (dinheiro, Pix, cartão ou transferência) e, se não for hoje, `data`"
            " (AAAA-MM-DD). Quem confirma confere o valor na página."
        ),
        esquema=RegistrarPagamentoArgs,
        resumir=resumir,
        carregar=carregar,
        executar=executar,
    )


class ConfirmarPagamentoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pagamento_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")


def tipo_confirmar_pagamento(
    host: ModuleHost, abrir_provider: Callable[[ModuleHost], Any]
) -> TipoAcao:
    def resumir(args: dict[str, Any]) -> str:
        return "Confirmar na Agenda um pagamento que aguarda conferência"

    def carregar(ctx: ContextoAcao, args: dict[str, Any]) -> Previa:
        return Previa(
            resumo="Confirmar pagamento aguardando conferência",
            dados={
                "pagamento_id": args["pagamento_id"],
                "aviso": (
                    "A Agenda dá baixa nas dívidas mais antigas primeiro, emite o recibo e o"
                    " excedente vira crédito do paciente."
                ),
            },
        )

    def executar(ctx: ContextoAcao, args: dict[str, Any]) -> dict[str, Any]:
        with abrir_provider(host) as provider:
            resposta = provider.confirmar_pagamento(
                args["pagamento_id"], ator=ctx.ator, trace_id=ctx.trace_id
            )
        return {
            "resumo": _texto_baixa("Pagamento", resposta),
            "pagamento_id": args["pagamento_id"],
            "status_agenda": resposta.get("status"),
            **descrever_recibo(resposta.get("receipt")),
        }

    return TipoAcao(
        nome="confirmar_pagamento",
        titulo="Confirmar pagamento na Agenda",
        descricao=(
            "Confirma na Agenda um pagamento que ficou aguardando conferência (valor que não bateu"
            " com nenhum saldo): dá a baixa e emite o recibo. Informe o `pagamento_id` devolvido"
            " ao registrar. Restrito ao gestor."
        ),
        esquema=ConfirmarPagamentoArgs,
        resumir=resumir,
        carregar=carregar,
        executar=executar,
        papel_minimo="admin",
    )
