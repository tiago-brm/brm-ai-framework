import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import exportar_config_para_notas as exportar  # noqa: E402
import gerar_mapa_do_sistema as mapa  # noqa: E402

from mcp_engine.core.auth import Role, UserProfile, current_user_var, set_current_user  # noqa: E402
from mcp_engine.core.executor import SecretStore  # noqa: E402
from mcp_engine.core.mcp_server import BRMEngine  # noqa: E402

RULES = """\
schema_version: 1
client_id: example
rules:
  - id: libera-leitura
    description: leitura liberada
    condition: {"==": [{"var": "skill_name"}, "leitura"]}
    effect: allow
  - id: bloqueia-tudo
    description: bloqueia o resto
    condition: {"==": [1, 1]}
    effect: deny
    message: Bloqueado
"""
SKILLS = """\
schema_version: 1
client_id: example
skills:
  - name: leitura
    description: consulta
    executor: { type: native, handler: t.leitura }
  - name: outra
    description: outra consulta
    executor: { type: native, handler: t.leitura }
"""
CONFIG = "client_id: example\nrbac_default: allow\ndomain_allowlist: [api.example.com]\n"


def preparar(tmp_path: Path) -> Path:
    cfg = tmp_path / "config" / "example" / "config"
    cfg.mkdir(parents=True)
    (cfg / "client_config.yaml").write_text(CONFIG)
    (cfg / "rules.yaml").write_text(RULES)
    (cfg / "skills.yaml").write_text(SKILLS)
    return tmp_path / "config"


def test_exportacao_preserva_ordem_e_nao_sobrescreve(tmp_path: Path) -> None:
    raiz = preparar(tmp_path)
    args = ["example", "--config-root", str(raiz)]
    assert exportar.main([*args, "--dry-run"]) == 0
    assert not (raiz / "example" / "vault").exists()
    assert exportar.main(args) == 0
    base = raiz / "example" / "vault" / "configuracao"
    assert sorted(p.name for p in (base / "regras").glob("*.md")) == [
        "bloqueia-tudo.md", "libera-leitura.md"
    ]  # fmt: skip
    (base / "regras" / "bloqueia-tudo.md").write_text("editada")
    exportar.main(args)
    assert (base / "regras" / "bloqueia-tudo.md").read_text() == "editada"


def test_ida_e_volta_gera_o_mesmo_comportamento_depois_da_aprovacao(tmp_path: Path) -> None:
    raiz = preparar(tmp_path)
    exportar.main(["example", "--config-root", str(raiz)])
    (raiz / "example" / "config" / "client_config.yaml").write_text(
        CONFIG + "config_via_notas: true\nconfig_aprovacoes_por_hora: 50\n"
    )
    token = current_user_var.set(None)
    try:
        eng = BRMEngine("example", raiz, tmp_path / "data", secrets=SecretStore(tmp_path))
        eng.handlers.register("t.leitura", lambda ctx, args: {"ok": True})
        assert eng.rules.rules == () and eng.skills == ()
        ana = UserProfile(identity="ana@c.com", role=Role.ADMIN)
        bia = UserProfile(identity="bia@c.com", role=Role.ADMIN)
        for item in eng.caderno.estado():
            set_current_user(ana)
            op = eng.config_propor(item["caminho"])["operacao_id"]
            set_current_user(bia)
            assert eng.aprovar_operacao(op)["allowed"] is True
        original = yaml.safe_load(RULES)["rules"]
        assert [r.id for r in eng.rules.rules] == [r["id"] for r in original]
        assert {s.name for s in eng.skills} == {"leitura", "outra"}
        set_current_user(UserProfile(identity="x@c.com", role=Role.VIEWER))
        assert eng.call_skill("leitura", {})["allowed"] is True
        assert eng.call_skill("outra", {})["allowed"] is False
    finally:
        current_user_var.reset(token)


def test_script_do_mapa_gera_as_quatro_paginas(tmp_path: Path, capsys) -> None:
    raiz = preparar(tmp_path)
    assert (
        mapa.main(["example", "--config-root", str(raiz), "--data-root", str(tmp_path / "d")]) == 0
    )
    sistema = raiz / "example" / "vault" / "sistema"
    assert {p.name for p in sistema.glob("*.md")} == {
        "estado.md", "matriz.md", "mapa-de-cobertura.md", "alertas.md"
    }  # fmt: skip
    assert "criado" in capsys.readouterr().out
