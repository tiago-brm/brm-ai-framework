"""Provider da Agenda (spec 026): contrato de leitura e implementação HTTP.

`AgendaCadastroProvider` (spec 035) é o contrato da ficha completa, do histórico de status e da
gravação de campo faltante. O `HttpAgendaCadastroProvider` usa a ficha da Agenda
(`/api/v1/customers/:id/ficha`; pedidos G10, G11, G12 e G15 entregues); os testes usam o
`FakeAgendaProvider`."""

from __future__ import annotations

import json
import random
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from mcp_engine.core.executor import ExecucaoFalhou

STATUS_AGENDA = (
    "PENDING_CONFIRMATION",
    "WAITING",
    "CONFIRMED",
    "IN_PROGRESS",
    "COMPLETED",
    "CANCELLED",
)
STATUS_HISTORICO = (*STATUS_AGENDA, "NO_SHOW")
LIMITE_PAGINA = 200
MAX_PAGINAS = 500
MAX_BYTES = 5_000_000
ESPERA_MAX_S = 30.0
JANELA_AGENDAMENTO_DIAS = 120


class AgendaErro(ExecucaoFalhou):
    pass


class AgendaConfigInvalida(AgendaErro):
    pass


class AgendaNaoAutorizada(AgendaErro):
    pass


class AgendaIndisponivel(AgendaErro):
    pass


class AgendaRespostaInvalida(AgendaErro):
    pass


class AgendaRecursoIndisponivel(AgendaErro):
    """O recurso depende de um pedido ainda não entregue pela Agenda (G10-G15)."""


@dataclass(frozen=True)
class Agendamento:
    id: str
    customer_id: str
    customer_name: str
    customer_phone: str | None
    staff_id: str | None
    staff_name: str | None
    service_id: str | None
    service_name: str | None
    preco_centavos: int | None
    duracao_min: int | None
    inicio_utc: datetime
    status: str
    atualizado_em: datetime
    origem: str | None


@dataclass(frozen=True)
class Servico:
    id: str
    nome: str
    preco_centavos: int | None
    duracao_min: int | None
    disponivel: bool


@dataclass(frozen=True)
class Profissional:
    id: str
    nome: str
    funcao: str | None
    disponivel: bool


@dataclass(frozen=True)
class Cliente:
    id: str
    nome: str
    telefone: str | None


@dataclass(frozen=True)
class Endereco:
    cep: str | None = None
    logradouro: str | None = None
    numero: str | None = None
    complemento: str | None = None
    bairro: str | None = None
    cidade: str | None = None
    uf: str | None = None


@dataclass(frozen=True)
class Responsavel:
    nome: str | None = None
    cpf: str | None = None
    parentesco: str | None = None
    telefone: str | None = None
    email: str | None = None
    financeiro: bool = False


@dataclass(frozen=True)
class FichaCliente:
    id: str
    nome: str
    telefone: str | None = None
    email: str | None = None
    nascimento: date | None = None
    cpf: str | None = None
    endereco: Endereco | None = None
    responsaveis: tuple[Responsavel, ...] = ()


@dataclass(frozen=True)
class StatusSessao:
    agendamento_id: str
    status: str
    inicio_utc: datetime


class AgendaCadastroProvider(Protocol):
    def obter_agendamento(self, agendamento_id: str) -> Agendamento | None: ...

    def obter_cliente(
        self,
        customer_id: str,
        *,
        ator: str | None = None,
        trace_id: str | None = None,
        finalidade: str | None = None,
    ) -> FichaCliente | None: ...

    def historico_cliente(self, customer_id: str) -> list[StatusSessao]: ...

    def atualizar_cliente(
        self, customer_id: str, campos: dict[str, Any], *, ator: str, trace_id: str | None
    ) -> None: ...


class AgendaProvider(Protocol):
    def listar_agendamentos(
        self,
        *,
        de: datetime | None = None,
        ate: datetime | None = None,
        status: Iterable[str] = (),
        profissional_id: str | None = None,
        atualizado_desde: datetime | None = None,
    ) -> list[Agendamento]: ...

    def listar_servicos(self) -> list[Servico]: ...

    def listar_profissionais(self) -> list[Profissional]: ...

    def buscar_clientes(
        self, busca: str = "", limite: int = 20, offset: int = 0
    ) -> list[Cliente]: ...


def _invalida(motivo: str) -> AgendaRespostaInvalida:
    return AgendaRespostaInvalida(f"A Agenda devolveu um dado fora do contrato ({motivo}).")


