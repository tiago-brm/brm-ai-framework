"""Apoio dos testes de gatilhos: motor em modo notas, relógio controlado e contadores."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from audit.sink import FileSystemAuditSink
from mcp_engine.core.auth import Role, UserProfile, set_current_user
from mcp_engine.core.executor import ExecucaoFalhou, SecretStore
from mcp_engine.core.mcp_server import BRMEngine
from mcp_engine.vault.obsidian_adapter import ObsidianVaultProvider

ANA = UserProfile(identity="ana@c.com", role=Role.ADMIN)
BIA = UserProfile(identity="bia@c.com", role=Role.ADMIN)
LUCAS = UserProfile(identity="lucas@c.com", role=Role.VIEWER)
ROOT = UserProfile(identity="root@c.com", role=Role.SUPER_ADMIN)

CONFIG = """\
client_id: example
rbac_default: allow
block_private_ip: true
config_via_notas: true
domain_allowlist:
  - api.example.com
dlp_patterns:
  - pattern: '\\d{3}\\.\\d{3}\\.\\d{3}-\\d{2}'
    label: CPF
    action: deny
"""
SEG_21H = datetime(2026, 3, 2, 21, 0, 30, tzinfo=UTC)
ANTES_21H = datetime(2026, 3, 2, 20, 55, tzinfo=UTC)


class FakeEmbeddings:
    dim = 384

    def encode(self, texts):
        return [[0.0] * 384 for _ in texts]


class Relogio:
    def __init__(self, agora: datetime = ANTES_21H) -> None:
        self.agora = agora

    def __call__(self) -> datetime:
        return self.agora


def skill_nota(nome: str, autonomia: str, handler: str, extra: str = "") -> str:
    return (
        f"---\ntipo: skill\nid: {nome}\n---\nSkill de teste.\n\n```brm-config\n"
        f"name: {nome}\ndescription: skill {nome}\n"
        f"executor: {{ type: native, handler: {handler} }}\n"
        f"autonomia: {autonomia}\n{extra}```\n"
    )


def gatilho_bloco(**mudancas: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "g1",
        "descricao": "Gatilho de teste",
        "quando": {"agenda": "0 18 * * 1-5"},
        "acao": {"skill": "leitura", "argumentos": {}},
        "saida": {"tipo": "alerta", "mensagem": "n={{ resultado.n }}"},
        "autonomia": "A0",
        "permissoes": ["leitura"],
        "destinatario": "gestor",
    }
    base.update(mudancas)
    return base


def gatilho_nota(bloco: dict[str, Any], prosa: str = "Gatilho de teste.") -> str:
    texto = yaml.safe_dump(bloco, sort_keys=True, allow_unicode=True)
    return f"---\ntipo: gatilho\nid: {bloco['id']}\n---\n{prosa}\n\n```brm-config\n{texto}```\n"


class Cenario:
    """Motor de teste: handlers contáveis, skills ativadas por notas e relógio manual."""

    def __init__(self, raiz: Path) -> None:
        self.raiz = raiz
        self.ctr: Counter[str] = Counter()
        self.enviados: list[dict[str, Any]] = []
        self.relogio = Relogio()
        self.engine = self.subir(semear=True)

    def subir(self, semear: bool = False) -> BRMEngine:
        cfg = self.raiz / "config" / "example" / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "client_config.yaml").write_text(CONFIG)
        (cfg / "rules.yaml").write_text("schema_version: 1\nclient_id: example\nrules: []\n")
        (cfg / "skills.yaml").write_text("schema_version: 1\nclient_id: example\nskills: []\n")
        (self.raiz / "config" / "example" / "vault").mkdir(exist_ok=True)
        data = self.raiz / "data"
        provider = ObsidianVaultProvider(
            self.raiz / "config", data, FileSystemAuditSink(data), FakeEmbeddings()
        )
        eng = BRMEngine(
            "example",
            self.raiz / "config",
            data,
            vault_provider=provider,
            secrets=SecretStore(self.raiz),
        )
        self._registrar(eng)
        eng.gatilhos.relogio = self.relogio
        if semear:
            self.engine = eng
            for nome, autonomia, handler in (
                ("leitura", "A0", "t.leitura"),
                ("efeito_a1", "A1", "t.a1"),
                ("efeito_a2", "A2", "t.a2"),
                ("efeito_a3", "A3", "t.a3"),
                ("emite", "A1", "t.emite"),
                ("quebra", "A0", "t.quebra"),
                ("enviar_email", "A1", "t.email"),
            ):
                self.ativar(f"skills/{nome}.md", skill_nota(nome, autonomia, handler))
        return eng

    def _registrar(self, eng: BRMEngine) -> None:
        ctr = self.ctr

        def contar(nome: str, retorno: dict[str, Any] | None = None):
            def handler(ctx, args):
                ctr[nome] += 1
                ctr[f"{nome}:ctx"] = (ctx.actor, ctx.role, ctx.confirmado_por, ctx.aprovado_por)
                return dict(retorno or {"ok": True})

            return handler

        def quebra(ctx, args):
            ctr["quebra"] += 1
            raise ExecucaoFalhou("falha de teste")

        def emite(ctx, args):
            ctr["emite"] += 1
            return {
                "ok": True,
                "_eventos": [{"tipo": "demo.ocorreu", "payload": {"n": ctr["emite"]}}],
            }

        def email(ctx, args):
            self.enviados.append(dict(args))
            return {"enviado": True}

        eng.handlers.register("t.leitura", contar("leitura", {"texto": "tudo certo", "n": 2}))
        eng.handlers.register("t.a1", contar("efeito_a1"))
        eng.handlers.register("t.a2", contar("efeito_a2"))
        eng.handlers.register("t.a3", contar("efeito_a3"))
        eng.handlers.register("t.emite", emite)
        eng.handlers.register("t.quebra", quebra)
        eng.handlers.register("t.email", email)

    def escrever(self, rel: str, texto: str) -> None:
        destino = self.engine._vault_root() / rel
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto)

    def ativar(self, caminho: str, texto: str) -> dict[str, Any]:
        self.escrever(caminho, texto)
        set_current_user(ANA)
        proposta = self.engine.config_propor(caminho)
        assert proposta["allowed"], proposta
        set_current_user(BIA)
        resultado = self.engine.aprovar_operacao(proposta["operacao_id"])
        assert resultado["allowed"], resultado
        return resultado

    def gatilho(self, **mudancas: Any) -> dict[str, Any]:
        bloco = gatilho_bloco(**mudancas)
        self.ativar(f"gatilhos/{bloco['id']}.md", gatilho_nota(bloco))
        return bloco

    def propor_gatilho(self, **mudancas: Any) -> dict[str, Any]:
        bloco = gatilho_bloco(**mudancas)
        self.escrever(f"gatilhos/{bloco['id']}.md", gatilho_nota(bloco))
        set_current_user(ANA)
        return self.engine.config_propor(f"gatilhos/{bloco['id']}.md")

    def publicar(self, tipo: str, payload: dict[str, Any] | None = None, dedup: str | None = None):
        self.engine.gatilhos.publicar_eventos(
            [{"tipo": tipo, "payload": payload or {}, "dedup": dedup}]
        )
