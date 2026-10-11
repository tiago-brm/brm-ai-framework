"""Importação de planilhas para o ledger (spec 024): dry-run por padrão, idempotente."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from mcp_engine.modules.financeiro import lancamentos, params, sessoes
from mcp_engine.modules.financeiro.db import (
    ErroFinanceiro,
    abrir,
    agora_iso,
    hoje,
    normalizar_nome,
    parse_data,
)
from mcp_engine.modules.financeiro.importers import leitores
from mcp_engine.modules.financeiro.importers.mapeamento import Mapeamento, chave
from mcp_engine.modules.financeiro.importers.sessoes_parser import interpretar
from mcp_engine.modules.financeiro.lancamentos import Quem

_ESTORNO = re.compile(r"estorn|devol|desist|reembols", re.IGNORECASE)
_SEM_NF = re.compile(r"^S\s*/?\s*NF$", re.IGNORECASE)
GRUPO_NOME_VARIADA = "Variados"


@dataclass
class Opcoes:
    aplicar: bool
    quem: Quem
    mapeamento: Mapeamento
    hoje: date
    exigir_sem_pendencia: bool = False


@dataclass
class _Linha:
    status: str
    valor: int | None
    bucket: tuple[str, str, str]
    chave: str | None = None
    lancamento_id: int | None = None


class _Chaves:
    def __init__(self, base: tuple[Any, ...]) -> None:
        self.base = base
        self.cont: Counter[str] = Counter()

    def gerar(self, *campos: Any) -> str:
        texto = json.dumps([*self.base, *campos], ensure_ascii=False, default=str)
        self.cont[texto] += 1
        return hashlib.sha256(f"{texto}#{self.cont[texto]}".encode()).hexdigest()


def _iso(v: Any) -> str:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v).strip() if v is not None else ""


def _data(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


class _Cache:
    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con
        self.formas: dict[str, int] = {}
        self.unidades: dict[str, tuple[int, str]] = {}
        self.profissionais: dict[str, int | None] = {}
        self.categorias: dict[tuple[str, str], int] = {}

    def forma(self, nome: str) -> int:
        if nome not in self.formas:
            self.formas[nome] = params.id_forma(self.con, nome)
        return self.formas[nome]

    def unidade(self, nome: str | None) -> tuple[int | None, str]:
        if not nome:
            return None, "sem unidade"
        k = normalizar_nome(nome)
        if k not in self.unidades:
            row = self.con.execute(
                "SELECT id, nome FROM unidade WHERE nome = ? COLLATE NOCASE", (nome.strip(),)
            ).fetchone()
            if row:
                self.unidades[k] = (row["id"], row["nome"])
            else:
                novo = nome.strip().title()
                self.unidades[k] = (params.garantir_unidade(self.con, novo), novo)
        return self.unidades[k]

    def profissional(self, nome: str) -> int | None:
        k = normalizar_nome(nome)
        if k not in self.profissionais:
            row = self.con.execute(
                "SELECT id FROM profissional WHERE nome_norm = ?", (k,)
            ).fetchone()
            self.profissionais[k] = row["id"] if row else None
        return self.profissionais[k]

    def categoria(self, grupo: str, nome: str) -> int:
        k = (grupo, nome)
        if k not in self.categorias:
            self.categorias[k] = params.garantir_categoria(self.con, grupo, nome)
        return self.categorias[k]


def _classificar_nf(v: Any) -> tuple[str, str | None, str | None]:
    if v is None or str(v).strip() == "":
        return "pendente", None, None
    texto = str(int(v)) if isinstance(v, float) and v == int(v) else str(v).strip()
    if _SEM_NF.match(texto):
        return "sem_nf", None, None
    if texto.isdigit():
        return "emitida", texto, None
    return "pendente", None, f"NF não reconhecida: {texto!r}"


def _pendente(
    con: sqlite3.Connection,
    lote: int,
    tipo: str,
    motivo: str,
    ch: str,
    aba: str,
    linha: int,
    detalhe: dict[str, Any],
    sugestao: str | None = None,
) -> None:
    sessoes.criar_pendencia(
        con,
        tipo,
        motivo,
        referencia=ch,
        sugestao=sugestao,
        detalhe=detalhe,
        lote_id=lote,
        aba=aba,
        linha=linha,
    )


def _registrar_linha(
    con: sqlite3.Connection,
    ch: str,
    lote: int,
    aba: str,
    linha: int,
    rec: int | None = None,
    desp: int | None = None,
) -> None:
    con.execute(
        "INSERT INTO importacao_linha (chave, lote_id, aba, linha, recebimento_id, despesa_id)"
        " VALUES (?,?,?,?,?,?)",
        (ch, lote, aba.strip(), linha, rec, desp),
    )


def _existente(con: sqlite3.Connection, ch: str) -> sqlite3.Row | None:
    return con.execute(
        "SELECT recebimento_id, despesa_id FROM importacao_linha WHERE chave = ?", (ch,)
    ).fetchone()


def _processar_recebimento(
    con: sqlite3.Connection,
    cache: _Cache,
    plan: leitores.Planilha,
    lin: leitores.LinhaRecebimento,
    chaves: _Chaves,
    lote: int,
    op: Opcoes,
    nfs: Counter[str],
    avisos: list[str],
) -> _Linha:
    unidade_id, unidade_nome = cache.unidade(plan.unidade)
    try:
        centavos = leitores.valor_em_centavos(lin.valor)
        erro_valor = None if centavos else "valor ausente ou zero"
    except ErroFinanceiro as exc:
        centavos, erro_valor = None, str(exc)
    d = _data(lin.data)
    mes = d.strftime("%Y-%m") if d else "sem-data"
    destino = None
    erro_forma = None
    if d:
        try:
            destino = op.mapeamento.resolver(lin.forma, d)
        except ErroFinanceiro as exc:
            erro_forma = str(exc)
    rotulo = (
        destino.forma if destino and destino.forma else normalizar_nome(lin.forma) or "sem forma"
    )
    bucket = (mes, rotulo, unidade_nome)
    ch = chaves.gerar(
        "rec",
        _iso(lin.data),
        normalizar_nome(lin.paciente),
        chave(lin.forma),
        _iso(lin.sessoes),
        centavos if centavos is not None else str(lin.valor),
        str(lin.nf or ""),
        normalizar_nome(lin.profissional),
        lin.comprovante,
    )
    ja = _existente(con, ch)
    if ja is not None:
        return _Linha("duplicada", centavos, bucket, ch, ja["recebimento_id"])
    detalhe: dict[str, Any] = {
        "chave": ch,
        "layout": "secretarias",
        "valor_centavos": centavos,
        "linha": {
            "data": _iso(lin.data),
            "paciente": lin.paciente,
            "forma": lin.forma,
            "profissional": lin.profissional,
            "unidade": unidade_nome,
            "nf": str(lin.nf) if lin.nf is not None else None,
            "comprovante": lin.comprovante,
            "observacao": lin.observacao,
            "sessoes": _iso(lin.sessoes),
        },
        "proposto": {},
    }

    def pend(tipo: str, motivo: str, sugestao: str | None = None) -> _Linha:
        _pendente(con, lote, tipo, motivo, ch, lin.aba, lin.linha, detalhe, sugestao)
        return _Linha("pendente", centavos, bucket, ch)

    if erro_valor or centavos is None:
        return pend("importacao_valor_invalido", f"Valor inválido: {erro_valor}")
    if centavos < 0:
        return pend(
            "importacao_valor_negativo",
            "Valor negativo na planilha: estorne o lançamento original pelo ledger.",
            "Use estornar_lancamento no recebimento original.",
        )
    if d is None:
        return pend("importacao_data_invalida", f"Data de pagamento inválida: {_iso(lin.data)!r}.")
    if not lin.paciente:
        return pend("importacao_paciente_vazio", "Linha sem nome de paciente.")
    if erro_forma or destino is None:
        return pend(
            "importacao_forma_desconhecida",
            erro_forma or "Forma de pagamento sem mapeamento.",
            "Inclua a forma em config/importacao.yaml ou informe a correção ao aceitar.",
        )
    detalhe["proposto"].update(forma=destino.forma, meio=destino.meio, tipo=destino.tipo)

    sess = interpretar(lin.sessoes, d)
    proposta = sess.datas
    if sess.erro or sess.confianca < op.mapeamento.confianca_minima:
        if sess.sugestao:
            proposta = sess.sugestao
        detalhe["proposto"]["datas_sessoes"] = [x.isoformat() for x in proposta]
        detalhe["proposto"]["juros"] = sess.juros
        return pend(
            "importacao_sessoes",
            sess.erro
            or f"Datas de sessão com baixa confiança ({sess.confianca:.1f}): "
            + "; ".join(sess.avisos),
            ("Sugestão: " + ", ".join(x.isoformat() for x in proposta)) if proposta else None,
        )
    detalhe["proposto"]["datas_sessoes"] = [x.isoformat() for x in sess.datas]
    detalhe["proposto"]["juros"] = sess.juros

    prof_id = None
    if lin.profissional:
        prof_id = cache.profissional(lin.profissional)
        if prof_id is None:
            return pend(
                "importacao_profissional_desconhecido",
                f"Profissional desconhecido: {lin.profissional!r}.",
                "Cadastre o profissional (e a comissão) antes de importar.",
            )
    paciente_nome = lin.paciente
    row = con.execute(
        "SELECT id, profissional_id FROM paciente_ref WHERE nome_norm = ?",
        (normalizar_nome(paciente_nome),),
    ).fetchone()
    if row is None:
        pac_id = params.garantir_paciente(
            con, paciente_nome, unidade_id=unidade_id, profissional_id=prof_id, status="a_confirmar"
        )
        avisos.append("paciente_a_confirmar")
    else:
        pac_id = row["id"]
        prof_id = prof_id or row["profissional_id"]

    nf_status, nf_numero, aviso_nf = _classificar_nf(lin.nf)
    if aviso_nf:
        avisos.append(aviso_nf)
    if nf_numero:
        nfs[nf_numero] += 1
    tipo = "juros" if sess.juros else destino.tipo
    if destino.forma == "credito":
        avisos.append("crédito importado como 1 parcela")

    lanc = {
        "data_pagamento": d,
        "paciente_id": pac_id,
        "forma_id": cache.forma(destino.forma),
        "valor": centavos,
        "profissional_id": prof_id,
        "unidade_id": unidade_id,
        "tipo": tipo,
        "nf_status": nf_status,
        "nf_numero": nf_numero,
        "comprovante_ref": lin.comprovante or None,
        "observacao": lin.observacao or None,
        "origem": "importacao",
        "lote_id": lote,
        "datas_sessoes": list(sess.datas),
        "secretaria": plan.secretaria,
    }
    con.execute("SAVEPOINT linha")
    try:
        if destino.meio:
            lanc["meio_id"] = params.meio_vigente(con, destino.meio, d)
        rid, _ = lancamentos.inserir_recebimento(con, lanc, op.quem, hoje=op.hoje)
        _registrar_linha(con, ch, lote, lin.aba, lin.linha, rec=rid)
        con.execute("RELEASE linha")
    except (ErroFinanceiro, sqlite3.IntegrityError) as exc:
        con.execute("ROLLBACK TO linha")
        con.execute("RELEASE linha")
        texto = str(exc) if isinstance(exc, ErroFinanceiro) else "Dado recusado pelo banco."
        return pend("importacao_erro", texto)
    if lin.observacao and _ESTORNO.search(lin.observacao):
        _pendente(
            con,
            lote,
            "importacao_possivel_estorno",
            f"Observação sugere estorno: {lin.observacao[:120]!r}.",
            ch + ":estorno",
            lin.aba,
            lin.linha,
            {"chave": ch, "valor_centavos": centavos},
            "Se confirmado, aceite a pendência para estornar o lançamento.",
        )
    return _Linha("inserida", centavos, bucket, ch, rid)


def _processar_despesa(
    con: sqlite3.Connection,
    cache: _Cache,
    plan: leitores.Planilha,
    lin: leitores.LinhaDespesa,
    chaves: _Chaves,
    lote: int,
    op: Opcoes,
    avisos: list[str],
) -> _Linha:
    try:
        centavos = leitores.valor_em_centavos(lin.valor)
        erro_valor = None if centavos else "valor ausente ou zero"
    except ErroFinanceiro as exc:
        centavos, erro_valor = None, str(exc)
    d = _data(lin.data)
    if d is None and isinstance(lin.data, int) and plan.mes:
        try:
            d = date(int(plan.mes[:4]), int(plan.mes[5:]), lin.data)
        except ValueError:
            d = None
    if d is None and plan.mes and lin.data in (None, ""):
        d = date(int(plan.mes[:4]), int(plan.mes[5:]), 1)
        avisos.append("despesa sem data: usado o dia 1 do mês")
    mes = d.strftime("%Y-%m") if d else "sem-data"
    bucket = (mes, lin.grupo, "despesa")
    descricao = lin.descricao or lin.forma or lin.grupo.upper()
    ch = chaves.gerar(
        "desp",
        lin.grupo,
        _iso(lin.data),
        descricao,
        chave(lin.forma),
        centavos if centavos is not None else str(lin.valor),
    )
    ja = _existente(con, ch)
    if ja is not None:
        return _Linha("duplicada", centavos, bucket, ch, ja["despesa_id"])
    detalhe = {
        "chave": ch,
        "layout": "financeiro",
        "valor_centavos": centavos,
        "linha": {
            "data": _iso(lin.data),
            "descricao": descricao,
            "forma": lin.forma,
            "grupo": lin.grupo,
        },
        "proposto": {},
    }

    def pend(tipo: str, motivo: str) -> _Linha:
        _pendente(con, lote, tipo, motivo, ch, lin.aba, lin.linha, detalhe)
        return _Linha("pendente", centavos, bucket, ch)

    if erro_valor or centavos is None or centavos < 0:
        return pend("importacao_valor_invalido", f"Valor inválido: {erro_valor or 'negativo'}")
    if d is None:
        return pend("importacao_data_invalida", "Data da despesa não identificada.")
    try:
        destino = op.mapeamento.resolver(lin.forma, d, despesa=True) if lin.forma else None
    except ErroFinanceiro as exc:
        return pend("importacao_forma_desconhecida", str(exc))
    forma_id = cache.forma(destino.forma) if destino and destino.forma else None
    nome_cat = GRUPO_NOME_VARIADA if lin.grupo == "variada" else descricao[:80]
    con.execute("SAVEPOINT linha")
    try:
        rid, _ = lancamentos.inserir_despesa(
            con,
            {
                "data": d,
                "descricao": descricao[:200],
                "categoria_id": cache.categoria(lin.grupo, nome_cat),
                "forma_id": forma_id,
                "valor": centavos,
                "origem": "importacao",
                "lote_id": lote,
                "recorrente": lin.grupo == "fixa",
            },
            op.quem,
        )
        _registrar_linha(con, ch, lote, lin.aba, lin.linha, desp=rid)
        con.execute("RELEASE linha")
    except (ErroFinanceiro, sqlite3.IntegrityError) as exc:
        con.execute("ROLLBACK TO linha")
        con.execute("RELEASE linha")
        return pend(
            "importacao_erro", str(exc) if isinstance(exc, ErroFinanceiro) else "Dado recusado."
        )
    return _Linha("inserida", centavos, bucket, ch, rid)


def _soma_ledger(con: sqlite3.Connection, linhas: list[_Linha], despesa: bool) -> dict[tuple, int]:
    ids = [
        x.lancamento_id for x in linhas if x.status in ("inserida", "duplicada") and x.lancamento_id
    ]
    soma: dict[tuple, int] = defaultdict(int)
    for i in range(0, len(ids), 500):
        lote_ids = ids[i : i + 500]
        marcas = ",".join("?" * len(lote_ids))
        if despesa:
            sql = (
                "SELECT substr(d.data,1,7) m, c.grupo g, '-' u, SUM(d.valor_centavos) s FROM"
                " despesa d"
                f" JOIN categoria_despesa c ON c.id = d.categoria_id WHERE d.id IN ({marcas})"
                " GROUP BY 1,2"
            )
        else:
            sql = (
                "SELECT substr(r.data_pagamento,1,7) m, f.nome g, COALESCE(u.nome,'sem"
                " unidade') u,"
                " SUM(r.valor_centavos) s FROM recebimento r JOIN forma_pagamento f ON f.id ="
                " r.forma_id"
                f" LEFT JOIN unidade u ON u.id = r.unidade_id WHERE r.id IN ({marcas}) GROUP BY"
                " 1,2,3"
            )
        for r in con.execute(sql, lote_ids):
            k = (r["m"], r["g"], "despesa" if despesa else r["u"])
            soma[k] += r["s"]
    return soma


def _conciliar(
    con: sqlite3.Connection, linhas: list[_Linha], despesa: bool
) -> list[dict[str, Any]]:
    plan: dict[tuple, int] = defaultdict(int)
    pend: dict[tuple, int] = defaultdict(int)
    for x in linhas:
        if x.valor is None:
            continue
        plan[x.bucket] += x.valor
        if x.status == "pendente":
            pend[x.bucket] += x.valor
    ledger = _soma_ledger(con, linhas, despesa)
    out = []
    for k in sorted(set(plan) | set(ledger), key=str):
        dif = plan.get(k, 0) - ledger.get(k, 0) - pend.get(k, 0)
        out.append(
            {
                "mes": k[0],
                "forma": k[1],
                "unidade": k[2],
                "planilha_centavos": plan.get(k, 0),
                "ledger_centavos": ledger.get(k, 0),
                "pendente_centavos": pend.get(k, 0),
                "diferenca_centavos": dif,
            }
        )
    return out


def importar(
    db_path: Path | str,
    arquivo: Path | str,
    opcoes: Opcoes,
    *,
    mes: str | None = None,
    layout: str | None = None,
) -> dict[str, Any]:
    arquivo = Path(arquivo)
    plan = leitores.ler(arquivo, mes=mes, layout=layout)
    con = abrir(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            relatorio = _executar(con, plan, opcoes)
        except BaseException:
            con.execute("ROLLBACK")
            raise
        gravar = opcoes.aplicar and relatorio["ok"]
        con.execute("COMMIT" if gravar else "ROLLBACK")
        relatorio["modo"] = (
            "aplicado" if gravar else ("dry-run" if not opcoes.aplicar else "revertido")
        )
        return relatorio
    finally:
        con.close()


def _executar(con: sqlite3.Connection, plan: leitores.Planilha, op: Opcoes) -> dict[str, Any]:
    lote = con.execute(
        "INSERT INTO importacao_lote (arquivo_hash, arquivo_nome, layout, criado_por, criado_em)"
        " VALUES (?,?,?,?,?)",
        (plan.hash, plan.arquivo, plan.layout, op.quem.actor, agora_iso()),
    ).lastrowid
    cache = _Cache(con)
    avisos: list[str] = list(plan.avisos)
    nfs: Counter[str] = Counter()
    resultados: list[_Linha] = []
    ignoradas = 0
    base = (plan.layout, plan.mes, plan.unidade)
    chaves = _Chaves(base)
    if plan.layout == "secretarias":
        for lin in plan.recebimentos:
            if lin.paciente == "" and lin.valor in (None, "") and lin.data in (None, ""):
                ignoradas += 1
                continue
            resultados.append(
                _processar_recebimento(con, cache, plan, lin, chaves, lote, op, nfs, avisos)
            )
    else:
        for lin in plan.despesas:
            resultados.append(_processar_despesa(con, cache, plan, lin, chaves, lote, op, avisos))
    despesa = plan.layout == "financeiro"
    buckets = _conciliar(con, resultados, despesa)
    contagem = Counter(x.status for x in resultados)
    motivos = Counter(
        r["tipo"]
        for r in con.execute(
            "SELECT tipo FROM pendencia_operacional WHERE lote_id = ? AND status = 'aberta'",
            (lote,),
        )
    )
    repetidas = sorted(n for n, c in nfs.items() if c > 1)
    if repetidas:
        avisos.append(f"NF repetida em linhas distintas: {', '.join(repetidas[:10])}")
    divergentes = [b for b in buckets if b["diferenca_centavos"] != 0]
    pendentes = contagem.get("pendente", 0)
    ok = not divergentes and not (op.exigir_sem_pendencia and pendentes)
    return {
        "arquivo": plan.arquivo,
        "arquivo_hash": plan.hash,
        "layout": plan.layout,
        "mes": plan.mes,
        "unidade": plan.unidade,
        "lote_id": lote,
        "linhas": {
            "lidas": len(resultados) + ignoradas,
            "inseridas": contagem.get("inserida", 0),
            "duplicadas": contagem.get("duplicada", 0),
            "pendentes": pendentes,
            "ignoradas": ignoradas,
        },
        "conciliacao": buckets,
        "divergencias": divergentes,
        "pendencias_por_motivo": dict(sorted(motivos.items())),
        "avisos": sorted(set(avisos)),
        "ok": ok,
    }


def aplicar_pendencia(
    con: sqlite3.Connection, pend: sqlite3.Row, correcao: dict[str, Any], quem: Quem, *, hoje: date
) -> dict[str, Any]:
    det = json.loads(pend["detalhe"] or "{}")
    ch = det.get("chave")
    if pend["tipo"] == "importacao_possivel_estorno":
        row = _existente(con, ch)
        if row is None or row["recebimento_id"] is None:
            raise ErroFinanceiro("Lançamento original da importação não encontrado.")
        rid, _ = lancamentos.estornar_recebimento(
            con, row["recebimento_id"], None, "estorno indicado na planilha", quem, hoje=hoje
        )
        return {"estorno_id": rid}
    if _existente(con, ch) is not None:
        raise ErroFinanceiro("Esta linha já foi importada.")
    linha, proposto = det["linha"], {**det.get("proposto", {}), **correcao}
    if det.get("layout") == "financeiro":
        raise ErroFinanceiro(
            "Corrija a despesa na planilha e reimporte, ou registre-a manualmente."
        )
    forma = proposto.get("forma")
    if not forma:
        raise ErroFinanceiro("Informe a forma na correção para aceitar esta linha.")
    d = date.fromisoformat(linha["data"])
    cache = _Cache(con)
    unidade_id, _ = cache.unidade(linha["unidade"])
    prof_id = cache.profissional(linha["profissional"]) if linha["profissional"] else None
    if linha["profissional"] and prof_id is None:
        raise ErroFinanceiro("Profissional ainda não cadastrado.")
    datas = [date.fromisoformat(x) for x in proposto.get("datas_sessoes", [])]
    if isinstance(correcao.get("datas_sessoes"), str):
        datas = [parse_data(x) for x in correcao["datas_sessoes"].split(",") if x.strip()]
    row = con.execute(
        "SELECT id, profissional_id FROM paciente_ref WHERE nome_norm = ?",
        (normalizar_nome(linha["paciente"]),),
    ).fetchone()
    pac_id = (
        row["id"]
        if row
        else params.garantir_paciente(
            con,
            linha["paciente"],
            unidade_id=unidade_id,
            profissional_id=prof_id,
            status="a_confirmar",
        )
    )
    prof_id = prof_id or (row["profissional_id"] if row else None)
    nf_status, nf_numero, _ = _classificar_nf(linha.get("nf"))
    meio = proposto.get("meio")
    rid, _ = lancamentos.inserir_recebimento(
        con,
        {
            "data_pagamento": d,
            "paciente_id": pac_id,
            "forma_id": params.id_forma(con, forma),
            "meio_id": params.meio_vigente(con, meio, d) if meio else None,
            "valor": det["valor_centavos"],
            "profissional_id": prof_id,
            "unidade_id": unidade_id,
            "tipo": "juros" if proposto.get("juros") else proposto.get("tipo", "sessao"),
            "nf_status": nf_status,
            "nf_numero": nf_numero,
            "comprovante_ref": linha.get("comprovante") or None,
            "observacao": linha.get("observacao") or None,
            "origem": "importacao",
            "lote_id": pend["lote_id"],
            "datas_sessoes": datas,
        },
        quem,
        hoje=hoje,
    )
    _registrar_linha(con, ch, pend["lote_id"], pend["aba"] or "", pend["linha"] or 0, rec=rid)
    return {"recebimento_id": rid}


def reverter_lote(db_path: Path | str, lote_id: int, quem: Quem, *, hoje: date) -> dict[str, Any]:
    """Estorna, por contra-lançamento, tudo o que o lote importou e ainda não foi estornado."""
    con = abrir(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            if (
                con.execute("SELECT 1 FROM importacao_lote WHERE id = ?", (lote_id,)).fetchone()
                is None
            ):
                raise ErroFinanceiro(f"Lote {lote_id} não encontrado.")
            if con.execute(
                "SELECT 1 FROM importacao_lote WHERE id = ? AND resumo = 'revertido'", (lote_id,)
            ).fetchone():
                raise ErroFinanceiro(f"O lote {lote_id} já foi revertido.")
            n_rec = n_desp = 0
            for r in con.execute(
                "SELECT id FROM recebimento WHERE lote_id = ? AND tipo <> 'estorno' ORDER BY id",
                (lote_id,),
            ).fetchall():
                try:
                    lancamentos.estornar_recebimento(
                        con, r["id"], None, f"reversão do lote {lote_id}", quem, hoje=hoje
                    )
                    n_rec += 1
                except ErroFinanceiro as exc:
                    if "totalmente estornado" not in str(exc):
                        raise
            for r in con.execute(
                "SELECT id FROM despesa WHERE lote_id = ? AND estorno_de IS NULL ORDER BY id",
                (lote_id,),
            ).fetchall():
                try:
                    lancamentos.estornar_despesa(
                        con, r["id"], None, f"reversão do lote {lote_id}", quem, hoje=hoje
                    )
                    n_desp += 1
                except ErroFinanceiro as exc:
                    if "totalmente estornado" not in str(exc):
                        raise
            con.execute("UPDATE importacao_lote SET resumo = 'revertido' WHERE id = ?", (lote_id,))
        except BaseException:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")
        return {"lote_id": lote_id, "recebimentos_estornados": n_rec, "despesas_estornadas": n_desp}
    finally:
        con.close()


def registrar_descarte(con: sqlite3.Connection, pend: sqlite3.Row) -> None:
    det = json.loads(pend["detalhe"] or "{}")
    ch = det.get("chave")
    if ch and pend["lote_id"] and _existente(con, ch) is None:
        _registrar_linha(con, ch, pend["lote_id"], pend["aba"] or "", pend["linha"] or 0)


def opcoes_padrao(
    quem: Quem,
    config_dir: Path | None,
    *,
    aplicar: bool = False,
    exigir_sem_pendencia: bool = False,
) -> Opcoes:
    arquivo = Path(config_dir) / "importacao.yaml" if config_dir else None
    return Opcoes(
        aplicar=aplicar,
        quem=quem,
        mapeamento=Mapeamento.carregar(arquivo),
        hoje=hoje(),
        exigir_sem_pendencia=exigir_sem_pendencia,
    )
