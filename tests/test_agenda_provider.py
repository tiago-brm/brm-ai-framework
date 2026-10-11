import logging
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from mcp_engine.modules.agenda import provider as prov
from mcp_engine.modules.agenda.provider import (
    AgendaConfigInvalida,
    AgendaIndisponivel,
    AgendaNaoAutorizada,
    AgendaRespostaInvalida,
    HttpAgendaProvider,
)
from mcp_engine.modules.agenda.tempo import limites_utc

CHAVE = "chave-secreta-xyz-123"
BASE = "https://agenda.brmsolutions.com.br/api-base"
HOST = "agenda.brmsolutions.com.br"

ITEM = {
    "id": "a1",
    "customer_id": "c1",
    "customer_name": "Maria Silva",
    "customer_phone": "11999990001",
    "staff_id": "s1",
    "staff_name": "Ana",
    "service_id": "sv1",
    "service_name": "Sessão",
    "service_price": 150.1,
    "duration_minutes": 50,
    "scheduled_at": "2026-10-09T13:00:00Z",
    "status": "CONFIRMED",
    "updated_at": "2026-10-08T10:00:00Z",
    "source": "web",
}


def montar(handler, **kw):
    sono = []
    p = HttpAgendaProvider(
        BASE,
        CHAVE,
        dominios=[HOST],
        transport=httpx.MockTransport(handler),
        sleep=sono.append,
        **kw,
    )
    return p, sono


def json_ok(dados, cursor=None):
    return httpx.Response(200, json={"data": dados, "next_cursor": cursor})


def test_requisicao_so_get_com_bearer_parametros_e_paginacao():
    vistas = []

    def handler(req):
        vistas.append(req)
        if "cursor" not in req.url.params:
            return json_ok([ITEM], cursor="abc")
        return json_ok([{**ITEM, "id": "a2"}])

    p, _ = montar(handler)
    itens = p.listar_agendamentos(
        de=datetime(2026, 10, 9, 3, tzinfo=UTC),
        ate=datetime(2026, 10, 10, 3, tzinfo=UTC),
        status=["completed", "CANCELLED"],
        profissional_id="123e4567-e89b-12d3-a456-426614174000",
        atualizado_desde=datetime(2026, 10, 1, tzinfo=UTC),
    )
    assert [i.id for i in itens] == ["a1", "a2"]
    assert itens[0].preco_centavos == 15010
    assert {r.method for r in vistas} == {"GET"}
    assert all(r.headers["authorization"] == f"Bearer {CHAVE}" for r in vistas)
    q = vistas[0].url.params
    assert q["from"] == "2026-10-09T03:00:00Z" and q["to"] == "2026-10-10T03:00:00Z"
    assert q["status"] == "COMPLETED,CANCELLED" and q["limit"] == "200"
    assert q["updated_since"] == "2026-10-01T00:00:00Z"
    assert vistas[1].url.params["cursor"] == "abc"
    assert vistas[0].url.path == "/api-base/api/v1/appointments"


def test_provider_so_tem_metodos_de_leitura():
    publicos = {n for n in dir(HttpAgendaProvider) if not n.startswith("_")}
    assert publicos == {
        "listar_agendamentos",
        "listar_servicos",
        "listar_profissionais",
        "buscar_clientes",
        "fechar",
    }
    for proibido in ("criar", "cancelar", "remarcar", "post", "put", "delete", "escrever"):
        assert not any(proibido in n for n in publicos)


def test_catalogos_e_clientes():
    def handler(req):
        if req.url.path.endswith("/services"):
            return json_ok([{"id": "sv", "name": "Sessão", "price": 200, "available": True}])
        if req.url.path.endswith("/staff"):
            return json_ok([{"id": "s", "name": "Ana", "role": "psi", "available": False}])
        assert req.url.params["search"] == "mar" and req.url.params["limit"] == "20"
        return httpx.Response(200, json={"data": [{"id": "c", "name": "Maria", "phone": "1"}]})

    p, _ = montar(handler)
    assert p.listar_servicos()[0].preco_centavos == 20000
    assert p.listar_profissionais()[0].disponivel is False
    assert p.buscar_clientes("mar", 20)[0].nome == "Maria"


def test_401_nao_repete_e_nao_vaza():
    chamadas = []

    def handler(req):
        chamadas.append(req)
        return httpx.Response(401, json={"error": f"chave {CHAVE} em {req.url}"})

    p, sono = montar(handler)
    with pytest.raises(AgendaNaoAutorizada) as exc:
        p.listar_servicos()
    assert len(chamadas) == 1 and sono == []
    assert CHAVE not in exc.value.reason and HOST not in exc.value.reason


