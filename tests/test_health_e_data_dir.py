import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mcp_engine.core.health import checar_banco, tudo_ok, verificar_integridade
from mcp_engine.core.http_app import create_app
from mcp_engine.core.mcp_server import BRMEngine
from mcp_engine.core.user_store import SQLUserStore

RAIZ = Path(__file__).resolve().parent.parent


def _criar_banco(caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(caminho)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    for i in range(500):
        conn.execute("INSERT INTO t (v) VALUES (?)", (f"valor-{i}" * 20,))
    conn.commit()
    conn.close()


class TestIntegridade:
    def test_banco_saudavel(self, tmp_path: Path) -> None:
        _criar_banco(tmp_path / "a.db")
        assert checar_banco(tmp_path / "a.db") == "ok"

    def test_arquivo_corrompido_e_detectado(self, tmp_path: Path) -> None:
        banco = tmp_path / "b.sqlite3"
        _criar_banco(banco)
        dados = bytearray(banco.read_bytes())
        for i in range(4096, 8192):
            dados[i] = 0xFF
        banco.write_bytes(bytes(dados))
        resultado = verificar_integridade([tmp_path])
        assert not tudo_ok(resultado)

    def test_ignora_outros_arquivos_e_pastas_ausentes(self, tmp_path: Path) -> None:
        (tmp_path / "audit.jsonl").write_text("{}\n")
        assert verificar_integridade([tmp_path, tmp_path / "nao-existe"]) == {}

    def test_bancos_homonimos_sao_todos_verificados(self, tmp_path: Path) -> None:
        ruim = tmp_path / "dados" / "users.db"
        saudavel = tmp_path / "config" / "users.db"
        _criar_banco(ruim)
        _criar_banco(saudavel)
        dados = bytearray(ruim.read_bytes())
        for i in range(4096, min(len(dados), 8192)):
            dados[i] = 0xFF
        ruim.write_bytes(bytes(dados))

        resultado = verificar_integridade([ruim.parent, saudavel.parent])

        assert list(resultado) == ["users.db", "users.db (2)"]
        assert not tudo_ok(resultado)


class TestBrmDataDir:
    def test_sem_variavel_mantem_users_db_na_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BRM_DATA_DIR", raising=False)
        store = SQLUserStore(tmp_path, "c1")
        assert store.db_path == tmp_path / "c1" / "config" / "users.db"
        assert store.db_path.exists()

    def test_com_variavel_users_db_sai_da_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dados = tmp_path / "dados"
        monkeypatch.setenv("BRM_DATA_DIR", str(dados))
        store = SQLUserStore(tmp_path / "cfg", "c1")
        assert store.db_path == dados / "c1" / "users.db"
        assert store.db_path.exists()
        assert not (tmp_path / "cfg").exists()

    def test_copia_users_db_legado_uma_vez(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BRM_DATA_DIR", raising=False)
        from mcp_engine.core.auth import Role

        legado = SQLUserStore(tmp_path / "cfg", "c1")
        _, token = legado.create_user("a@x.com", Role.ADMIN)
        monkeypatch.setenv("BRM_DATA_DIR", str(tmp_path / "dados"))
        novo = SQLUserStore(tmp_path / "cfg", "c1")
        assert novo.get_by_token(token) is not None
        novo.create_user("b@x.com", Role.VIEWER)
        assert legado.get_by_email("b@x.com") is None


class TestHealthEndpoint:
    @pytest.fixture
    def client(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        monkeypatch.setenv("BRM_DATA_DIR", str(tmp_path / "dados"))
        cfg = tmp_path / "cfg" / "example" / "config"
        cfg.mkdir(parents=True)
        for nome in ("client_config.yaml", "rules.yaml", "skills.yaml"):
            shutil.copy(RAIZ / "clients" / "example" / "config" / nome, cfg / nome)
        engine = BRMEngine("example", tmp_path / "cfg", tmp_path / "data")
        return TestClient(create_app(engine))

    def test_saudavel_retorna_200(self, client: TestClient) -> None:
        resposta = client.get("/health")
        assert resposta.status_code == 200
        assert resposta.json() == {"status": "ok"}

    def test_banco_corrompido_retorna_503(self, client: TestClient, tmp_path: Path) -> None:
        banco = tmp_path / "dados" / "example" / "users.db"
        dados = bytearray(banco.read_bytes())
        for i in range(4096, min(len(dados), 20000)):
            dados[i] = 0xFF
        banco.write_bytes(bytes(dados))
        resposta = client.get("/health")
        assert resposta.status_code == 503
        assert resposta.json()["status"] == "degraded"
