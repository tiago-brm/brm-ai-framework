"""Contrato de sessão (spec 035): tipo de ação, fatos derivados e lista de pendentes.

O framework não copia o cadastro: a ficha é lida da Agenda na hora (G10/G11), o campo faltante
é gravado na Agenda com ator e trace_id (G12) e o histórico de status vem da Agenda (G15). A
única cópia dos dados fica dentro do documento emitido.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from mcp_engine.core.acoes import CampoFaltante, ContextoAcao, Previa, TipoAcao
from mcp_engine.core.documentos import DocumentoErro, cpf_valido
from mcp_engine.core.executor import ExecucaoFalhou, Handler, HandlerContext
from mcp_engine.modules import ModuleHost
from mcp_engine.modules.agenda.provider import (
    JANELA_AGENDAMENTO_DIAS,
    AgendaConfigInvalida,
    Agendamento,
    AgendaRecursoIndisponivel,
    FichaCliente,
)
from mcp_engine.modules.agenda.sincronizacao import _normalizar
from mcp_engine.modules.agenda.tempo import FUSO_PADRAO, limites_utc, para_local, parse_dia

MODELO_CONTRATO = "contrato-sessao"
GESTORES = ("admin", "super_admin")
MAX_DIAS = 62
MAX_ITENS = 100
FATOS_CONTRATO = ("primeira_sessao_concluida", "contrato_emitido")
_UFS = frozenset(
    "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_EDITAVEIS: dict[str, tuple[str, ...]] = {
    "paciente.cpf": ("cpf",),
    "paciente.nascimento": ("nascimento",),
    **{
        f"paciente.endereco.{c}": ("endereco", c)
        for c in ("cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf")
    },
    **{
        f"responsavel.{c}": ("responsavel", c)
        for c in ("nome", "cpf", "parentesco", "telefone", "email")
    },
    **{
        f"responsavel.endereco.{c}": ("responsavel", "endereco", c)
        for c in ("cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf")
    },
}


def referencia_cliente(customer_id: str) -> str:
    return f"agenda:cliente:{customer_id}"


def _idade(nascimento: date, em: date) -> int:
    return em.year - nascimento.year - ((em.month, em.day) < (nascimento.month, nascimento.day))


def _numero_crp(valor: str | None) -> str | None:
    """Número do CRP sem o prefixo: "CRP 14/03059-0" e "14/03059-0" viram "14/03059-0"."""
    if not valor:
        return None
    limpo = re.sub(r"^\s*CRP[\s:.-]*", "", valor, flags=re.IGNORECASE).strip()
    return limpo or None


def crp_do_profissional(
    emissor: Mapping[str, str], nome: str | None, da_agenda: str | None = None
) -> str | None:
    """CRP do profissional: o cadastrado na Agenda e, na falta dele, o da configuração do
    cliente (chave `crp_<nome_sem_acento_com__>`)."""
    if da_agenda:
        return _numero_crp(da_agenda)
    if not nome:
        return None
    return _numero_crp(emissor.get("crp_" + _normalizar(nome).replace(" ", "_")))


def _endereco_dict(endereco: Any) -> dict[str, Any]:
    return {
        c: getattr(endereco, c) if endereco else None
        for c in ("cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf")
    }


def montar_fontes(
    ag: Agendamento,
    ficha: FichaCliente,
    livre: dict[str, str] | None = None,
    crp: str | None = None,
) -> dict[str, Any]:
    inicio = para_local(ag.inicio_utc, FUSO_PADRAO)
    menor = None if ficha.nascimento is None else _idade(ficha.nascimento, inicio.date()) < 18
    responsavel = next((r for r in ficha.responsaveis if r.financeiro), None) or (
        ficha.responsaveis[0] if ficha.responsaveis else None
    )
    endereco = ficha.endereco
    e_responsavel = bool(menor and responsavel) or bool(responsavel and responsavel.financeiro)
    return {
        "livre": dict(livre or {}),
        "paciente": {
            "nome": ficha.nome,
            "cpf": ficha.cpf,
            "nascimento": ficha.nascimento.isoformat() if ficha.nascimento else None,
            "telefone": ficha.telefone,
            "email": ficha.email,
            "menor_de_idade": menor,
            "endereco": _endereco_dict(endereco),
        },
        "responsavel": {
            **{
                c: getattr(responsavel, c) if responsavel else None
                for c in ("nome", "cpf", "parentesco", "telefone", "email")
            },
            "endereco": _endereco_dict(responsavel.endereco if responsavel else None),
        },
        "contratante": {"e_responsavel": e_responsavel, "e_paciente": not e_responsavel},
        "profissional": {"nome": ag.staff_name, "crp": crp},
        "emissao": {"data": datetime.now(FUSO_PADRAO).date().isoformat()},
        "sessao": {
            "agendamento_id": ag.id,
            "data": inicio.date().isoformat(),
            "hora": inicio.strftime("%H:%M"),
            "profissional": ag.staff_name,
            "servico": ag.service_name,
            "preco": ag.preco_centavos,
            "duracao_min": ag.duracao_min,
            "status": ag.status,
        },
    }


def _validar_campo(origem: str, valor: str) -> str:
    folha = origem.rsplit(".", 1)[-1]
    valor = valor.strip()
    if folha == "cpf":
        if not cpf_valido(valor):
            raise ExecucaoFalhou("CPF inválido.")
        return re.sub(r"\D", "", valor)
    if folha == "cep":
        digitos = re.sub(r"\D", "", valor)
        if len(digitos) != 8:
            raise ExecucaoFalhou("CEP inválido (8 dígitos).")
        return digitos
    if folha == "uf":
        if valor.upper() not in _UFS:
            raise ExecucaoFalhou("UF inválida.")
        return valor.upper()
    if folha == "nascimento":
        try:
            dia = date.fromisoformat(valor)
        except ValueError:
            raise ExecucaoFalhou("Data de nascimento inválida (AAAA-MM-DD).") from None
        if dia > date.today() or dia.year < 1900:
            raise ExecucaoFalhou("Data de nascimento fora da faixa.")
        return dia.isoformat()
    if folha == "email":
        if not _EMAIL.match(valor):
            raise ExecucaoFalhou("E-mail inválido.")
        return valor.lower()
    if folha == "telefone":
        digitos = re.sub(r"\D", "", valor)
        if not 10 <= len(digitos) <= 13:
            raise ExecucaoFalhou("Telefone inválido.")
        return digitos
    if len(valor) > 120:
        raise ExecucaoFalhou("Valor longo demais.")
    return valor


class EmitirContratoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agendamento_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    paciente: str | None = Field(default=None, min_length=2, max_length=80)
    modelo: str = Field(default=MODELO_CONTRATO, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    substitui: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    valores: dict[Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,47}$")], str] = (
        Field(default_factory=dict, max_length=20)
    )

    @model_validator(mode="after")
    def _uma_referencia(self) -> EmitirContratoArgs:
        if (self.agendamento_id is None) == (self.paciente is None):
            raise ValueError("informe agendamento_id ou paciente (um dos dois)")
        return self


def sessoes_do_paciente(provider: Any, nome: str) -> list[Agendamento]:
    agora = datetime.now(UTC)
    itens = provider.listar_agendamentos(
        de=agora - timedelta(days=JANELA_AGENDAMENTO_DIAS), ate=agora + timedelta(days=30)
    )
    alvo = _normalizar(nome)
    achados = [a for a in itens if a.status != "CANCELLED" and alvo in _normalizar(a.customer_name)]
    achados = [a for a in achados if _normalizar(a.customer_name) == alvo] or achados
    if not achados:
        raise ExecucaoFalhou(
            f"Nenhuma sessão desse paciente nos últimos {JANELA_AGENDAMENTO_DIAS} dias na Agenda."
        )
    if len({a.customer_id for a in achados}) > 1:
        raise ExecucaoFalhou(
            "Mais de um paciente corresponde a esse nome; informe o nome completo."
        )
    return achados


def resolver_paciente(provider: Any, nome: str) -> Agendamento:
    achados = sessoes_do_paciente(provider, nome)
    concluidas = [a for a in achados if a.status == "COMPLETED"]
    return max(concluidas or achados, key=lambda a: a.inicio_utc)


def tipo_emitir_contrato(host: ModuleHost, abrir_provider: Callable[[ModuleHost], Any]) -> TipoAcao:
    def buscar(args: dict[str, Any]) -> Agendamento:
        with abrir_provider(host) as provider:
            if not args.get("agendamento_id"):
                return resolver_paciente(provider, args["paciente"])
            ag = provider.obter_agendamento(args["agendamento_id"])
        if ag is None:
            raise ExecucaoFalhou("Agendamento não encontrado na Agenda.")
        return ag

    def ler(
        ctx: ContextoAcao, args: dict[str, Any]
    ) -> tuple[Agendamento, FichaCliente, str | None]:
        ag = buscar(args)
        with abrir_provider(host) as provider:
            ficha = provider.obter_cliente(
                ag.customer_id,
                ator=ctx.ator,
                trace_id=ctx.trace_id,
                finalidade=f"contrato de sessão {args['modelo']}",
            )
            crp = next(
                (p.crp for p in provider.listar_profissionais() if p.id == ag.staff_id), None
            )
        if ficha is None:
            raise ExecucaoFalhou("Paciente do agendamento não encontrado na Agenda.")
        return ag, ficha, crp_do_profissional(ctx.documentos.emissor, ag.staff_name, crp)

    def resumir(args: dict[str, Any]) -> str:
        alvo = (
            f"da sessão {args['agendamento_id']}"
            if args.get("agendamento_id")
            else f"do paciente {args['paciente']}"
        )
        texto = f"Emitir contrato ({args['modelo']}) {alvo}"
        if args.get("substitui"):
            texto += f", corrigindo o documento {args['substitui']}"
        return texto

    def carregar(ctx: ContextoAcao, args: dict[str, Any]) -> Previa:
        ag, ficha, crp = ler(ctx, args)
        fontes = montar_fontes(ag, ficha, args.get("valores"), crp)
        try:
            linhas = ctx.documentos.previa(args["modelo"], fontes)
        except DocumentoErro as exc:
            raise ExecucaoFalhou(str(exc)) from None
        faltantes = tuple(
            CampoFaltante(
                nome=linha["campo"],
                rotulo=linha["rotulo"],
                tipo=linha["tipo"],
                editavel=linha["origem"] in _EDITAVEIS,
            )
            for linha in linhas
            if linha["faltante"] or linha.get("invalido")
        )
        anteriores = ctx.documentos.emitidos(referencia_cliente(ag.customer_id), args["modelo"])
        sessao = fontes["sessao"]
        return Previa(
            resumo=(
                f"Contrato de {ficha.nome}: sessão de {sessao['data']} às {sessao['hora']}"
                f" com {sessao['profissional'] or 'profissional não informado'}"
            ),
            dados={
                "modelo": args["modelo"],
                "menor_de_idade": fontes["paciente"]["menor_de_idade"],
                "campos": linhas,
                "contratos_anteriores": [d["documento_id"] for d in anteriores],
            },
            faltantes=faltantes,
        )

    def gravar_campos(ctx: ContextoAcao, args: dict[str, Any], valores: dict[str, str]) -> None:
        try:
            modelo = ctx.documentos.modelo(args["modelo"])
        except DocumentoErro as exc:
            raise ExecucaoFalhou(str(exc)) from None
        origens = {c.nome: c.origem for c in modelo.campos}
        campos: dict[str, Any] = {}
        for nome, valor in valores.items():
            caminho = _EDITAVEIS.get(origens.get(nome, ""))
            if caminho is None:
                raise ExecucaoFalhou(f"O campo {nome!r} não é gravado na Agenda.")
            limpo = _validar_campo(origens[nome], valor)
            pasta = campos
            for parte in caminho[:-1]:
                pasta = pasta.setdefault(parte, {})
            pasta[caminho[-1]] = limpo
        ag = buscar(args)
        with abrir_provider(host) as provider:
            provider.atualizar_cliente(ag.customer_id, campos, ator=ctx.ator, trace_id=ctx.trace_id)

    def executar(ctx: ContextoAcao, args: dict[str, Any]) -> dict[str, Any]:
        ag, ficha, crp = ler(ctx, args)
        try:
            doc = ctx.emitir_documento(
                args["modelo"],
                montar_fontes(ag, ficha, args.get("valores"), crp),
                referencia=referencia_cliente(ag.customer_id),
                metadados={"agendamento_id": ag.id, "customer_id": ag.customer_id},
                substitui=args.get("substitui"),
            )
        except DocumentoErro as exc:
            raise ExecucaoFalhou(str(exc)) from None
        return {"resumo": "Contrato emitido.", "documento_id": doc.id, "sha256": doc.sha256}

    return TipoAcao(
        nome="emitir_contrato_sessao",
        titulo="Emitir contrato de sessão",
        descricao=(
            "Gera o PDF do contrato a partir do modelo aprovado e dos dados lidos da Agenda no"
            " momento; campo faltante é preenchido na página e gravado na Agenda. Informe o"
            " `paciente` (nome como o usuário digitou; a sessão mais recente dele é usada) ou o"
            " `agendamento_id`. O modelo padrão é contrato-sessao."
        ),
        esquema=EmitirContratoArgs,
        resumir=resumir,
        carregar=carregar,
        executar=executar,
        gravar_campos=gravar_campos,
    )


def _cliente_por_nome(host: ModuleHost, nome: str) -> str | None:
    con = host.connect()
    try:
        linhas = con.execute(
            "SELECT customer_id FROM cliente_espelho WHERE nome_norm = ? LIMIT 2",
            (_normalizar(nome),),
        ).fetchall()
    finally:
        con.close()
    return linhas[0][0] if len(linhas) == 1 else None


def fatos_contrato(
    host: ModuleHost, abrir_provider: Callable[[ModuleHost], Any]
) -> Callable[..., dict[str, Any]]:
    """`primeira_sessao_concluida` (há sessão COMPLETED; NO_SHOW não conta) e `contrato_emitido`.

    Lê `cliente_agenda_id` ou, na falta, `paciente` pelo espelho de nomes. Cliente desconhecido ou
    histórico ainda não oferecido pela Agenda (G15) dão fato nulo; Agenda fora do ar falha (e o
    motor nega a chamada)."""

    def calcular(ctx: Any, args: Any) -> dict[str, Any]:
        customer_id = args.get("cliente_agenda_id")
        if not customer_id and args.get("paciente"):
            customer_id = _cliente_por_nome(host, str(args["paciente"]))
        if not customer_id:
            return dict.fromkeys(FATOS_CONTRATO)
        emitido = bool(
            host.documentos_emitidos(referencia_cliente(str(customer_id)), MODELO_CONTRATO)
        )
        try:
            with abrir_provider(host) as provider:
                historico = provider.historico_cliente(str(customer_id))
        except (AgendaRecursoIndisponivel, AgendaConfigInvalida):
            return {"primeira_sessao_concluida": None, "contrato_emitido": emitido}
        concluida = any(s.status == "COMPLETED" for s in historico)
        return {"primeira_sessao_concluida": concluida, "contrato_emitido": emitido}

    return calcular


def handler_contratos_pendentes(
    host: ModuleHost, abrir_provider: Callable[[ModuleHost], Any], hoje: Callable[[], date]
) -> Handler:
    def contratos_pendentes(ctx: HandlerContext, args: dict[str, Any]) -> dict[str, Any]:
        dia = hoje()
        try:
            de = parse_dia(args.get("de"), dia - timedelta(days=6))
            ate = parse_dia(args.get("ate"), dia)
        except ValueError as exc:
            raise ExecucaoFalhou(str(exc)) from None
        if ate < de or (ate - de).days >= MAX_DIAS:
            raise ExecucaoFalhou(f"Período inválido (no máximo {MAX_DIAS} dias).")
        inicio, fim = limites_utc(de, ate, FUSO_PADRAO)
        with abrir_provider(host) as provider:
            concluidas = provider.listar_agendamentos(de=inicio, ate=fim, status=["COMPLETED"])
        ultima: dict[str, Agendamento] = {}
        for ag in sorted(concluidas, key=lambda a: a.inicio_utc):
            ultima[ag.customer_id] = ag
        pendentes = [
            ag
            for cid, ag in ultima.items()
            if not host.documentos_emitidos(referencia_cliente(cid), MODELO_CONTRATO)
        ]
        resposta: dict[str, Any] = {
            "periodo": {"de": de.isoformat(), "ate": ate.isoformat()},
            "total": len(pendentes),
        }
        if ctx.role in GESTORES:
            resposta["pendentes"] = [
                {
                    "agendamento_id": ag.id,
                    "cliente": ag.customer_name,
                    "profissional": ag.staff_name,
                    "data": para_local(ag.inicio_utc, FUSO_PADRAO).date().isoformat(),
                }
                for ag in pendentes[:MAX_ITENS]
            ]
        else:
            resposta["agregado"] = True
        return resposta

    return contratos_pendentes
