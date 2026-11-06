"""Comissão e faturamento só para o administrador: regras duras, travas e leitura do vault."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from mcp_engine.core.acoes import AcaoErro
from mcp_engine.core.auth import Role, UserProfile, current_user_var, set_current_user
from mcp_engine.core.identidade import Identidade
from mcp_engine.core.mcp_server import BRMEngine
from mcp_engine.core.models import RuleEffect, RuleSet, VaultAccess, VaultNote
from mcp_engine.core.rule_evaluator import evaluate
from mcp_engine.modules.financeiro.consultas import CONSULTAS
from mcp_engine.vault.access import filter_notes

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "clients" / "clinica-piloto" / "config"
RECEPCAO = ["a_receber", "saldo_pacote", "faltas_por_periodo", "pendencias", "sem_nf"]
RECEPCAO += ["ultimos_lancamentos"]
SEGREDOS = sorted(set(CONSULTAS) - set(RECEPCAO))
ADMINS = ("admin", "super_admin")


@pytest.fixture(scope="module")
def regras() -> RuleSet:
    return RuleSet.model_validate(yaml.safe_load((CONFIG / "rules.yaml").read_text()))


def decidir(regras: RuleSet, papel: str, skill: str, **args):
    return evaluate(regras, {"skill_name": skill, "actor_role": papel, **args})


class TestRegrasDuras:
    @pytest.mark.parametrize("consulta", SEGREDOS)
    def test_nao_admin_nunca_consulta_comissao_ou_faturamento(self, regras, consulta) -> None:
        d = decidir(regras, "viewer", "consultar_financeiro", consulta=consulta)
        assert d.effect is RuleEffect.DENY and d.rule_id in {
            "comissao-so-admin",
            "faturamento-so-admin",
        }

    @pytest.mark.parametrize("consulta", ["repasse_por_profissional", "simular_repasse"])
    def test_comissao_tem_regra_propria_com_mensagem_clara(self, regras, consulta) -> None:
        d = decidir(regras, "viewer", "consultar_financeiro", consulta=consulta)
        assert d.rule_id == "comissao-so-admin" and "administrador" in d.message

    @pytest.mark.parametrize("consulta", list(CONSULTAS))
    @pytest.mark.parametrize("papel", ADMINS)
    def test_admin_consulta_tudo(self, regras, papel, consulta) -> None:
        d = decidir(regras, papel, "consultar_financeiro", consulta=consulta)
        assert d.effect is RuleEffect.ALLOW

    @pytest.mark.parametrize("consulta", RECEPCAO)
    def test_recepcao_mantem_o_operacional(self, regras, consulta) -> None:
        assert (
            decidir(regras, "viewer", "consultar_financeiro", consulta=consulta).effect
            is RuleEffect.ALLOW
        )

    def test_lista_de_permitidos_so_tem_consultas_que_existem(self) -> None:
        assert set(RECEPCAO) <= set(CONSULTAS)

    @pytest.mark.parametrize(
        "consulta",
        [
            "RESUMO_MENSAL",
            " resumo_mensal",
            "resumo_mensal ",
            "Repasse_Por_Profissional",
            "",
            None,
            ["resumo_mensal"],
            {"x": 1},
            123,
            "consulta_que_ainda_nao_existe",
        ],
    )
    def test_variacoes_e_entradas_estranhas_nao_furam(self, regras, consulta) -> None:
        d = decidir(regras, "viewer", "consultar_financeiro", consulta=consulta)
        assert d.effect is RuleEffect.DENY

    def test_papel_desconhecido_ou_ausente_e_negado(self, regras) -> None:
        for papel in ("", "readonly", "profissional", None):
            d = decidir(regras, papel, "consultar_financeiro", consulta="resumo_mensal")
            assert d.effect is RuleEffect.DENY

    @pytest.mark.parametrize("tipo", ["financeiro", "secretarias", "Financeiro", "", None, "outra"])
    def test_nenhuma_planilha_para_quem_nao_e_admin(self, regras, tipo) -> None:
        d = decidir(regras, "viewer", "exportar_planilha", tipo=tipo)
        assert d.effect is RuleEffect.DENY and d.rule_id == "planilha-so-admin"
        for papel in ADMINS:
            assert decidir(regras, papel, "exportar_planilha", tipo=tipo).effect is RuleEffect.ALLOW

    def test_bonus_de_indicacao_so_admin(self, regras) -> None:
        d = decidir(regras, "viewer", "agenda_bonus_indicacao")
        assert d.effect is RuleEffect.DENY and d.rule_id == "bonus-indicacao-so-admin"
        for papel in ADMINS:
            assert decidir(regras, papel, "agenda_bonus_indicacao").effect is RuleEffect.ALLOW

    def test_regras_de_confidencialidade_vem_antes_das_outras(self, regras) -> None:
        assert {r.id for r in regras.rules[:4]} == {
            "comissao-so-admin",
            "bonus-indicacao-so-admin",
            "faturamento-so-admin",
            "planilha-so-admin",
        }


class TestLeituraDoVault:
    @pytest.fixture
    def acesso(self) -> VaultAccess:
        return VaultAccess.model_validate(
            yaml.safe_load((CONFIG / "vault_access.yaml").read_text())
        )

    @staticmethod
    def nota(note_id: str, **frontmatter) -> VaultNote:
        return VaultNote(
            note_id=note_id,
            client_id="clinica-piloto",
            title=note_id,
            content="Repasses: R$ 10.000,00",
            frontmatter=frontmatter,
            links=(),
            tags=(),
            updated_at=datetime.now(UTC),
            content_hash="h",
        )

    @pytest.mark.parametrize(
        "fm",
        [
            {"tipo": "relatorio"},
            {"tipo": "comissao"},
            {"tipo": "taxa"},
            {"tipo": "repasse"},
            {"tipo": "politica", "confidencialidade": "financeiro"},
            {"tipo": "memoria", "confidencialidade": "financeiro"},
        ],
    )
    def test_nota_financeira_so_aparece_para_admin(self, acesso, fm) -> None:
        notas = [self.nota("n", **fm)]
        assert filter_notes(acesso, notas, "sec@x", "viewer").visible == []
        for papel in ADMINS:
            r = filter_notes(acesso, notas, "gestor@x", papel)
            assert [n.note_id for n in r.visible] == ["n"]
            assert r.privileged == ["n"]

    def test_modelos_continuam_abertos(self, acesso) -> None:
        r = filter_notes(acesso, [self.nota("m", tipo="modelo")], "sec@x", "viewer")
        assert [n.note_id for n in r.visible] == ["m"]

    def test_politica_de_confidencialidade_esta_no_vault_e_e_restrita(self, acesso) -> None:
        texto = (
            RAIZ / "clients/clinica-piloto/vault/politicas/confidencialidade-financeira.md"
        ).read_text()
        fm = yaml.safe_load(texto.split("---")[1])
        notas = [self.nota("p", **fm)]
        assert filter_notes(acesso, notas, "sec@x", "viewer").visible == []


class TestMotorDePonta:
    @pytest.fixture
    def engine(self, tmp_path: Path) -> BRMEngine:
        origem = RAIZ / "clients" / "clinica-piloto"
        destino = tmp_path / "clients" / "clinica-piloto"
        shutil.copytree(origem, destino, ignore=shutil.ignore_patterns("*.db"))
        cfg = destino / "config" / "client_config.yaml"
        cfg.write_text(
            cfg.read_text().replace(
                "vault_root: clients/clinica-piloto/vault", f"vault_root: {destino / 'vault'}"
            )
        )
        token = current_user_var.set(None)
        yield BRMEngine("clinica-piloto", tmp_path / "clients", tmp_path / "data")
        current_user_var.reset(token)

    def test_chat_de_recepcao_recebe_negativa_e_o_gestor_consulta(self, engine) -> None:
        set_current_user(UserProfile(identity="sec@x", role=Role.VIEWER))
        for consulta in ("resumo_mensal", "repasse_por_profissional", "painel_financeiro"):
            r = engine.call_skill("consultar_financeiro", {"consulta": consulta})
            assert r["allowed"] is False and r["rule_id"].endswith("so-admin"), r
        set_current_user(UserProfile(identity="gestor@x", role=Role.ADMIN))
        r = engine.call_skill("consultar_financeiro", {"consulta": "resumo_mensal"})
        assert r["allowed"] is True, r

    def test_planilha_foi_retirada_da_clinica_para_todos(self, engine) -> None:
        for perfil in (
            UserProfile(identity="sec@x", role=Role.VIEWER),
            UserProfile(identity="gestor@x", role=Role.ADMIN),
        ):
            set_current_user(perfil)
            r = engine.call_skill("exportar_planilha", {"tipo": "financeiro", "periodo": "2026-02"})
            assert r["allowed"] is False and "skill not found" in r["reason"], r

    def test_acoes_de_bonus_e_aptidao_exigem_administrador(self, engine) -> None:
        for nome in (
            "agenda.definir_corte_bonus_indicacao",
            "agenda.definir_aptidao_bonus_indicacao",
            "agenda.confirmar_pagamento",
            "agenda.definir_crp_profissional",
        ):
            assert engine.acoes._tipo(nome).papel_minimo == "admin", nome

    def test_consulta_restrita_so_abre_para_admin_e_o_chat_nao_ve_numeros(self, engine) -> None:
        sec = Identidade(
            id="sec@x",
            perfil=UserProfile(identity="sec@x", role=Role.VIEWER),
            provedor="local",
            metodo="token",
        )
        gestor = Identidade(
            id="gestor@x",
            perfil=UserProfile(identity="gestor@x", role=Role.ADMIN),
            provedor="local",
            metodo="token",
        )
        engine.acoes.atributos.definir_pin("local", gestor.id, "7391", por=gestor.id)
        set_current_user(sec.perfil)
        r = engine.criar_acao(
            "financeiro.consulta_restrita",
            {"consulta": "repasse_por_profissional", "periodo": "2026-11"},
        )
        assert r.get("acao_id"), r
        with pytest.raises(AcaoErro) as exc:
            engine.acoes.abrir(sec, r["acao_id"])
        assert exc.value.status == 403
        pagina = engine.acoes.abrir(gestor, r["acao_id"])
        item = pagina["itens"][0]
        assert item["dados"]["consulta"] == "repasse_por_profissional"
        assert "dados" in item["dados"]
        saida = engine.acoes.confirmar(gestor, r["acao_id"], "7391")
        assert saida["status"] == "confirmada"
        chat = engine.status_da_acao(r["acao_id"])
        assert "dados" not in str(chat["resultado"]) and "repasse" not in str(chat["resultado"])
        assert "gestor@x" in chat["resultado"][0]["resumo"]

    def test_consulta_restrita_de_consulta_inexistente_falha_sem_vazar(self, engine) -> None:
        gestor = Identidade(
            id="gestor@x",
            perfil=UserProfile(identity="gestor@x", role=Role.ADMIN),
            provedor="local",
            metodo="token",
        )
        set_current_user(gestor.perfil)
        r = engine.criar_acao("financeiro.consulta_restrita", {"consulta": "nao_existe"})
        with pytest.raises(AcaoErro):
            engine.acoes.abrir(gestor, r["acao_id"])
