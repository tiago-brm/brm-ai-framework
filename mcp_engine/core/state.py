"""Estado transacional do motor, um SQLite por cliente (`data/<cliente>/state.db`).

Guarda o que precisa sobreviver a reinício e ser consultado com integridade:
resultados de execução idempotente (spec 021) e, nas próximas specs, as
operações com confirmação (022).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Text, UniqueConstraint, create_engine, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class StateBase(DeclarativeBase):
    pass


class ExecucaoRow(StateBase):
    __tablename__ = "execucao"

    idempotency_key: Mapped[str] = mapped_column(primary_key=True)
    skill: Mapped[str]
    resultado: Mapped[str]
    criado_em: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))


class StateStore:
    def __init__(self, data_root: Path, client_id: str) -> None:
        db_path = Path(data_root) / client_id / "state.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{db_path}")

        @event.listens_for(self.engine, "connect")
        def _pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

        StateBase.metadata.create_all(self.engine)

    def get_execucao(self, idempotency_key: str) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            row = session.scalar(
                select(ExecucaoRow).where(ExecucaoRow.idempotency_key == idempotency_key)
            )
            return json.loads(row.resultado) if row else None

    def save_execucao(self, idempotency_key: str, skill: str, resultado: dict[str, Any]) -> None:
        with Session(self.engine) as session:
            session.add(
                ExecucaoRow(
                    idempotency_key=idempotency_key,
                    skill=skill,
                    resultado=json.dumps(resultado, sort_keys=True, default=str),
                )
            )
            session.commit()


class OperacaoRow(StateBase):
    __tablename__ = "operacao"

    id: Mapped[str] = mapped_column(primary_key=True)
    skill: Mapped[str]
    argumentos: Mapped[str]
    digest_argumentos: Mapped[str]
    resumo: Mapped[str]
    status: Mapped[str] = mapped_column(index=True)
    proposto_por: Mapped[str]
    proposto_papel: Mapped[str]
    confirmado_por: Mapped[str | None] = mapped_column(default=None)
    aprovado_por: Mapped[str | None] = mapped_column(default=None)
    aprovador_papel: Mapped[str | None] = mapped_column(default=None)
    regra_id: Mapped[str | None] = mapped_column(default=None)
    motivo: Mapped[str | None] = mapped_column(default=None)
    resultado: Mapped[str | None] = mapped_column(default=None)
    criado_em: Mapped[datetime]
    expira_em: Mapped[datetime]
    executada_em: Mapped[datetime | None] = mapped_column(default=None)


class ConfigVersaoRow(StateBase):
    """Versão aprovada de uma nota de configuração (spec 030). Nunca é alterada."""

    __tablename__ = "config_versao"
    __table_args__ = (UniqueConstraint("caminho", "versao"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    caminho: Mapped[str] = mapped_column(index=True)
    versao: Mapped[int]
    tipo: Mapped[str]
    config_id: Mapped[str]
    conteudo: Mapped[str]
    hash: Mapped[str]
    proposto_por: Mapped[str]
    aprovado_por: Mapped[str]
    aprovado_em: Mapped[datetime]
    operacao_id: Mapped[str]


class PaginaGeradaRow(StateBase):
    __tablename__ = "pagina_gerada"

    caminho: Mapped[str] = mapped_column(primary_key=True)
    hash: Mapped[str]
    atualizado_em: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))


class ConhecimentoRow(StateBase):
    __tablename__ = "conhecimento_hash"

    caminho: Mapped[str] = mapped_column(primary_key=True)
    hash: Mapped[str]
    visto_em: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))


class ControleRow(StateBase):
    """Chaves de controle do motor (por exemplo, a pausa global dos gatilhos)."""

    __tablename__ = "controle"

    chave: Mapped[str] = mapped_column(primary_key=True)
    valor: Mapped[str]
    atualizado_em: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))


class GatilhoRow(StateBase):
    """Gatilho ativo (spec 031): definição do snapshot aprovado mais o estado de execução."""

    __tablename__ = "gatilho"

    config_id: Mapped[str] = mapped_column(primary_key=True)
    caminho: Mapped[str]
    hash: Mapped[str]
    versao: Mapped[int]
    aprovado_por: Mapped[str]
    definicao: Mapped[str] = mapped_column(Text)
    ativo: Mapped[bool] = mapped_column(default=True)
    motivo_desativacao: Mapped[str | None] = mapped_column(default=None)
    proxima_execucao: Mapped[datetime | None] = mapped_column(default=None)
    ultima_execucao: Mapped[datetime | None] = mapped_column(default=None)
    falhas_seguidas: Mapped[int] = mapped_column(default=0)


class GatilhoExecucaoRow(StateBase):
    __tablename__ = "gatilho_execucao"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    gatilho_id: Mapped[str] = mapped_column(index=True)
    chave: Mapped[str] = mapped_column(unique=True)
    janela: Mapped[str]
    origem: Mapped[str]
    evento_id: Mapped[int | None] = mapped_column(default=None)
    profundidade: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(index=True)
    hash_definicao: Mapped[str]
    iniciado_em: Mapped[datetime]
    concluido_em: Mapped[datetime | None] = mapped_column(default=None)
    resultado: Mapped[str | None] = mapped_column(Text, default=None)
    erro: Mapped[str | None] = mapped_column(default=None)
    trace_id: Mapped[str | None] = mapped_column(default=None)
    operacao_id: Mapped[str | None] = mapped_column(default=None)


class EventoRow(StateBase):
    """Outbox de eventos (spec 031): publicado só depois do commit do efeito de domínio."""

    __tablename__ = "evento"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tipo: Mapped[str] = mapped_column(index=True)
    payload: Mapped[str] = mapped_column(Text)
    criado_em: Mapped[datetime]
    profundidade: Mapped[int] = mapped_column(default=0)
    origem_gatilho: Mapped[str | None] = mapped_column(default=None)
    chave_dedup: Mapped[str | None] = mapped_column(unique=True, default=None)
    trace_id: Mapped[str | None] = mapped_column(default=None)
    processado_em: Mapped[datetime | None] = mapped_column(default=None, index=True)


class GatilhoAlertaRow(StateBase):
    __tablename__ = "gatilho_alerta"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    gatilho_id: Mapped[str] = mapped_column(index=True)
    execucao_id: Mapped[int | None] = mapped_column(default=None)
    nivel: Mapped[str]
    texto: Mapped[str] = mapped_column(Text)
    destinatario: Mapped[str]
    criado_em: Mapped[datetime]
    reconhecido_por: Mapped[str | None] = mapped_column(default=None)
    reconhecido_em: Mapped[datetime | None] = mapped_column(default=None)


class AtributosUsuarioRow(StateBase):
    """Atributos de governança de uma pessoa (spec 035), chaveados pelo provedor de identidade
    e pelo id do usuário nele. Nunca guarda senha nem cadastro: só o hash do PIN e escopo."""

    __tablename__ = "atributos_usuario"

    provedor: Mapped[str] = mapped_column(primary_key=True)
    usuario_id: Mapped[str] = mapped_column(primary_key=True)
    pin_hash: Mapped[str | None] = mapped_column(default=None)
    pin_falhas: Mapped[int] = mapped_column(default=0)
    pin_bloqueado_ate: Mapped[datetime | None] = mapped_column(default=None)
    unidades: Mapped[str] = mapped_column(Text, default="[]")
    atualizado_em: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    atualizado_por: Mapped[str | None] = mapped_column(default=None)


class AcaoRow(StateBase):
    """Ação da Central de Ações (spec 035): pedido guardado no servidor, uso único."""

    __tablename__ = "acao"

    id: Mapped[str] = mapped_column(primary_key=True)
    itens: Mapped[str] = mapped_column(Text)
    digest: Mapped[str]
    resumo: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(index=True)
    criado_por: Mapped[str] = mapped_column(index=True)
    criado_papel: Mapped[str]
    criado_em: Mapped[datetime]
    expira_em: Mapped[datetime]
    trace_id: Mapped[str | None] = mapped_column(default=None)
    sensivel: Mapped[bool] = mapped_column(default=False)
    papel_minimo: Mapped[str] = mapped_column(default="viewer")
    papel_liberacao: Mapped[str] = mapped_column(default="admin")
    confirmado_por: Mapped[str | None] = mapped_column(default=None)
    confirmado_papel: Mapped[str | None] = mapped_column(default=None)
    confirmado_provedor: Mapped[str | None] = mapped_column(default=None)
    metodo: Mapped[str | None] = mapped_column(default=None)
    liberado_por: Mapped[str | None] = mapped_column(default=None)
    resultado: Mapped[str | None] = mapped_column(Text, default=None)
    motivo: Mapped[str | None] = mapped_column(default=None)
    concluida_em: Mapped[datetime | None] = mapped_column(default=None)


class ModeloAprovadoRow(StateBase):
    """Hash de uma versão de modelo de documento aprovada para emissão. Nunca é alterada."""

    __tablename__ = "modelo_aprovado"
    __table_args__ = (UniqueConstraint("modelo_id", "versao"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    modelo_id: Mapped[str] = mapped_column(index=True)
    versao: Mapped[int]
    hash: Mapped[str]
    aprovado_por: Mapped[str]
    aprovado_em: Mapped[datetime]


class DocumentoEmitidoRow(StateBase):
    """Documento emitido: imutável (gatilhos do SQLite recusam UPDATE e DELETE)."""

    __tablename__ = "documento_emitido"

    id: Mapped[str] = mapped_column(primary_key=True)
    modelo_id: Mapped[str] = mapped_column(index=True)
    modelo_versao: Mapped[int]
    modelo_hash: Mapped[str]
    titulo: Mapped[str]
    sha256: Mapped[str]
    dados: Mapped[str] = mapped_column(Text)
    referencia: Mapped[str | None] = mapped_column(index=True, default=None)
    metadados: Mapped[str] = mapped_column(Text, default="{}")
    emitido_por: Mapped[str]
    emitido_papel: Mapped[str]
    emitido_em: Mapped[datetime]
    acao_id: Mapped[str | None] = mapped_column(default=None)
    trace_id: Mapped[str | None] = mapped_column(default=None)
    substitui: Mapped[str | None] = mapped_column(default=None)
