"""Emissor de documentos determinístico (spec 035, fase 3)."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

from mcp_engine.core.documentos import (
    CampoObrigatorioAusente,
    DocumentoErro,
    DocumentoStore,
    IntegridadeViolada,
    ModeloInvalido,
    ModeloNaoAprovado,
    ServicoDocumentos,
    ValorInvalido,
    campos_faltantes,
    carregar_modelos,
    cpf_valido,
    parse_modelo,
    renderizar,
    resolver_valores,
)
from mcp_engine.core.state import StateStore

MODELO = """---
documento: termo-simples
versao: 1
titulo: Termo simples
campos:
  - {nome: nome, tipo: texto, origem: pessoa.nome, obrigatorio: true}
  - {nome: cpf, tipo: cpf, origem: pessoa.cpf, obrigatorio: true}
  - {nome: valor, tipo: dinheiro, origem: atendimento.preco, obrigatorio: true}
  - {nome: dia, tipo: data, origem: atendimento.data}
  - {nome: tutor, tipo: texto, origem: tutor.nome, obrigatorio_quando: pessoa.menor}
  - {nome: empresa, tipo: texto, origem: emissor.razao_social, obrigatorio: true}
---
# Termo

**{{ nome }}**, CPF {{ cpf }}, paga {{ valor }} a {{ empresa }}{% if dia %} em {{ dia }}{% endif %}.
{% if tutor %}Responsável: {{ tutor }}.{% endif %}

