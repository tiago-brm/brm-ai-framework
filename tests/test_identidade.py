"""Provedores de identidade (spec 035, fase 1)."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from mcp_engine.core.auth import Role
from mcp_engine.core.executor import SecretStore
from mcp_engine.core.http_app import create_app
from mcp_engine.core.identidade import (
    AgendaIdentityProvider,
    IdentidadeRecusada,
    LocalIdentityProvider,
    ResolvedorIdentidade,
)
from mcp_engine.core.mcp_server import BRMEngine
from mcp_engine.core.models import IdentidadeAgenda, IdentidadeConfig
from mcp_engine.core.user_store import SQLUserStore

SEGREDO = "segredo-de-teste-com-mais-de-32-bytes-0123456789"
EMISSOR = "https://agenda.exemplo.test/auth/v1"


def token(
    *,
    papel: str | None = "ADMIN",
    segredo: str = SEGREDO,
    exp: int | None = None,
    iss: str = EMISSOR,
    aud: str = "authenticated",
    alg: str = "HS256",
    **extra,
) -> str:
    agora = int(time.time())
    claims = {
        "sub": "11111111-2222-3333-4444-555555555555",
        "email": "Joana@Clinica.test",
        "iss": iss,
        "aud": aud,
        "iat": agora - 10,
        "exp": exp if exp is not None else agora + 600,
        "aal": "aal1",
        "amr": [{"method": "password", "timestamp": agora - 60}],
        **extra,
    }
    if papel is not None:
        claims["app_metadata"] = {"role": papel, "tenant_id": "t1"}
    return jwt.encode(claims, segredo, algorithm=alg)


@pytest.fixture
def segredos(tmp_path: Path) -> SecretStore:
    pasta = tmp_path / "segredos" / "cli"
    pasta.mkdir(parents=True)
    (pasta / "agenda_jwt_secret").write_text(SEGREDO)
    return SecretStore(tmp_path / "segredos").for_client("cli")


def provedor(segredos: SecretStore, **cfg) -> AgendaIdentityProvider:
    return AgendaIdentityProvider(IdentidadeAgenda(emissor=EMISSOR, **cfg), segredos)


class TestAgendaJWT:
    def test_token_valido_mapeia_papel_e_extrai_claims(self, segredos) -> None:
        ident = provedor(segredos).autenticar(token())
        assert ident is not None
        assert ident.perfil.identity == "joana@clinica.test"
        assert ident.perfil.role is Role.ADMIN
        assert ident.id == "11111111-2222-3333-4444-555555555555"
        assert ident.papel_origem == "ADMIN" and ident.aal == "aal1"
        assert ident.amr == ("password",)
        assert ident.autenticado_em is not None and ident.autenticacao_recente(5)
        assert ident.provedor == "agenda" and ident.metodo == "jwt"

    @pytest.mark.parametrize(
        ("papel", "esperado"),
        [("SUPER_ADMIN", Role.SUPER_ADMIN), ("MASTER", Role.ADMIN), ("USER", Role.VIEWER)],
    )
    def test_mapeamento_padrao(self, segredos, papel, esperado) -> None:
        assert provedor(segredos).autenticar(token(papel=papel)).perfil.role is esperado

    def test_readonly_sem_acesso_por_padrao(self, segredos) -> None:
        with pytest.raises(IdentidadeRecusada, match="papel sem acesso"):
            provedor(segredos).autenticar(token(papel="READONLY"))

    def test_mapeamento_configuravel(self, segredos) -> None:
        p = provedor(segredos, papeis={"USER": "admin"})
        assert p.autenticar(token(papel="USER")).perfil.role is Role.ADMIN
        with pytest.raises(IdentidadeRecusada):
            p.autenticar(token(papel="ADMIN"))

    def test_expirado(self, segredos) -> None:
        with pytest.raises(IdentidadeRecusada, match="expirado"):
            provedor(segredos).autenticar(token(exp=int(time.time()) - 3600))

    def test_emissor_errado(self, segredos) -> None:
        with pytest.raises(IdentidadeRecusada, match="emissor"):
            provedor(segredos).autenticar(token(iss="https://outro.test/auth/v1"))

    def test_audiencia_errada(self, segredos) -> None:
        with pytest.raises(IdentidadeRecusada, match="audiência"):
            provedor(segredos).autenticar(token(aud="anon"))

    def test_assinatura_errada(self, segredos) -> None:
        with pytest.raises(IdentidadeRecusada, match="inválido"):
            provedor(segredos).autenticar(
                token(segredo="outro-segredo-com-tamanho-suficiente-0123")
            )

    def test_alg_none_recusado(self, segredos) -> None:
        sem_assinatura = jwt.encode({"sub": "x", "exp": time.time() + 60}, None, algorithm="none")
        with pytest.raises(IdentidadeRecusada, match="algoritmo"):
            provedor(segredos).autenticar(sem_assinatura)

    def test_papel_ausente(self, segredos) -> None:
        with pytest.raises(IdentidadeRecusada, match="papel ausente"):
            provedor(segredos).autenticar(token(papel=None))

    def test_tenant_de_outro_estabelecimento(self, segredos) -> None:
        p = provedor(segredos, claim_tenant="app_metadata.tenant_id", tenant_id="t2")
        with pytest.raises(IdentidadeRecusada, match="outro estabelecimento"):
            p.autenticar(token())

    def test_sem_segredo_configurado_falha_fechado(self, tmp_path) -> None:
        p = provedor(SecretStore(tmp_path / "vazio").for_client("cli"))
        with pytest.raises(IdentidadeRecusada, match="segredo"):
            p.autenticar(token())

    def test_token_que_nao_e_jwt_nao_e_deste_provedor(self, segredos) -> None:
        assert provedor(segredos).autenticar("token-local-qualquer") is None

    def test_rs256_por_jwks(self, segredos) -> None:
        chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        class FakeJWKS:
            def get_signing_key_from_jwt(self, _token):
                return type("K", (), {"key": chave.public_key()})()

        p = AgendaIdentityProvider(
            IdentidadeAgenda(
                emissor=EMISSOR,
                algoritmos=("RS256",),
                segredo=None,
                jwks_url="https://agenda.exemplo.test/.well-known/jwks.json",
            ),
            segredos,
            jwks_client=FakeJWKS(),
        )
        assert p.autenticar(token(segredo=chave, alg="RS256")).perfil.role is Role.ADMIN
        with pytest.raises(IdentidadeRecusada, match="algoritmo"):
            p.autenticar(token())

    def test_jwks_fora_da_allowlist(self, segredos) -> None:
        cfg = IdentidadeAgenda(
            algoritmos=("RS256",), segredo=None, jwks_url="https://evil.test/jwks.json"
        )
        with pytest.raises(ValueError, match="allowlist"):
            AgendaIdentityProvider(cfg, segredos, domain_allowlist=("agenda.exemplo.test",))


class TestConfig:
    def test_rs256_exige_jwks_https(self) -> None:
        with pytest.raises(ValueError):
            IdentidadeAgenda(algoritmos=("RS256",), jwks_url="http://x.test/jwks")

    def test_tenant_anda_junto(self) -> None:
        with pytest.raises(ValueError):
            IdentidadeAgenda(claim_tenant="app_metadata.tenant_id")

    def test_agenda_exige_secao(self) -> None:
        with pytest.raises(ValueError):
            IdentidadeConfig(provedores=("agenda",))

    def test_algoritmo_desconhecido(self) -> None:
        with pytest.raises(ValueError):
            IdentidadeAgenda(algoritmos=("none",))


class TestResolvedor:
    def test_local_continua_funcionando(self, tmp_path, segredos) -> None:
        store = SQLUserStore(tmp_path, "cli")
        store.create_user("sec@clinica.test", Role.VIEWER, api_token="tok-local")
        recusas: list[tuple[str, str]] = []
        r = ResolvedorIdentidade(
            [LocalIdentityProvider(store), provedor(segredos)],
            lambda p, m: recusas.append((p, m)),
        )
        assert r.resolver("Bearer tok-local").perfil.identity == "sec@clinica.test"
        assert r.resolver("Bearer " + token()).provedor == "agenda"
        assert r.resolver("Bearer " + token(exp=int(time.time()) - 3600)) is None
        assert recusas == [("agenda", "token expirado")]
        assert r.resolver("Basic abc") is None and r.resolver(None) is None


def _engine(tmp_path: Path, segredos_dir: Path) -> BRMEngine:
    cfg = tmp_path / "cfg" / "cli" / "config"
    cfg.mkdir(parents=True)
    (cfg / "rules.yaml").write_text("schema_version: 1\nclient_id: cli\nrules: []\n")
    (cfg / "skills.yaml").write_text("schema_version: 1\nclient_id: cli\nskills: []\n")
    (cfg / "client_config.yaml").write_text(
        "client_id: cli\ndomain_allowlist: [api.example.com]\n"
        "identidade:\n  provedores: [local, agenda]\n"
        f"  agenda:\n    emissor: {EMISSOR}\n"
    )
    return BRMEngine("cli", tmp_path / "cfg", tmp_path / "data", secrets=SecretStore(segredos_dir))


class TestHTTP:
    def test_jwt_entra_na_central_e_recusa_e_auditada(self, tmp_path, segredos) -> None:
        engine = _engine(tmp_path, tmp_path / "segredos")
        client = TestClient(create_app(engine=engine))
        ok = client.get("/api/central/eu", headers={"Authorization": f"Bearer {token()}"})
        assert ok.status_code == 200
        assert ok.json()["identidade"] == "joana@clinica.test"
        assert ok.json()["provedor"] == "agenda" and ok.json()["pin_definido"] is False

        expirado = token(exp=int(time.time()) - 3600)
        r = client.get("/api/central/eu", headers={"Authorization": f"Bearer {expirado}"})
        assert r.status_code == 401
        eventos = [e for e in engine._audit_sink.iter_events("cli")]
        recusa = [e for e in eventos if e.action == "identidade:agenda:recusada"]
        assert recusa and recusa[-1].details["motivo"] == "token expirado"
        assert expirado not in str([e.model_dump() for e in eventos])

    def test_jwt_nao_acessa_painel_super_admin_sem_papel(self, tmp_path, segredos) -> None:
        engine = _engine(tmp_path, tmp_path / "segredos")
        client = TestClient(create_app(engine=engine))
        r = client.get("/api/me", headers={"Authorization": f"Bearer {token(papel='USER')}"})
        assert r.status_code == 403

    def test_autenticado_em_por_auth_time(self, segredos) -> None:
        antigo = int(time.time()) - 7200
        ident = provedor(segredos).autenticar(token(auth_time=antigo, aal="aal2"))
        assert ident.autenticado_em == datetime.fromtimestamp(antigo, UTC)
        assert not ident.autenticacao_recente(10)
