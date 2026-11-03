"""Emissor de documentos determinístico (spec 035).

Modelos ficam em `<vault>/_modelos/*.md` com front matter que declara os campos (nome, tipo,
origem e obrigatoriedade). O preenchimento usa Jinja2 em sandbox com `StrictUndefined`: o modelo
só enxerga os campos declarados, campo obrigatório ausente é erro e nada é inventado. O PDF sai
do ReportLab com as fontes Vera embutidas e modo invariante. O documento emitido é imutável.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from typing import Any, Literal
from xml.sax.saxutils import escape

import frontmatter as fm
from jinja2 import StrictUndefined, TemplateError, meta, nodes
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from mcp_engine.core.state import DocumentoEmitidoRow, ModeloAprovadoRow, StateStore

PASTA_MODELOS = "_modelos"
TAMANHO_MAXIMO_MODELO = 128 * 1024
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_NOME_CAMPO = r"^[a-z][a-z0-9_]{0,47}$"
_ORIGEM = r"^[a-z][a-z0-9_]{0,47}(\.[a-z][a-z0-9_]{0,47}){0,4}$"

TipoCampo = Literal["texto", "data", "dinheiro", "inteiro", "cpf", "booleano"]


class DocumentoErro(Exception):
    pass


class ModeloInvalido(DocumentoErro):
    pass


class ModeloNaoAprovado(DocumentoErro):
    pass


class ValorInvalido(DocumentoErro):
    def __init__(self, campo: str, motivo: str) -> None:
        super().__init__(f"campo {campo!r}: {motivo}")
        self.campo = campo
        self.motivo = motivo


class CampoObrigatorioAusente(DocumentoErro):
    def __init__(self, campos: list[str]) -> None:
        super().__init__(f"campos obrigatórios ausentes: {', '.join(campos)}")
        self.campos = campos


class IntegridadeViolada(DocumentoErro):
    pass


class CampoModelo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    nome: str = Field(pattern=_NOME_CAMPO)
    tipo: TipoCampo = "texto"
    origem: str = Field(pattern=_ORIGEM)
    obrigatorio: bool = False
    obrigatorio_quando: str | None = Field(default=None, pattern=_ORIGEM)
    rotulo: str | None = None


@dataclass(frozen=True)
class ModeloDocumento:
    id: str
    versao: int
    titulo: str
    campos: tuple[CampoModelo, ...]
    corpo: str
    hash: str
    caminho: str
    exemplo: bool = False
    inferido: bool = False

    def resumo(self) -> dict[str, Any]:
        return {
            "modelo_id": self.id,
            "versao": self.versao,
            "titulo": self.titulo,
            "exemplo": self.exemplo,
            "hash": self.hash,
            "campos": [c.model_dump(exclude_none=True) for c in self.campos],
        }


def _ambiente() -> SandboxedEnvironment:
    return SandboxedEnvironment(
        undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True
    )


_OPCIONAL = "\0opcional"


def _caminho(no: Any) -> str | None:
    if isinstance(no, nodes.Name):
        return no.name
    if isinstance(no, nodes.Getattr):
        base = _caminho(no.node)
        return None if base is None else f"{base}.{no.attr}"
    return None


def _tipo_por_nome(folha: str) -> TipoCampo:
    if folha == "cpf":
        return "cpf"
    if folha in ("nascimento", "data"):
        return "data"
    if folha == "preco" or folha.startswith("valor"):
        return "dinheiro"
    if folha == "menor_de_idade" or folha.startswith(("e_", "tem_")):
        return "booleano"
    return "texto"


def _inferir_campos(corpo: str) -> tuple[CampoModelo, ...]:
    """Campos de um modelo sem `campos:` declarados: cada `{{ fonte.caminho }}` do texto é lido da
    fonte de dados do módulo que emite (`emissor` vem da configuração do cliente); um nome simples
    (`{{ valor_pacote }}`) é uma lacuna livre, informada na emissão. Fora de `{% if %}` o
    campo é obrigatório;
    dentro de um `{% if fonte.booleano %}` só é exigido quando o booleano é verdadeiro."""
    usos: dict[str, str | None] = {}

    def registrar(caminho: str, cond: str | None) -> None:
        atual = usos.get(caminho, _OPCIONAL)
        if cond is None:
            usos[caminho] = None
        elif atual == _OPCIONAL:
            usos[caminho] = cond

    def visitar(no: Any, cond: str | None) -> None:
        if isinstance(no, nodes.If):
            teste = _caminho(no.test)
            if teste:
                registrar(teste, _OPCIONAL)
            else:
                visitar(no.test, cond)
            for filho in no.body:
                visitar(filho, teste or _OPCIONAL)
            for outro in (*no.elif_, *no.else_):
                visitar(outro, _OPCIONAL)
            return
        caminho = _caminho(no)
        if caminho:
            registrar(caminho, cond)
            return
        for filho in no.iter_child_nodes():
            visitar(filho, cond)

    visitar(_ambiente().parse(corpo), None)
    campos = []
    for caminho, cond in usos.items():
        raiz, _, resto = caminho.partition(".")
        if resto:
            origem, tipo = caminho, _tipo_por_nome(caminho.rsplit(".", 1)[-1])
        else:
            origem, tipo = f"livre.{raiz}", "texto"
        obrigatorio_quando = None if cond in (None, _OPCIONAL) else cond
        try:
            campos.append(
                CampoModelo(
                    nome=caminho.replace(".", "_"),
                    tipo=tipo,
                    origem=origem,
                    obrigatorio=cond is None,
                    obrigatorio_quando=obrigatorio_quando,
                )
            )
        except ValidationError as exc:
            raise ModeloInvalido(f"lacuna inválida {caminho!r}: {exc.errors()[0]['msg']}") from exc
    if not campos:
        raise ModeloInvalido("o texto não tem nenhuma lacuna")
    return tuple(campos)


def parse_modelo(texto: str, caminho: str = "") -> ModeloDocumento:
    if len(texto.encode("utf-8")) > TAMANHO_MAXIMO_MODELO:
        raise ModeloInvalido("modelo acima do tamanho máximo")
    try:
        post = fm.loads(texto)
    except Exception as exc:
        raise ModeloInvalido(f"front matter inválido: {exc}") from exc
    meta_ = dict(post.metadata)
    modelo_id = str(meta_.get("documento") or "")
    if not _ID.match(modelo_id):
        raise ModeloInvalido("'documento' (id do modelo) ausente ou inválido")
    versao = meta_.get("versao")
    if isinstance(versao, bool) or not isinstance(versao, int) or versao < 1:
        raise ModeloInvalido("'versao' deve ser inteiro positivo")
    brutos = meta_.get("campos")
    inferido = brutos is None
    try:
        if inferido:
            campos = _inferir_campos(post.content)
        elif isinstance(brutos, list) and brutos:
            campos = tuple(CampoModelo.model_validate(c) for c in brutos)
        else:
            raise ModeloInvalido("'campos' deve declarar ao menos um campo")
    except ValidationError as exc:
        raise ModeloInvalido(f"campo mal declarado: {exc.errors()[0]['msg']}") from exc
    except TemplateError as exc:
        raise ModeloInvalido(f"modelo com sintaxe inválida: {exc}") from exc
    nomes = [c.nome for c in campos]
    if len(set(nomes)) != len(nomes):
        raise ModeloInvalido("campos com nome repetido")
    if not inferido:
        try:
            usados = meta.find_undeclared_variables(_ambiente().parse(post.content))
        except TemplateError as exc:
            raise ModeloInvalido(f"modelo com sintaxe inválida: {exc}") from exc
        desconhecidos = sorted(usados - set(nomes))
        if desconhecidos:
            raise ModeloInvalido(f"o texto usa campos não declarados: {', '.join(desconhecidos)}")
    return ModeloDocumento(
        id=modelo_id,
        versao=versao,
        titulo=str(meta_.get("titulo") or modelo_id),
        campos=campos,
        corpo=post.content,
        hash=hashlib.sha256(texto.encode("utf-8")).hexdigest(),
        caminho=caminho,
        exemplo=bool(meta_.get("exemplo", False)),
        inferido=inferido,
    )


def carregar_modelos(vault_root: Path) -> tuple[dict[str, ModeloDocumento], dict[str, str]]:
    """Modelos de `_modelos/` que declaram `documento:`; devolve também os erros por arquivo."""
    pasta = Path(vault_root) / PASTA_MODELOS
    modelos: dict[str, ModeloDocumento] = {}
    erros: dict[str, str] = {}
    if not pasta.is_dir():
        return modelos, erros
    for arquivo in sorted(pasta.glob("*.md")):
        caminho = f"{PASTA_MODELOS}/{arquivo.name}"
        texto = arquivo.read_text(encoding="utf-8")
        try:
            if "documento" not in fm.loads(texto).metadata:
                continue
            modelo = parse_modelo(texto, caminho)
        except Exception as exc:
            erros[caminho] = str(exc)
            continue
        if modelo.id in modelos:
            erros[caminho] = f"modelo {modelo.id!r} já declarado em {modelos[modelo.id].caminho}"
            continue
        modelos[modelo.id] = modelo
    return modelos, erros


def _dig(fontes: Mapping[str, Any], caminho: str) -> Any:
    atual: Any = fontes
    for parte in caminho.split("."):
        if not isinstance(atual, Mapping) or parte not in atual:
            return None
        atual = atual[parte]
    return atual


def _vazio(valor: Any) -> bool:
    return valor is None or (isinstance(valor, str) and not valor.strip())


def cpf_valido(cpf: str) -> bool:
    digitos = re.sub(r"\D", "", cpf)
    if len(digitos) != 11 or len(set(digitos)) == 1:
        return False
    for tamanho in (9, 10):
        soma = sum(int(d) * peso for d, peso in zip(digitos[:tamanho], range(tamanho + 1, 1, -1)))
        if (soma * 10 % 11) % 10 != int(digitos[tamanho]):
            return False
    return True


def formatar_dinheiro(centavos: int) -> str:
    reais, cent = divmod(centavos, 100)
    return f"R$ {reais:,}".replace(",", ".") + f",{cent:02d}"


def _formatar(campo: CampoModelo, valor: Any) -> str:
    tipo = campo.tipo
    if tipo == "texto":
        if not isinstance(valor, str | int | float) or isinstance(valor, bool):
            raise ValorInvalido(campo.nome, "texto esperado")
        texto = str(valor).strip()
        if campo.origem.startswith("livre.") and (len(texto) > 200 or "\n" in texto):
            raise ValorInvalido(campo.nome, "valor livre: uma linha de até 200 caracteres")
        return texto
    if tipo == "inteiro":
        if isinstance(valor, bool) or not isinstance(valor, int):
            raise ValorInvalido(campo.nome, "inteiro esperado")
        return str(valor)
    if tipo == "booleano":
        if not isinstance(valor, bool):
            raise ValorInvalido(campo.nome, "sim/não esperado")
        return "sim" if valor else "não"
    if tipo == "dinheiro":
        if isinstance(valor, bool) or not isinstance(valor, int | str | Decimal):
            raise ValorInvalido(campo.nome, "valor em centavos esperado")
        try:
            centavos = int(Decimal(str(valor)))
        except InvalidOperation:
            raise ValorInvalido(campo.nome, "valor inválido") from None
        if centavos < 0:
            raise ValorInvalido(campo.nome, "valor negativo")
        return formatar_dinheiro(centavos)
    if tipo == "data":
        if isinstance(valor, datetime):
            valor = valor.date()
        elif isinstance(valor, str):
            try:
                valor = date.fromisoformat(valor)
            except ValueError:
                raise ValorInvalido(campo.nome, "data AAAA-MM-DD esperada") from None
        if not isinstance(valor, date):
            raise ValorInvalido(campo.nome, "data esperada")
        return valor.strftime("%d/%m/%Y")
    if not isinstance(valor, str) or not cpf_valido(valor):
        raise ValorInvalido(campo.nome, "CPF inválido")
    d = re.sub(r"\D", "", valor)
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


def _exigido(campo: CampoModelo, fontes: Mapping[str, Any]) -> bool:
    if campo.obrigatorio:
        return True
    return bool(campo.obrigatorio_quando and _dig(fontes, campo.obrigatorio_quando) is True)


def campos_faltantes(modelo: ModeloDocumento, fontes: Mapping[str, Any]) -> list[CampoModelo]:
    return [c for c in modelo.campos if _exigido(c, fontes) and _vazio(_dig(fontes, c.origem))]


def resolver_valores(modelo: ModeloDocumento, fontes: Mapping[str, Any]) -> dict[str, str]:
    """Valores formatados de cada campo; falha se faltar obrigatório ou vier valor inválido."""
    faltantes = campos_faltantes(modelo, fontes)
    if faltantes:
        raise CampoObrigatorioAusente([c.nome for c in faltantes])
    valores: dict[str, str] = {}
    for campo in modelo.campos:
        bruto = _dig(fontes, campo.origem)
        valores[campo.nome] = "" if _vazio(bruto) else _formatar(campo, bruto)
    return valores


def _contexto_inferido(modelo: ModeloDocumento, valores: Mapping[str, str]) -> dict[str, Any]:
    contexto: dict[str, Any] = {}
    for campo in modelo.campos:
        valor: Any = valores.get(campo.nome, "")
        if campo.tipo == "booleano":
            valor = valor == "sim"
        raiz, _, resto = campo.origem.partition(".")
        if raiz == "livre":
            contexto[resto] = valor
            continue
        pasta = contexto
        *meio, folha = campo.origem.split(".")
        for parte in meio:
            pasta = pasta.setdefault(parte, {})
        pasta[folha] = valor
    return contexto


def renderizar(modelo: ModeloDocumento, valores: Mapping[str, str]) -> str:
    contexto = _contexto_inferido(modelo, valores) if modelo.inferido else dict(valores)
    try:
        return _ambiente().from_string(modelo.corpo).render(contexto)
    except TemplateError as exc:
        raise ModeloInvalido(f"falha ao preencher o modelo: {type(exc).__name__}") from exc


_FONTES_REGISTRADAS = False


def _registrar_fontes() -> None:
    global _FONTES_REGISTRADAS
    if _FONTES_REGISTRADAS:
        return
    import reportlab
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    pasta = Path(reportlab.__file__).parent / "fonts"
    for nome in ("Vera", "VeraBd", "VeraIt", "VeraBI"):
        pdfmetrics.registerFont(TTFont(nome, str(pasta / f"{nome}.ttf")))
    addMapping("Vera", 0, 0, "Vera")
    addMapping("Vera", 1, 0, "VeraBd")
    addMapping("Vera", 0, 1, "VeraIt")
    addMapping("Vera", 1, 1, "VeraBI")
    _FONTES_REGISTRADAS = True


def _inline(linha: str) -> str:
    seguro = escape(linha)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", seguro)


def gerar_pdf(titulo: str, texto: str, rodape: str) -> bytes:
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    _registrar_fontes()
    corpo = ParagraphStyle("corpo", fontName="Vera", fontSize=10, leading=14, alignment=TA_JUSTIFY)
    t1 = ParagraphStyle("t1", parent=corpo, fontName="VeraBd", fontSize=14, leading=18)
    t2 = ParagraphStyle("t2", parent=corpo, fontName="VeraBd", fontSize=11, leading=15)
    item = ParagraphStyle("item", parent=corpo, leftIndent=12, bulletIndent=2)

    blocos: list[Any] = []
    paragrafo: list[str] = []

    def fechar() -> None:
        if paragrafo:
            blocos.append(Paragraph(_inline(" ".join(paragrafo)), corpo))
            blocos.append(Spacer(1, 2 * mm))
            paragrafo.clear()

    for bruta in texto.splitlines():
        linha = bruta.strip()
        if not linha:
            fechar()
        elif linha.startswith("# "):
            fechar()
            blocos += [Paragraph(_inline(linha[2:]), t1), Spacer(1, 3 * mm)]
        elif linha.startswith("## "):
            fechar()
            blocos += [Paragraph(_inline(linha[3:]), t2), Spacer(1, 2 * mm)]
        elif linha.startswith(("- ", "* ")):
            fechar()
            blocos.append(Paragraph(_inline(linha[2:]), item, bulletText="•"))
        else:
            paragrafo.append(linha)
    fechar()

    def pagina(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Vera", 7)
        canvas.drawString(20 * mm, 10 * mm, f"{rodape} · página {doc.page}")
        canvas.restoreState()

    saida = BytesIO()
    doc = SimpleDocTemplate(
        saida,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=titulo,
        author="BRM",
        creator="BRM",
        invariant=1,
    )
    doc.build(blocos, onFirstPage=pagina, onLaterPages=pagina)
    return saida.getvalue()


@dataclass(frozen=True)
class DocumentoEmitido:
    id: str
    modelo_id: str
    modelo_versao: int
    modelo_hash: str
    titulo: str
    sha256: str
    referencia: str | None
    metadados: dict[str, Any]
    emitido_por: str
    emitido_papel: str
    emitido_em: datetime
    acao_id: str | None
    trace_id: str | None
    substitui: str | None

    def publico(self) -> dict[str, Any]:
        return {
            "documento_id": self.id,
            "modelo_id": self.modelo_id,
            "modelo_versao": self.modelo_versao,
            "titulo": self.titulo,
            "sha256": self.sha256,
            "referencia": self.referencia,
            "emitido_por": self.emitido_por,
            "emitido_em": self.emitido_em.isoformat(),
            "acao_id": self.acao_id,
            "substitui": self.substitui,
        }


def _to_doc(row: DocumentoEmitidoRow) -> DocumentoEmitido:
    emitido = row.emitido_em if row.emitido_em.tzinfo else row.emitido_em.replace(tzinfo=UTC)
    return DocumentoEmitido(
        id=row.id,
        modelo_id=row.modelo_id,
        modelo_versao=row.modelo_versao,
        modelo_hash=row.modelo_hash,
        titulo=row.titulo,
        sha256=row.sha256,
        referencia=row.referencia,
        metadados=json.loads(row.metadados),
        emitido_por=row.emitido_por,
        emitido_papel=row.emitido_papel,
        emitido_em=emitido,
        acao_id=row.acao_id,
        trace_id=row.trace_id,
        substitui=row.substitui,
    )


_IMUTAVEL = (
    "CREATE TRIGGER IF NOT EXISTS documento_emitido_sem_update BEFORE UPDATE ON documento_emitido "
    "BEGIN SELECT RAISE(ABORT, 'documento emitido é imutável'); END",
    "CREATE TRIGGER IF NOT EXISTS documento_emitido_sem_delete BEFORE DELETE ON documento_emitido "
    "BEGIN SELECT RAISE(ABORT, 'documento emitido é imutável'); END",
    "CREATE TRIGGER IF NOT EXISTS modelo_aprovado_sem_update BEFORE UPDATE ON modelo_aprovado "
    "BEGIN SELECT RAISE(ABORT, 'aprovação de modelo é imutável'); END",
)


class DocumentoStore:
    def __init__(self, state: StateStore, data_root: Path, client_id: str) -> None:
        self._engine = state.engine
        self._pasta = Path(data_root) / client_id / "documentos"
        with self._engine.begin() as conn:
            for ddl in _IMUTAVEL:
                conn.execute(text(ddl))

    def aprovar_modelo(self, modelo: ModeloDocumento, por: str) -> bool:
        """Registra o hash desta versão como aprovado. Falso se a versão já tinha outro hash."""
        with Session(self._engine) as session:
            existente = session.scalar(
                select(ModeloAprovadoRow).where(
                    ModeloAprovadoRow.modelo_id == modelo.id,
                    ModeloAprovadoRow.versao == modelo.versao,
                )
            )
            if existente is not None:
                return existente.hash == modelo.hash
            session.add(
                ModeloAprovadoRow(
                    modelo_id=modelo.id,
                    versao=modelo.versao,
                    hash=modelo.hash,
                    aprovado_por=por,
                    aprovado_em=datetime.now(UTC),
                )
            )
            session.commit()
            return True

    def aprovacao(self, modelo: ModeloDocumento) -> dict[str, Any] | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(ModeloAprovadoRow).where(
                    ModeloAprovadoRow.modelo_id == modelo.id,
                    ModeloAprovadoRow.versao == modelo.versao,
                    ModeloAprovadoRow.hash == modelo.hash,
                )
            )
            if row is None:
                return None
            return {"aprovado_por": row.aprovado_por, "aprovado_em": row.aprovado_em.isoformat()}

    def emitir(
        self,
        modelo: ModeloDocumento,
        fontes: Mapping[str, Any],
        *,
        emitido_por: str,
        emitido_papel: str,
        referencia: str | None = None,
        metadados: Mapping[str, Any] | None = None,
        acao_id: str | None = None,
        trace_id: str | None = None,
        substitui: str | None = None,
    ) -> DocumentoEmitido:
        if self.aprovacao(modelo) is None:
            raise ModeloNaoAprovado(
                f"o modelo {modelo.id!r} v{modelo.versao} não está aprovado com este conteúdo"
            )
        if substitui is not None:
            anterior = self.obter(substitui)
            if anterior is None or anterior.modelo_id != modelo.id:
                raise DocumentoErro("o documento substituído não existe ou é de outro modelo")
            if anterior.referencia != referencia:
                raise DocumentoErro("a correção precisa ter a mesma referência do original")
        valores = resolver_valores(modelo, fontes)
        documento_id = secrets.token_hex(16)
        agora = datetime.now(UTC)
        rodape = f"Documento {documento_id} · {modelo.id} v{modelo.versao} · {agora:%d/%m/%Y}"
        if substitui:
            rodape += f" · substitui {substitui}"
        pdf = gerar_pdf(modelo.titulo, renderizar(modelo, valores), rodape)
        digest = hashlib.sha256(pdf).hexdigest()

        self._pasta.mkdir(parents=True, exist_ok=True)
        caminho = self._pasta / f"{documento_id}.pdf"
        fd = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(fd, "wb") as arquivo:
            arquivo.write(pdf)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        row = DocumentoEmitidoRow(
            id=documento_id,
            modelo_id=modelo.id,
            modelo_versao=modelo.versao,
            modelo_hash=modelo.hash,
            titulo=modelo.titulo,
            sha256=digest,
            dados=json.dumps(valores, sort_keys=True, ensure_ascii=False),
            referencia=referencia,
            metadados=json.dumps(dict(metadados or {}), sort_keys=True, default=str),
            emitido_por=emitido_por,
            emitido_papel=emitido_papel,
            emitido_em=agora,
            acao_id=acao_id,
            trace_id=trace_id,
            substitui=substitui,
        )
        with Session(self._engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return _to_doc(row)

    def obter(self, documento_id: str) -> DocumentoEmitido | None:
        with Session(self._engine) as session:
            row = session.get(DocumentoEmitidoRow, documento_id)
            return _to_doc(row) if row else None

    def dados(self, documento_id: str) -> dict[str, str] | None:
        with Session(self._engine) as session:
            row = session.get(DocumentoEmitidoRow, documento_id)
            return json.loads(row.dados) if row else None

    def ler_pdf(self, documento_id: str) -> bytes:
        doc = self.obter(documento_id)
        if doc is None or not re.fullmatch(r"[0-9a-f]{32}", documento_id):
            raise DocumentoErro("documento inexistente")
        conteudo = (self._pasta / f"{documento_id}.pdf").read_bytes()
        if hashlib.sha256(conteudo).hexdigest() != doc.sha256:
            raise IntegridadeViolada(f"o PDF do documento {documento_id} não confere com o hash")
        return conteudo

    def listar(
        self, referencia: str | None = None, modelo_id: str | None = None
    ) -> list[DocumentoEmitido]:
        with Session(self._engine) as session:
            stmt = select(DocumentoEmitidoRow).order_by(DocumentoEmitidoRow.emitido_em)
            if referencia is not None:
                stmt = stmt.where(DocumentoEmitidoRow.referencia == referencia)
            if modelo_id is not None:
                stmt = stmt.where(DocumentoEmitidoRow.modelo_id == modelo_id)
            return [_to_doc(r) for r in session.scalars(stmt)]


Auditor = Callable[[str, bool, dict[str, Any]], None]


class ServicoDocumentos:
    """Fachada do emissor para o motor e para os tipos de ação: modelos do vault, prévia,
    emissão auditada (o evento de intenção vem antes da escrita) e consulta."""

    def __init__(
        self,
        store: DocumentoStore,
        vault_root: Path,
        dados_do_emissor: Mapping[str, str],
        auditar: Auditor,
    ) -> None:
        self.store = store
        self._vault_root = Path(vault_root)
        self._emissor = dict(dados_do_emissor)
        self._auditar = auditar

    @property
    def emissor(self) -> dict[str, str]:
        return dict(self._emissor)

    def modelos(self) -> tuple[dict[str, ModeloDocumento], dict[str, str]]:
        return carregar_modelos(self._vault_root)

    def modelo(self, modelo_id: str) -> ModeloDocumento:
        modelos, erros = self.modelos()
        if modelo_id in modelos:
            return modelos[modelo_id]
        if erros:
            raise DocumentoErro(f"modelo {modelo_id!r} indisponível (há modelos com erro)")
        raise DocumentoErro(f"modelo {modelo_id!r} não encontrado em {PASTA_MODELOS}/")

    def _fontes(self, fontes: Mapping[str, Any]) -> dict[str, Any]:
        return {"emissor": dict(self._emissor), **fontes}

    def faltantes(self, modelo_id: str, fontes: Mapping[str, Any]) -> list[CampoModelo]:
        return campos_faltantes(self.modelo(modelo_id), self._fontes(fontes))

    def previa(self, modelo_id: str, fontes: Mapping[str, Any]) -> list[dict[str, Any]]:
        modelo = self.modelo(modelo_id)
        completas = self._fontes(fontes)
        faltando = {c.nome for c in campos_faltantes(modelo, completas)}
        linhas = []
        for campo in modelo.campos:
            bruto = _dig(completas, campo.origem)
            linha: dict[str, Any] = {
                "campo": campo.nome,
                "rotulo": campo.rotulo or campo.nome,
                "tipo": campo.tipo,
                "origem": campo.origem,
                "obrigatorio": _exigido(campo, completas),
                "faltante": campo.nome in faltando,
                "valor": None,
            }
            if not _vazio(bruto):
                try:
                    linha["valor"] = _formatar(campo, bruto)
                except ValorInvalido as exc:
                    linha["invalido"] = exc.motivo
            linhas.append(linha)
        return linhas

    def emitir(
        self,
        modelo_id: str,
        fontes: Mapping[str, Any],
        *,
        emitido_por: str,
        emitido_papel: str,
        referencia: str | None = None,
        metadados: Mapping[str, Any] | None = None,
        acao_id: str | None = None,
        trace_id: str | None = None,
        substitui: str | None = None,
    ) -> DocumentoEmitido:
        modelo = self.modelo(modelo_id)
        base = {
            "modelo_id": modelo.id,
            "modelo_versao": modelo.versao,
            "referencia": referencia,
            "acao_id": acao_id,
            "substitui": substitui,
        }
        self._auditar("documento:emitindo", True, base)
        try:
            doc = self.store.emitir(
                modelo,
                self._fontes(fontes),
                emitido_por=emitido_por,
                emitido_papel=emitido_papel,
                referencia=referencia,
                metadados=metadados,
                acao_id=acao_id,
                trace_id=trace_id,
                substitui=substitui,
            )
        except DocumentoErro as exc:
            self._auditar("documento:recusado", False, {**base, "motivo": str(exc)})
            raise
        self._auditar(
            "documento:emitido", True, {**base, "documento_id": doc.id, "sha256": doc.sha256}
        )
        return doc

    def emitidos(self, referencia: str, modelo_id: str | None = None) -> list[dict[str, Any]]:
        return [d.publico() for d in self.store.listar(referencia, modelo_id)]