def test_429_respeita_retry_after_e_depois_conclui():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        if n["i"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return json_ok([])

    p, sono = montar(handler)
    assert p.listar_servicos() == []
    assert sono == [2.0]


def test_retry_after_enorme_e_limitado():
    p, sono = montar(lambda req: httpx.Response(429, headers={"Retry-After": "86400"}))
    with pytest.raises(AgendaIndisponivel) as exc:
        p.listar_servicos()
    assert max(sono) == prov.ESPERA_MAX_S and "limitou" in exc.value.reason


def test_5xx_persistente_faz_backoff_e_falha_com_mensagem_limpa():
    p, sono = montar(lambda req: httpx.Response(503, text=f"boom {CHAVE} {req.url}"))
    with pytest.raises(AgendaIndisponivel) as exc:
        p.listar_servicos()
    assert len(sono) == 3 and sono == sorted(sono)
    assert CHAVE not in exc.value.reason and HOST not in exc.value.reason


def test_timeout_e_erro_de_rede_viram_indisponivel_sem_url():
    def tempo(req):
        raise httpx.ReadTimeout(f"timeout em {req.url}")

    def rede(req):
        raise httpx.ConnectError(f"falha {req.url} com {CHAVE}")

    for handler in (tempo, rede):
        p, _ = montar(handler)
        with pytest.raises(AgendaIndisponivel) as exc:
            p.listar_servicos()
        assert CHAVE not in exc.value.reason and HOST not in exc.value.reason
        assert exc.value.__cause__ is None


def test_redirect_nao_e_seguido():
    chamadas = []

    def handler(req):
        chamadas.append(req)
        return httpx.Response(302, headers={"Location": "https://mal.example/steal"})

    p, _ = montar(handler)
    with pytest.raises(AgendaIndisponivel):
        p.listar_servicos()
    assert len(chamadas) == 1


def test_resposta_gigante_e_recusada_com_e_sem_content_length():
    p, _ = montar(lambda req: httpx.Response(200, content=b"x" * 5000), max_bytes=1000)
    with pytest.raises(AgendaRespostaInvalida, match="tamanho"):
        p.listar_servicos()

    def fluxo(req):
        def pedacos():
            for _ in range(50):
                yield b"y" * 100

        return httpx.Response(200, content=pedacos())

    p2, _ = montar(fluxo, max_bytes=1000)
    with pytest.raises(AgendaRespostaInvalida, match="tamanho"):
        p2.listar_servicos()


@pytest.mark.parametrize(
    "corpo",
    [
        b"nao e json",
        b"[1,2]",
        b'{"data": "x"}',
        b'{"data": [1]}',
        b'{"data": [{"id": "a"}]}',
    ],
)
def test_resposta_fora_do_contrato(corpo):
    p, _ = montar(lambda req: httpx.Response(200, content=corpo))
    with pytest.raises(AgendaRespostaInvalida):
        p.listar_agendamentos()


@pytest.mark.parametrize("preco", [-1, "abc", True, 1e12, float("nan")])
def test_preco_invalido(preco):
    def handler(req):
        return httpx.Response(
            200, content=prov.json.dumps({"data": [{**ITEM, "service_price": preco}]}).encode()
        )

    p, _ = montar(handler)
    with pytest.raises(AgendaRespostaInvalida):
        p.listar_agendamentos()


def test_data_sem_fuso_e_cursor_repetido():
    p, _ = montar(lambda req: json_ok([{**ITEM, "scheduled_at": "2026-10-09T13:00:00"}]))
    with pytest.raises(AgendaRespostaInvalida):
        p.listar_agendamentos()
    p2, _ = montar(lambda req: json_ok([ITEM], cursor="mesmo"))
    with pytest.raises(AgendaRespostaInvalida, match="cursor"):
        p2.listar_agendamentos()


@pytest.mark.parametrize(
    "url",
    [
        "http://agenda.brmsolutions.com.br",
        "https://outro.example.com",
        "https://user:senha@agenda.brmsolutions.com.br",
        "agenda.brmsolutions.com.br",
    ],
)
def test_url_invalida_e_recusada_sem_ecoar(url):
    with pytest.raises(AgendaConfigInvalida) as exc:
        HttpAgendaProvider(url, CHAVE, dominios=[HOST])
    assert "senha" not in exc.value.reason and "outro.example" not in exc.value.reason


def test_chave_vazia_e_parametros_invalidos():
    with pytest.raises(AgendaConfigInvalida):
        HttpAgendaProvider(BASE, "  ", dominios=[HOST])
    p, _ = montar(lambda req: json_ok([]))
    with pytest.raises(AgendaConfigInvalida):
        p.listar_agendamentos(status=["DROP"])
    with pytest.raises(AgendaConfigInvalida):
        p.listar_agendamentos(profissional_id="1; drop table")


def test_chave_e_url_fora_do_repr_e_dos_logs(caplog):
    caplog.set_level(logging.DEBUG)
    p, _ = montar(lambda req: httpx.Response(500))
    with pytest.raises(AgendaIndisponivel):
        p.listar_servicos()
    assert CHAVE not in repr(p) and HOST not in repr(p)
    assert CHAVE not in caplog.text


def test_fuso_virada_do_dia_em_sao_paulo():
    sp = ZoneInfo("America/Sao_Paulo")
    ini, fim = limites_utc(date(2026, 10, 9), date(2026, 10, 9), sp)
    assert ini == datetime(2026, 10, 9, 3, tzinfo=UTC)
    assert fim == datetime(2026, 10, 10, 3, tzinfo=UTC)
    tarde_da_noite = datetime(2026, 10, 10, 2, 30, tzinfo=UTC)
    meia_noite = datetime(2026, 10, 10, 3, 0, tzinfo=UTC)
    assert ini <= tarde_da_noite < fim and not ini <= meia_noite < fim


def test_fuso_horario_de_verao_dias_com_23_e_25_horas():
    ny = ZoneInfo("America/New_York")
    ini, fim = limites_utc(date(2026, 3, 8), date(2026, 3, 8), ny)
    assert (fim - ini).total_seconds() == 23 * 3600
    ini, fim = limites_utc(date(2026, 11, 1), date(2026, 11, 1), ny)
    assert (fim - ini).total_seconds() == 25 * 3600
    sp = ZoneInfo("America/Sao_Paulo")
    ini, fim = limites_utc(date(2018, 11, 4), date(2018, 11, 4), sp)
    assert ini == datetime(2018, 11, 4, 3, tzinfo=UTC)
    assert (fim - ini).total_seconds() == 23 * 3600