def _texto(item: dict[str, Any], chave: str, *, obrigatorio: bool = False) -> str | None:
    valor = item.get(chave)
    if valor is None or valor == "":
        if obrigatorio:
            raise _invalida(f"{chave} ausente")
        return None
    if isinstance(valor, bool) or not isinstance(valor, str | int):
        raise _invalida(f"{chave} inválido")
    return str(valor).strip()[:200]


def _inteiro(item: dict[str, Any], chave: str) -> int | None:
    valor = item.get(chave)
    if valor is None:
        return None
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 0:
        raise _invalida(f"{chave} inválido")
    return valor


def _centavos(item: dict[str, Any], chave: str) -> int | None:
    valor = item.get(chave)
    if valor is None:
        return None
    if isinstance(valor, bool) or not isinstance(valor, int | float | str):
        raise _invalida(f"{chave} inválido")
    try:
        reais = Decimal(str(valor))
    except InvalidOperation:
        raise _invalida(f"{chave} inválido") from None
    if not reais.is_finite() or reais < 0 or reais > Decimal("1000000"):
        raise _invalida(f"{chave} fora da faixa")
    return int((reais * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _instante(item: dict[str, Any], chave: str) -> datetime:
    valor = item.get(chave)
    if not isinstance(valor, str):
        raise _invalida(f"{chave} ausente")
    try:
        dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        raise _invalida(f"{chave} inválido") from None
    if dt.tzinfo is None:
        raise _invalida(f"{chave} sem fuso")
    return dt.astimezone(UTC)


def _lista(corpo: dict[str, Any]) -> list[dict[str, Any]]:
    dados = corpo.get("data")
    if not isinstance(dados, list) or not all(isinstance(i, dict) for i in dados):
        raise _invalida("data")
    return dados


def _agendamento(item: dict[str, Any]) -> Agendamento:
    return Agendamento(
        id=_texto(item, "id", obrigatorio=True) or "",
        customer_id=_texto(item, "customer_id", obrigatorio=True) or "",
        customer_name=_texto(item, "customer_name", obrigatorio=True) or "",
        customer_phone=_texto(item, "customer_phone"),
        staff_id=_texto(item, "staff_id"),
        staff_name=_texto(item, "staff_name"),
        service_id=_texto(item, "service_id"),
        service_name=_texto(item, "service_name"),
        preco_centavos=_centavos(item, "service_price"),
        duracao_min=_inteiro(item, "duration_minutes"),
        inicio_utc=_instante(item, "scheduled_at"),
        status=_texto(item, "status", obrigatorio=True) or "",
        atualizado_em=_instante(item, "updated_at"),
        origem=_texto(item, "source"),
    )


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class HttpAgendaProvider:
    """Leitura da Agenda por HTTP. Só existe GET: não há método de escrita."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        dominios: Iterable[str] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        timeout_s: float = 10.0,
        max_bytes: int = MAX_BYTES,
        tentativas: int = 4,
    ) -> None:
        partes = urlsplit(base_url.strip())
        if partes.scheme != "https" or not partes.hostname or partes.username or partes.password:
            raise AgendaConfigInvalida("A URL da Agenda deve ser https e sem credenciais.")
        if dominios is not None and partes.hostname.lower() not in {d.lower() for d in dominios}:
            raise AgendaConfigInvalida("O host da Agenda não está entre os domínios permitidos.")
        if not api_key.strip():
            raise AgendaConfigInvalida("A chave de integração da Agenda está vazia.")
        self._sleep = sleep
        self._max_bytes = max_bytes
        self._tentativas = max(1, tentativas)
        self._client = httpx.Client(
            base_url=f"{partes.scheme}://{partes.netloc}{partes.path.rstrip('/')}/",
            headers={"Authorization": f"Bearer {api_key.strip()}", "Accept": "application/json"},
            timeout=httpx.Timeout(timeout_s, connect=min(5.0, timeout_s)),
            follow_redirects=False,
            transport=transport,
        )

    def __repr__(self) -> str:
        return "HttpAgendaProvider()"

    def fechar(self) -> None:
        self._client.close()

    def _espera(self, resposta: httpx.Response | None, tentativa: int) -> float:
        if resposta is not None:
            bruto = resposta.headers.get("retry-after", "")
            if bruto.strip().isdigit():
                return min(float(bruto), ESPERA_MAX_S)
        return min(0.5 * 2**tentativa + random.uniform(0, 0.25), ESPERA_MAX_S)

    def _ler(self, resposta: httpx.Response) -> dict[str, Any]:
        declarado = resposta.headers.get("content-length", "")
        if declarado.isdigit() and int(declarado) > self._max_bytes:
            raise AgendaRespostaInvalida("A resposta da Agenda excede o tamanho permitido.")
        corpo = bytearray()
        for pedaco in resposta.iter_bytes():
            corpo.extend(pedaco)
            if len(corpo) > self._max_bytes:
                raise AgendaRespostaInvalida("A resposta da Agenda excede o tamanho permitido.")
        try:
            dados = json.loads(bytes(corpo))
        except ValueError:
            raise _invalida("JSON") from None
        if not isinstance(dados, dict):
            raise _invalida("objeto esperado")
        return dados

    def _get(self, caminho: str, params: dict[str, Any]) -> dict[str, Any]:
        falha: AgendaErro = AgendaIndisponivel("A Agenda está indisponível no momento.")
        for tentativa in range(self._tentativas):
            resposta = None
            try:
                with self._client.stream("GET", caminho, params=params) as resposta:
                    codigo = resposta.status_code
                    if codigo == 200:
                        return self._ler(resposta)
                    if codigo in (401, 403):
                        raise AgendaNaoAutorizada(
                            "A Agenda recusou a chave de integração (inválida, expirada ou sem"
                            " permissão). Peça ao administrador para conferir a chave."
                        )
                    if 300 <= codigo < 400:
                        raise AgendaIndisponivel("A Agenda tentou redirecionar a consulta.")
                    if codigo == 429:
                        falha = AgendaIndisponivel(
                            "A Agenda limitou as consultas (muitas requisições). Tente de novo"
                            " em instantes."
                        )
                    elif codigo >= 500:
                        falha = AgendaIndisponivel("A Agenda está com erro no momento.")
                    else:
                        raise AgendaIndisponivel(f"A Agenda recusou a consulta (HTTP {codigo}).")
                    espera = self._espera(resposta, tentativa)
            except httpx.TimeoutException:
                falha = AgendaIndisponivel("A Agenda não respondeu a tempo.")
                espera = self._espera(None, tentativa)
            except httpx.HTTPError:
                falha = AgendaIndisponivel("Não foi possível falar com a Agenda.")
                espera = self._espera(None, tentativa)
            if tentativa + 1 < self._tentativas:
                self._sleep(espera)
        raise falha

    def listar_agendamentos(
        self,
        *,
        de: datetime | None = None,
        ate: datetime | None = None,
        status: Iterable[str] = (),
        profissional_id: str | None = None,
        atualizado_desde: datetime | None = None,
    ) -> list[Agendamento]:
        params: dict[str, Any] = {"limit": LIMITE_PAGINA}
        if de is not None:
            params["from"] = _iso(de)
        if ate is not None:
            params["to"] = _iso(ate)
        if atualizado_desde is not None:
            params["updated_since"] = _iso(atualizado_desde)
        filtro = [s.strip().upper() for s in status if s.strip()]
        if any(s not in STATUS_AGENDA for s in filtro):
            raise AgendaConfigInvalida("Status de agendamento desconhecido.")
        if filtro:
            params["status"] = ",".join(filtro)
        if profissional_id:
            try:
                params["staff_id"] = str(uuid.UUID(str(profissional_id)))
            except ValueError:
                raise AgendaConfigInvalida("Identificador de profissional inválido.") from None
        saida: list[Agendamento] = []
        vistos: set[str] = set()
        for _ in range(MAX_PAGINAS):
            corpo = self._get("api/v1/appointments", params)
            saida.extend(_agendamento(i) for i in _lista(corpo))
            proximo = corpo.get("next_cursor")
            if not proximo:
                return saida
            if not isinstance(proximo, str) or proximo in vistos:
                raise _invalida("cursor")
            vistos.add(proximo)
            params = {**params, "cursor": proximo}
        raise _invalida("paginação sem fim")

    def listar_servicos(self) -> list[Servico]:
        return [
            Servico(
                id=_texto(i, "id", obrigatorio=True) or "",
                nome=_texto(i, "name", obrigatorio=True) or "",
                preco_centavos=_centavos(i, "price"),
                duracao_min=_inteiro(i, "duration_minutes"),
                disponivel=i.get("available") is not False,
            )
            for i in _lista(self._get("api/v1/services", {}))
        ]

    def listar_profissionais(self) -> list[Profissional]:
        return [
            Profissional(
                id=_texto(i, "id", obrigatorio=True) or "",
                nome=_texto(i, "name", obrigatorio=True) or "",
                funcao=_texto(i, "role"),
                disponivel=i.get("available") is not False,
            )
            for i in _lista(self._get("api/v1/staff", {}))
        ]

    def buscar_clientes(self, busca: str = "", limite: int = 20, offset: int = 0) -> list[Cliente]:
        params = {"search": busca[:80], "limit": max(1, min(int(limite), 50)), "offset": offset}
        corpo = self._get("api/v1/customers", params)
        return [
            Cliente(
                id=_texto(i, "id", obrigatorio=True) or "",
                nome=_texto(i, "name", obrigatorio=True) or "",
                telefone=_texto(i, "phone"),
            )
            for i in _lista({"data": corpo.get("data", corpo.get("customers"))})
        ]


class HttpAgendaCadastroProvider(HttpAgendaProvider):
    """Ficha do paciente, histórico de status e gravação de campo faltante na Agenda.

    A leitura vem mascarada; só com ator e trace (e a finalidade) o CPF e o endereço completos
    são pedidos, o que a Agenda registra em `pii_access_log`. Escrever é sempre `PATCH` com
    `X-BRM-Actor` e `X-BRM-Trace-Id`."""

    def _chamar(
        self,
        metodo: str,
        caminho: str,
        *,
        params: dict[str, Any] | None = None,
        corpo: dict[str, Any] | None = None,
        ator: str | None = None,
        trace_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        cabecalhos = {}
        if ator:
            cabecalhos["X-BRM-Actor"] = ator[:200]
        if trace_id:
            cabecalhos["X-BRM-Trace-Id"] = trace_id[:100]
        tentativas = self._tentativas if metodo == "GET" else 1
        falha: AgendaErro = AgendaIndisponivel("A Agenda está indisponível no momento.")
        for tentativa in range(tentativas):
            espera = self._espera(None, tentativa)
            try:
                with self._client.stream(
                    metodo, caminho, params=params, json=corpo, headers=cabecalhos
                ) as resposta:
                    codigo = resposta.status_code
                    if codigo in (401, 403):
                        raise AgendaNaoAutorizada(
                            "A Agenda recusou a chave de integração (inválida, expirada ou sem"
                            " permissão). Peça ao administrador para conferir a chave."
                        )
                    if 300 <= codigo < 400:
                        raise AgendaIndisponivel("A Agenda tentou redirecionar a consulta.")
                    if codigo == 429 or codigo >= 500:
                        falha = AgendaIndisponivel(
                            "A Agenda está com erro ou limitou as consultas."
                        )
                        espera = self._espera(resposta, tentativa)
                    else:
                        try:
                            return codigo, self._ler(resposta)
                        except AgendaRespostaInvalida:
                            if codigo == 404:
                                return codigo, {}
                            raise
            except httpx.TimeoutException:
                falha = AgendaIndisponivel("A Agenda não respondeu a tempo.")
            except httpx.HTTPError:
                falha = AgendaIndisponivel("Não foi possível falar com a Agenda.")
            if tentativa + 1 < tentativas:
                self._sleep(espera)
        raise falha

    def _ficha_bruta(
        self,
        customer_id: str,
        *,
        ator: str | None = None,
        trace_id: str | None = None,
        finalidade: str | None = None,
    ) -> dict[str, Any] | None:
        caminho = f"api/v1/customers/{_id_cliente(customer_id)}/ficha"
        params: dict[str, Any] = {}
        if ator and trace_id:
            params["unmask"] = "1"
            if finalidade:
                params["purpose"] = finalidade[:200]
        codigo, corpo = self._chamar("GET", caminho, params=params, ator=ator, trace_id=trace_id)
        if codigo == 404:
            return None
        if codigo != 200:
            raise _recusa(codigo, corpo)
        dados = corpo.get("data")
        if not isinstance(dados, dict) or not isinstance(dados.get("paciente"), dict):
            raise _invalida("ficha")
        return dados

    def obter_agendamento(self, agendamento_id: str) -> Agendamento | None:
        agora = datetime.now(UTC)
        recentes = self.listar_agendamentos(
            de=agora - timedelta(days=JANELA_AGENDAMENTO_DIAS), ate=agora + timedelta(days=1)
        )
        return next((a for a in recentes if a.id == agendamento_id), None)

    def obter_cliente(
        self,
        customer_id: str,
        *,
        ator: str | None = None,
        trace_id: str | None = None,
        finalidade: str | None = None,
    ) -> FichaCliente | None:
        dados = self._ficha_bruta(customer_id, ator=ator, trace_id=trace_id, finalidade=finalidade)
        return None if dados is None else _ficha(dados)

    def historico_cliente(self, customer_id: str) -> list[StatusSessao]:
        dados = self._ficha_bruta(customer_id)
        if dados is None:
            return []
        itens = dados.get("historico")
        if not isinstance(itens, list):
            raise _invalida("historico")
        return sorted(
            (
                StatusSessao(
                    str(i.get("agendamento_id")), str(i.get("status")), _instante(i, "inicio_utc")
                )
                for i in itens
                if isinstance(i, dict)
            ),
            key=lambda s: s.inicio_utc,
        )

    def atualizar_cliente(
        self, customer_id: str, campos: dict[str, Any], *, ator: str, trace_id: str | None
    ) -> None:
        corpo = {k: v for k, v in campos.items() if k != "responsavel"}
        trace = trace_id or uuid.uuid4().hex
        if "responsavel" in campos:
            dados = self._ficha_bruta(customer_id)
            if dados is None:
                raise AgendaErro("Cliente não encontrado na Agenda.")
            corpo["responsaveis"] = _lista_responsaveis(
                dados.get("responsaveis"), campos["responsavel"]
            )
        codigo, resposta = self._chamar(
            "PATCH",
            f"api/v1/customers/{_id_cliente(customer_id)}/ficha",
            corpo=corpo,
            ator=ator,
            trace_id=trace,
        )
        if codigo == 404:
            raise AgendaErro("Cliente não encontrado na Agenda.")
        if codigo != 200:
            raise _recusa(codigo, resposta)


_RECUSAS = {
    "CPF_ALREADY_REGISTERED": "Este CPF já está cadastrado para outro paciente na Agenda.",
    "INVALID_CPF": "A Agenda recusou o CPF (dígitos verificadores inválidos).",
    "INVALID_CEP": "A Agenda recusou o CEP.",
    "INVALID_UF": "A Agenda recusou a UF.",
    "MULTIPLE_FINANCIAL_GUARDIANS": "Só um responsável pode ser o financeiro.",
}


def _id_cliente(customer_id: str) -> str:
    try:
        return str(uuid.UUID(str(customer_id)))
    except ValueError:
        raise AgendaConfigInvalida("Identificador de paciente inválido.") from None


def _recusa(codigo: int, corpo: dict[str, Any]) -> AgendaErro:
    motivo = _RECUSAS.get(str(corpo.get("code")))
    if motivo:
        return AgendaErro(motivo)
    return AgendaErro(f"A Agenda recusou o pedido (HTTP {codigo}).")


def _lista_responsaveis(existentes: Any, mudancas: dict[str, Any]) -> list[dict[str, Any]]:
    """A Agenda substitui a lista inteira: quem não aparece é removido. Mantém todos pelo `id`
    e aplica a mudança no responsável financeiro (ou no primeiro), como `montar_fontes`."""
    atuais = [r for r in (existentes or []) if isinstance(r, dict) and r.get("id")]
    if not atuais:
        if not mudancas.get("nome"):
            raise AgendaErro("Informe o nome do responsável para cadastrá-lo na Agenda.")
        return [{**mudancas, "financeiro": True}]
    alvo = next((r for r in atuais if r.get("financeiro")), atuais[0])
    return [{"id": r["id"], **(mudancas if r is alvo else {})} for r in atuais]


def _data(valor: Any) -> date | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        return date.fromisoformat(valor[:10])
    except ValueError:
        raise _invalida("data") from None


def _responsavel(item: dict[str, Any]) -> Responsavel:
    return Responsavel(
        nome=_texto(item, "nome"),
        cpf=_texto(item, "cpf"),
        parentesco=_texto(item, "parentesco"),
        telefone=_texto(item, "telefone"),
        email=_texto(item, "email"),
        financeiro=item.get("financeiro") is True,
    )


def _ficha(dados: dict[str, Any]) -> FichaCliente:
    p = dados["paciente"]
    ender = p.get("endereco")
    endereco = None
    if isinstance(ender, dict):
        endereco = Endereco(
            **{
                c: _texto(ender, c)
                for c in ("cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf")
            }
        )
    return FichaCliente(
        id=_texto(p, "id", obrigatorio=True) or "",
        nome=_texto(p, "nome", obrigatorio=True) or "",
        telefone=_texto(p, "telefone"),
        email=_texto(p, "email"),
        nascimento=_data(p.get("nascimento")),
        cpf=_texto(p, "cpf"),
        endereco=endereco,
        responsaveis=tuple(
            _responsavel(r) for r in (dados.get("responsaveis") or []) if isinstance(r, dict)
        ),
    )