- item com & e <tag>
"""

FONTES = {
    "pessoa": {"nome": "Ana Lima", "cpf": "52998224725", "menor": False},
    "atendimento": {"preco": 15000, "data": "2026-10-09"},
}


@pytest.fixture
def modelo():
    return parse_modelo(MODELO, "_modelos/termo.md")


@pytest.fixture
def servico(tmp_path: Path):
    vault = tmp_path / "vault" / "_modelos"
    vault.mkdir(parents=True)
    (vault / "termo.md").write_text(MODELO)
    eventos: list[tuple[str, bool, dict]] = []
    store = DocumentoStore(StateStore(tmp_path / "data", "cli"), tmp_path / "data", "cli")
    svc = ServicoDocumentos(
        store,
        tmp_path / "vault",
        {"razao_social": "Empresa X"},
        lambda a, ok, d: eventos.append((a, ok, d)),
    )
    svc.eventos = eventos
    return svc


class TestModelo:
    def test_parse(self, modelo) -> None:
        assert modelo.id == "termo-simples" and modelo.versao == 1
        assert [c.nome for c in modelo.campos][:2] == ["nome", "cpf"]
        assert len(modelo.hash) == 64

    @pytest.mark.parametrize(
        ("troca", "erro"),
        [
            ("documento: termo-simples", "documento: Termo Simples!"),
            ("versao: 1", "versao: zero"),
            ("{{ nome }}", "{{ nome }} {{ nao_declarado }}"),
            ("tipo: cpf", "tipo: rg"),
            ("origem: pessoa.nome", "origem: ../etc/passwd"),
            ("{% if dia %}", "{% if dia %"),
        ],
    )
    def test_modelo_invalido(self, troca, erro) -> None:
        with pytest.raises(ModeloInvalido):
            parse_modelo(MODELO.replace(troca, erro, 1))

    def test_carregar_ignora_notas_sem_documento_e_reporta_erros(self, tmp_path) -> None:
        pasta = tmp_path / "_modelos"
        pasta.mkdir()
        (pasta / "README.md").write_text("# Modelos\n")
        (pasta / "nota.md").write_text("---\ntipo: modelo\n---\ntexto\n")
        (pasta / "ok.md").write_text(MODELO)
        (pasta / "ruim.md").write_text(MODELO.replace("versao: 1", "versao: 0"))
        modelos, erros = carregar_modelos(tmp_path)
        assert list(modelos) == ["termo-simples"]
        assert list(erros) == ["_modelos/ruim.md"]


class TestPreenchimento:
    def test_valores_formatados(self, modelo) -> None:
        valores = resolver_valores(modelo, {**FONTES, "emissor": {"razao_social": "X"}})
        assert valores["cpf"] == "529.982.247-25"
        assert valores["valor"] == "R$ 150,00"
        assert valores["dia"] == "09/10/2026"
        assert valores["tutor"] == ""

    def test_obrigatorio_ausente_e_erro_nunca_texto_inventado(self, modelo) -> None:
        with pytest.raises(CampoObrigatorioAusente) as exc:
            resolver_valores(modelo, {"pessoa": {"nome": "Ana"}})
        assert set(exc.value.campos) == {"cpf", "valor", "empresa"}

    def test_obrigatorio_quando(self, modelo) -> None:
        fontes = {**FONTES, "pessoa": {**FONTES["pessoa"], "menor": True}}
        assert [c.nome for c in campos_faltantes(modelo, fontes)] == ["tutor", "empresa"]

    def test_valor_invalido(self, modelo) -> None:
        fontes = {**FONTES, "pessoa": {"nome": "Ana", "cpf": "11111111111"}, "emissor": {}}
        fontes["emissor"] = {"razao_social": "X"}
        with pytest.raises(ValorInvalido):
            resolver_valores(modelo, fontes)

    def test_sandbox_bloqueia_introspeccao(self) -> None:
        malicioso = parse_modelo(
            MODELO.replace("# Termo", "# {{ nome.__class__.__mro__ }}"), "_modelos/x.md"
        )
        with pytest.raises(ModeloInvalido):
            renderizar(malicioso, {c.nome: "x" for c in malicioso.campos})

    def test_cpf(self) -> None:
        assert cpf_valido("529.982.247-25") and not cpf_valido("529.982.247-24")
        assert not cpf_valido("000.000.000-00")


class TestEmissao:
    def test_modelo_sem_aprovacao_nao_emite(self, servico) -> None:
        with pytest.raises(ModeloNaoAprovado):
            servico.emitir("termo-simples", FONTES, emitido_por="a@x", emitido_papel="admin")
        assert ("documento:recusado", False) in [(a, ok) for a, ok, _ in servico.eventos]

    def test_emite_pdf_com_hash_e_dados(self, servico, tmp_path) -> None:
        modelo = servico.modelo("termo-simples")
        assert servico.store.aprovar_modelo(modelo, "gestor@x")
        doc = servico.emitir(
            "termo-simples",
            FONTES,
            emitido_por="sec@x",
            emitido_papel="viewer",
            referencia="ref:1",
            acao_id="a" * 32,
        )
        pdf = servico.store.ler_pdf(doc.id)
        assert pdf.startswith(b"%PDF") and hashlib.sha256(pdf).hexdigest() == doc.sha256
        assert b"FontFile2" in pdf and b"Vera" in pdf
        assert doc.modelo_versao == 1 and doc.modelo_hash == modelo.hash
        assert servico.store.dados(doc.id)["empresa"] == "Empresa X"
        caminho = tmp_path / "data" / "cli" / "documentos" / f"{doc.id}.pdf"
        assert not os.access(caminho, os.W_OK)
        acoes = [a for a, _, _ in servico.eventos]
        assert acoes.index("documento:emitindo") < acoes.index("documento:emitido")
        assert servico.emitidos("ref:1")[0]["documento_id"] == doc.id

    def test_documento_emitido_e_imutavel(self, servico, tmp_path) -> None:
        servico.store.aprovar_modelo(servico.modelo("termo-simples"), "g")
        doc = servico.emitir("termo-simples", FONTES, emitido_por="s", emitido_papel="viewer")
        con = sqlite3.connect(tmp_path / "data" / "cli" / "state.db")
        try:
            with pytest.raises(sqlite3.IntegrityError, match="imutável"):
                con.execute("UPDATE documento_emitido SET dados = '{}' WHERE id = ?", (doc.id,))
            with pytest.raises(sqlite3.IntegrityError, match="imutável"):
                con.execute("DELETE FROM documento_emitido WHERE id = ?", (doc.id,))
        finally:
            con.close()

    def test_pdf_adulterado_e_detectado(self, servico, tmp_path) -> None:
        servico.store.aprovar_modelo(servico.modelo("termo-simples"), "g")
        doc = servico.emitir("termo-simples", FONTES, emitido_por="s", emitido_papel="viewer")
        caminho = tmp_path / "data" / "cli" / "documentos" / f"{doc.id}.pdf"
        os.chmod(caminho, 0o644)
        caminho.write_bytes(caminho.read_bytes() + b"%")
        with pytest.raises(IntegridadeViolada):
            servico.store.ler_pdf(doc.id)

    def test_correcao_e_nova_emissao_apontando_a_anterior(self, servico) -> None:
        servico.store.aprovar_modelo(servico.modelo("termo-simples"), "g")
        kw = {"emitido_por": "s", "emitido_papel": "viewer", "referencia": "ref:1"}
        original = servico.emitir("termo-simples", FONTES, **kw)
        corrigido = servico.emitir("termo-simples", FONTES, substitui=original.id, **kw)
        assert corrigido.substitui == original.id
        assert servico.store.obter(original.id).sha256 == original.sha256
        with pytest.raises(DocumentoErro):
            servico.emitir(
                "termo-simples",
                FONTES,
                emitido_por="s",
                emitido_papel="v",
                referencia="ref:2",
                substitui=original.id,
            )

    def test_modelo_alterado_depois_da_aprovacao(self, servico, tmp_path) -> None:
        servico.store.aprovar_modelo(servico.modelo("termo-simples"), "g")
        arquivo = tmp_path / "vault" / "_modelos" / "termo.md"
        arquivo.write_text(MODELO.replace("# Termo", "# Termo alterado"))
        with pytest.raises(ModeloNaoAprovado):
            servico.emitir("termo-simples", FONTES, emitido_por="s", emitido_papel="viewer")
        assert not servico.store.aprovar_modelo(servico.modelo("termo-simples"), "g")

    def test_previa_marca_faltantes_e_invalidos(self, servico) -> None:
        linhas = servico.previa("termo-simples", {"pessoa": {"nome": "Ana", "cpf": "123"}})
        por_campo = {linha["campo"]: linha for linha in linhas}
        assert por_campo["nome"]["valor"] == "Ana"
        assert por_campo["cpf"]["invalido"] == "CPF inválido"
        assert por_campo["valor"]["faltante"] is True
        assert por_campo["empresa"]["valor"] == "Empresa X"
