"""Handlers nativos do módulo financeiro (executor de skills, spec 021)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date
from typing import Any

from mcp_engine.core.executor import EVENTOS_CHAVE, HandlerContext
from mcp_engine.modules.financeiro import consultas, gancho, lancamentos, params, sessoes
from mcp_engine.modules.financeiro.db import (
    ErroFinanceiro,
    abrir,
    caminho_ledger,
    hoje,
    normalizar_nome,
    parse_data,
    parse_mes,
    transacao,
)
from mcp_engine.modules.financeiro.lancamentos import (
    RETROATIVO_MAX_DIAS,
    Quem,
    estornar_despesa,
    estornar_recebimento,
)
from mcp_engine.modules.financeiro.money import ValorInvalido, formatar_brl, parse_brl

TIPOS_RECEBIMENTO = ("sessao", "pacote", "acordo", "juros")


def _texto(
    args: dict[str, Any], chave: str, *, obrigatorio: bool = False, maximo: int = 200
) -> str | None:
    valor = args.get(chave)
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        if obrigatorio:
            raise ErroFinanceiro(f"Informe {chave}.")
        return None
    if not isinstance(valor, str | int) or isinstance(valor, bool):
        raise ErroFinanceiro(f"{chave} deve ser um texto.")
    texto = str(valor).strip()
    if len(texto) > maximo:
        raise ErroFinanceiro(f"{chave} excede {maximo} caracteres.")
    return texto


def _inteiro(args: dict[str, Any], chave: str, *, obrigatorio: bool = False) -> int | None:
    texto = _texto(args, chave, obrigatorio=obrigatorio, maximo=12)
    if texto is None:
        return None
    if not texto.isdigit():
        raise ErroFinanceiro(f"{chave} deve ser um número inteiro.")
    return int(texto)


def _dinheiro(args: dict[str, Any], chave: str, *, obrigatorio: bool = True) -> int | None:
    bruto = args.get(chave)
    if bruto is None or bruto == "":
        if obrigatorio:
            raise ErroFinanceiro(f"Informe {chave}.")
        return None
    try:
        return parse_brl(bruto)
    except ValorInvalido as exc:
        raise ErroFinanceiro(str(exc)) from None


def _bool(args: dict[str, Any], chave: str) -> bool:
    v = args.get(chave)
    return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "sim", "1"))


def _datas(args: dict[str, Any], chave: str) -> list[date]:
    bruto = args.get(chave)
    if not bruto:
        return []
    itens = bruto if isinstance(bruto, list) else str(bruto).split(",")
    return [parse_data(i) for i in itens if str(i).strip()]


def _quem(ctx: HandlerContext) -> Quem:
    return Quem(
        actor=ctx.actor,
        role=ctx.role,
        confirmado_por=ctx.confirmado_por,
        aprovado_por=ctx.aprovado_por,
    )


def _evento(nome: str, dedup: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"tipo": nome, "payload": payload, "dedup": dedup}


def _executar(
    ctx: HandlerContext,
    fn: Callable[[sqlite3.Connection, Quem, date], dict[str, Any]],
    *,
    escrita: bool = True,
) -> dict[str, Any]:
    con = None
    try:
        con = abrir(caminho_ledger(ctx.data_root, ctx.client_id))
        if not escrita:
            return fn(con, _quem(ctx), hoje())
        with transacao(con):
            return fn(con, _quem(ctx), hoje())
    except sqlite3.Error:
        raise ErroFinanceiro(
            "Ledger indisponível no momento: nada foi gravado. Tente novamente."
        ) from None
    finally:
        if con is not None:
            con.close()


def _paciente(con: sqlite3.Connection, nome: str) -> sqlite3.Row | None:
    rows = con.execute(
        "SELECT * FROM paciente_ref WHERE nome_norm = ?", (normalizar_nome(nome),)
    ).fetchall()
    if len(rows) > 1:
        raise ErroFinanceiro("Mais de um paciente com este nome: informe o cadastro correto.")
    return rows[0] if rows else None


PACIENTE_AUSENTE = (
    "Paciente não encontrado pelo nome completo informado: confira como está cadastrado na"
    " Agenda (a lista é atualizada automaticamente)."
)


def _garantir_paciente(ctx: HandlerContext, nome: str) -> None:
    """Se o paciente não está no ledger, pede a atualização da lista antes de registrar."""
    caminho = caminho_ledger(ctx.data_root, ctx.client_id)
    con = None
    try:
        con = abrir(caminho)
        if _paciente(con, nome) is not None:
            return
    except (sqlite3.Error, ErroFinanceiro):
        return
    finally:
        if con is not None:
            con.close()
    gancho.atualizar(caminho)


def _id_por_nome(con: sqlite3.Connection, tabela: str, nome: str, rotulo: str) -> int:
    campo = "nome_norm" if tabela in ("profissional",) else "nome"
    valor = normalizar_nome(nome) if campo == "nome_norm" else nome
    row = con.execute(
        f"SELECT id FROM {tabela} WHERE {campo} = ? COLLATE NOCASE",
        (valor,),
    ).fetchone()
    if not row:
        raise ErroFinanceiro(f"{rotulo} desconhecido(a): {nome!r}.")
    return row["id"]


def _recusa(
    con: sqlite3.Connection, tipo: str, motivo: str, args: dict[str, Any]
) -> dict[str, Any]:
    pid = sessoes.criar_pendencia(
        con, tipo, motivo, detalhe={k: v for k, v in args.items() if k != "observacao"}
    )
    return {"registrado": False, "pendencia_id": pid, "motivo": motivo}


def registrar_recebimento(ctx: HandlerContext, args: dict[str, Any]) -> dict[str, Any]:
    _garantir_paciente(ctx, _texto(args, "paciente", obrigatorio=True, maximo=160))

    def run(con: sqlite3.Connection, quem: Quem, agora: date) -> dict[str, Any]:
        if ctx.idempotency_key:
            ja = con.execute(
                "SELECT id FROM recebimento WHERE operacao_id = ?", (ctx.idempotency_key,)
            ).fetchone()
            if ja:
                return {"registrado": True, "recebimento_id": ja["id"], "reutilizado": True}
        nome = _texto(args, "paciente", obrigatorio=True, maximo=160)
        data = parse_data(_texto(args, "data_pagamento", obrigatorio=True))
        paciente = _paciente(con, nome)
        if paciente is None:
            return _recusa(
                con,
                "paciente_desconhecido",
                PACIENTE_AUSENTE,
                {"paciente": nome, "data_pagamento": data.isoformat()},
            )
        forma_nome = _texto(args, "forma", obrigatorio=True, maximo=30)
        forma_id = params.id_forma(con, forma_nome)
        forma = normalizar_nome(forma_nome)
        meio_nome = _texto(args, "meio", maximo=60)
        if meio_nome is None and forma == "dinheiro":
            meio_nome = "Caixa"
        if meio_nome is None and forma != "acordo":
            raise ErroFinanceiro("Informe a conta (meio) do recebimento.")
        meio_id = params.meio_vigente(con, meio_nome, data) if meio_nome else None
        tipo = _texto(args, "tipo", maximo=10) or ("acordo" if forma == "acordo" else "sessao")
        if tipo not in TIPOS_RECEBIMENTO:
            raise ErroFinanceiro(f"Tipo inválido: {tipo!r}.")
        prof_nome = _texto(args, "profissional", maximo=120)
        unid_nome = _texto(args, "unidade", maximo=80)
        d: dict[str, Any] = {
            "data_pagamento": data,
            "paciente_id": paciente["id"],
            "forma_id": forma_id,
            "meio_id": meio_id,
            "parcelas": _inteiro(args, "parcelas") or 1,
            "valor": _dinheiro(args, "valor"),
            "profissional_id": (
                _id_por_nome(con, "profissional", prof_nome, "Profissional")
                if prof_nome
                else paciente["profissional_id"]
            ),
            "unidade_id": (
                _id_por_nome(con, "unidade", unid_nome, "Unidade")
                if unid_nome
                else paciente["unidade_id"]
            ),
            "servico": normalizar_nome(_texto(args, "servico", maximo=80) or "") or None,
            "tipo": tipo,
            "nf_status": _texto(args, "nf_status", maximo=10) or "pendente",
            "nf_numero": _texto(args, "nf_numero", maximo=30),
            "datas_sessoes": _datas(args, "datas_sessoes"),
            "observacao": _texto(args, "observacao", maximo=300),
            "pacote_id": _inteiro(args, "pacote_id"),
            "cobranca_prevista_id": _inteiro(args, "cobranca_prevista_id"),
            "operacao_id": ctx.idempotency_key,
        }
        if d["valor"] <= 0:
            raise ErroFinanceiro("O valor do recebimento deve ser maior que zero.")
        fatos = lancamentos.fatos_recebimento(con, d, agora)
        if fatos["mes_fechado"]:
            raise ErroFinanceiro(
                "Mês fechado: o lançamento é recusado. Lance o ajuste no mês corrente."
            )
        if fatos["duplicidade_suspeita"] and not _bool(args, "confirmar_duplicidade"):
            return {
                "registrado": False,
                "duplicidade_suspeita": True,
                "motivo": "Já existe recebimento igual (paciente, valor, data e forma). "
                "Se for outro pagamento, repita com confirmar_duplicidade.",
                "fatos": fatos,
            }
        precisa_gestor = (
            fatos["valor_atipico"]
            or fatos["retroativo_dias"] > RETROATIVO_MAX_DIAS
            or fatos["paciente_a_confirmar"]
        )
        if precisa_gestor and not quem.gestor and not quem.aprovado_por:
            res = _recusa(
                con,
                "lancamento_para_revisao",
                "Lançamento atípico, retroativo ou de paciente a confirmar: requer o gestor.",
                args,
            )
            return {**res, "requer_gestor": True, "fatos": fatos}
        rid, reutilizado = lancamentos.inserir_recebimento(con, d, quem, hoje=agora)
        if fatos["duplicidade_suspeita"]:
            sessoes.criar_pendencia(
                con,
                "duplicidade_suspeita",
                "Recebimento confirmado como possível duplicidade.",
                referencia=f"recebimento:{rid}",
            )
        rep = con.execute("SELECT * FROM repasse WHERE recebimento_id = ?", (rid,)).fetchone()
        resultado = {
            "registrado": True,
            "recebimento_id": rid,
            "reutilizado": reutilizado,
            "valor_centavos": d["valor"],
            "valor": formatar_brl(d["valor"]),
            "repasse": {
                "taxa_centavos": rep["taxa_centavos"],
                "repasse_centavos": rep["repasse_centavos"],
                "parte_clinica_centavos": rep["parte_clinica_centavos"],
            },
            "fatos": fatos,
        }
        if not reutilizado:
            resultado[EVENTOS_CHAVE] = [
                _evento(
                    "recebimento.registrado",
                    f"recebimento:{rid}",
                    {
                        "recebimento_id": rid,
                        "valor_centavos": d["valor"],
                        "data_pagamento": d["data_pagamento"].isoformat(),
                        "tipo": d["tipo"],
                        "nf_status": d["nf_status"],
                    },
                )
            ]
        return resultado

    return _executar(ctx, run)


def _categoria(con: sqlite3.Connection, nome: str, grupo: str | None, quem: Quem) -> int:
    rows = con.execute(
        "SELECT id, grupo FROM categoria_despesa WHERE nome = ? COLLATE NOCASE"
        " AND (? IS NULL OR grupo = ?)",
        (nome, grupo, grupo),
    ).fetchall()
    if len(rows) == 1:
        return rows[0]["id"]
    if len(rows) > 1:
        raise ErroFinanceiro("Categoria existe em mais de um grupo: informe o grupo.")
    if grupo and quem.gestor:
        if grupo not in ("variada", "fixa", "imposto", "folha"):
            raise ErroFinanceiro("Grupo de despesa inválido.")
        return params.garantir_categoria(con, grupo, nome)
    raise ErroFinanceiro(f"Categoria desconhecida: {nome!r}. Peça ao gestor para cadastrá-la.")


def registrar_despesa(ctx: HandlerContext, args: dict[str, Any]) -> dict[str, Any]:
    def run(con: sqlite3.Connection, quem: Quem, agora: date) -> dict[str, Any]:
        if ctx.idempotency_key:
            ja = con.execute(
                "SELECT id FROM despesa WHERE operacao_id = ?", (ctx.idempotency_key,)
            ).fetchone()
            if ja:
                return {"registrado": True, "despesa_id": ja["id"], "reutilizado": True}
        forma_nome = _texto(args, "forma", maximo=30)
        d: dict[str, Any] = {
            "data": parse_data(_texto(args, "data", obrigatorio=True)),
            "descricao": _texto(args, "descricao", obrigatorio=True),
            "categoria_id": _categoria(
                con,
                _texto(args, "categoria", obrigatorio=True, maximo=80),
                _texto(args, "grupo", maximo=10),
                quem,
            ),
            "forma_id": params.id_forma(con, forma_nome) if forma_nome else None,
            "valor": _dinheiro(args, "valor"),
            "recorrente": _bool(args, "recorrente"),
            "comprovante_ref": _texto(args, "comprovante_ref", maximo=200),
            "operacao_id": ctx.idempotency_key,
        }
        if d["valor"] <= 0:
            raise ErroFinanceiro("O valor da despesa deve ser maior que zero.")
        fatos = lancamentos.fatos_despesa(con, d, agora)
        if fatos["mes_fechado"]:
            raise ErroFinanceiro(
                "Mês fechado: o lançamento é recusado. Lance o ajuste no mês corrente."
            )
        if fatos["duplicidade_suspeita"] and not _bool(args, "confirmar_duplicidade"):
            return {
                "registrado": False,
                "duplicidade_suspeita": True,
                "motivo": "Já existe despesa igual. Se for outra, repita com"
                " confirmar_duplicidade.",
                "fatos": fatos,
            }
        retroativo = fatos["retroativo_dias"] > RETROATIVO_MAX_DIAS
        if retroativo and not quem.gestor and not quem.aprovado_por:
            res = _recusa(
                con, "lancamento_para_revisao", "Despesa retroativa: requer o gestor.", args
            )
            return {**res, "requer_gestor": True, "fatos": fatos}
        rid, reutilizado = lancamentos.inserir_despesa(con, d, quem)
        resultado = {
            "registrado": True,
            "despesa_id": rid,
            "reutilizado": reutilizado,
            "valor": formatar_brl(d["valor"]),
            "fatos": fatos,
        }
        if not reutilizado:
            resultado[EVENTOS_CHAVE] = [
                _evento(
                    "despesa.registrada",
                    f"despesa:{rid}",
                    {
                        "despesa_id": rid,
                        "valor_centavos": d["valor"],
                        "data": d["data"].isoformat(),
                    },
                )
            ]
        return resultado

    return _executar(ctx, run)


def estornar_lancamento(ctx: HandlerContext, args: dict[str, Any]) -> dict[str, Any]:
    def run(con: sqlite3.Connection, quem: Quem, agora: date) -> dict[str, Any]:
        tipo = _texto(args, "tipo", obrigatorio=True, maximo=12)
        lid = _inteiro(args, "lancamento_id", obrigatorio=True)
        motivo = _texto(args, "motivo", obrigatorio=True, maximo=200)
        valor = _dinheiro(args, "valor", obrigatorio=False)
        if tipo == "recebimento":
            rid, reut = estornar_recebimento(
                con, lid, valor, motivo, quem, hoje=agora, operacao_id=ctx.idempotency_key
            )
        elif tipo == "despesa":
            rid, reut = estornar_despesa(
                con, lid, valor, motivo, quem, hoje=agora, operacao_id=ctx.idempotency_key
            )
        else:
            raise ErroFinanceiro("Tipo deve ser 'recebimento' ou 'despesa'.")
        resultado = {
            "estornado": True,
            "estorno_id": rid,
            "tipo": tipo,
            "original_id": lid,
            "reutilizado": reut,
        }
        if not reut:
            resultado[EVENTOS_CHAVE] = [
                _evento(
                    "lancamento.estornado",
                    f"{tipo}-estorno:{rid}",
                    {"estorno_id": rid, "original_id": lid, "tipo": tipo},
                )
            ]
        return resultado

    return _executar(ctx, run)


def anexar_comprovante(ctx: HandlerContext, args: dict[str, Any]) -> dict[str, Any]:
    def run(con: sqlite3.Connection, quem: Quem, agora: date) -> dict[str, Any]:
        tipo = _texto(args, "tipo", obrigatorio=True, maximo=12)
        lid = _inteiro(args, "lancamento_id", obrigatorio=True)
        lancamentos.anexar_comprovante(
            con, tipo, lid, _texto(args, "comprovante_ref", obrigatorio=True)
        )
        return {"anexado": True, "lancamento_id": lid}

    return _executar(ctx, run)


def atualizar_status_nf(ctx: HandlerContext, args: dict[str, Any]) -> dict[str, Any]:
    def run(con: sqlite3.Connection, quem: Quem, agora: date) -> dict[str, Any]:
        lid = _inteiro(args, "recebimento_id", obrigatorio=True)
        status = _texto(args, "nf_status", obrigatorio=True, maximo=10)
        numero = _texto(args, "nf_numero", maximo=30)
        lancamentos.atualizar_status_nf(con, lid, status, numero)
        repetida = (
            numero
            and con.execute(
                "SELECT COUNT(*) FROM recebimento WHERE nf_numero = ? AND id <> ?", (numero, lid)
            ).fetchone()[0]
        )
        return {
            "atualizado": True,
            "recebimento_id": lid,
            "avisos": ["Número de NF já usado em outro recebimento."] if repetida else [],
        }

    return _executar(ctx, run)


def registrar_falta(ctx: HandlerContext, args: dict[str, Any]) -> dict[str, Any]:
    def run(con: sqlite3.Connection, quem: Quem, agora: date) -> dict[str, Any]:
        nome = _texto(args, "paciente", maximo=160)
        paciente = _paciente(con, nome) if nome else None
        dia = parse_data(args["data_sessao"]) if args.get("data_sessao") else None
        sessao = sessoes.localizar_sessao(
            con, _texto(args, "agenda_id", maximo=80), paciente["id"] if paciente else None, dia
        )
        oid, reut = sessoes.registrar_falta(
            con,
            sessao["id"],
            _texto(args, "tipo", maximo=20) or "falta",
            _texto(args, "motivo", maximo=200),
            quem,
            operacao_id=ctx.idempotency_key,
        )
        conflito = sessao["status_agenda"] == "COMPLETED"
        return {
            "registrado": True,
            "ocorrencia_id": oid,
            "reutilizado": reut,
            "conflito_desfecho": conflito,
        }

    return _executar(ctx, run)


def registrar_pacote(ctx: HandlerContext, args: dict[str, Any]) -> dict[str, Any]:
    _garantir_paciente(ctx, _texto(args, "paciente", obrigatorio=True, maximo=160))

    def run(con: sqlite3.Connection, quem: Quem, agora: date) -> dict[str, Any]:
        nome = _texto(args, "paciente", obrigatorio=True, maximo=160)
        paciente = _paciente(con, nome)
        if paciente is None:
            return _recusa(
                con,
                "paciente_desconhecido",
                PACIENTE_AUSENTE,
                {"paciente": nome},
            )
        res = sessoes.registrar_pacote(
            con,
            paciente_id=paciente["id"],
            modelo_nome=_texto(args, "modelo", obrigatorio=True, maximo=80),
            inicio=parse_data(_texto(args, "inicio", obrigatorio=True)),
            quem=quem,
            preco_acordado=_dinheiro(args, "preco_acordado", obrigatorio=False),
            sessoes_totais=_inteiro(args, "sessoes_totais"),
            operacao_id=ctx.idempotency_key,
        )
        return {"registrado": True, **res}

    return _executar(ctx, run)


def resolver_pendencia(ctx: HandlerContext, args: dict[str, Any]) -> dict[str, Any]:
    def run(con: sqlite3.Connection, quem: Quem, agora: date) -> dict[str, Any]:
        if not quem.gestor:
            raise ErroFinanceiro("Resolver pendências é restrito ao gestor.")
        pid = _inteiro(args, "pendencia_id", obrigatorio=True)
        acao = _texto(args, "acao", obrigatorio=True, maximo=30)
        pend = con.execute(
            "SELECT * FROM pendencia_operacional WHERE id = ? AND status = 'aberta'", (pid,)
        ).fetchone()
        if pend is None:
            raise ErroFinanceiro("Pendência não encontrada ou já encerrada.")
        if acao == "descartar":
            if pend["tipo"].startswith("importacao_"):
                from mcp_engine.modules.financeiro.importers import registrar_descarte

                registrar_descarte(con, pend)
            sessoes.encerrar_pendencia(con, pid, "descartada", acao, quem)
            return {"resolvida": True, "pendencia_id": pid, "acao": acao}
        if acao == "manter_realizada" and pend["tipo"] == "conflito_desfecho":
            sessoes.estornar_ocorrencia(con, int(pend["referencia"].split(":")[1]), quem)
            sessoes.encerrar_pendencia(con, pid, "resolvida", acao, quem)
            return {"resolvida": True, "pendencia_id": pid, "acao": acao}
        if acao == "aceitar" and pend["tipo"].startswith("importacao_"):
            from mcp_engine.modules.financeiro.importers import aplicar_pendencia

            correcao = args.get("correcao") if isinstance(args.get("correcao"), dict) else {}
            res = aplicar_pendencia(con, pend, correcao, quem, hoje=agora)
            sessoes.encerrar_pendencia(con, pid, "resolvida", acao, quem)
            return {"resolvida": True, "pendencia_id": pid, "acao": acao, **res}
        raise ErroFinanceiro("Ação não aplicável a esta pendência.")

    return _executar(ctx, run)


def fechar_mes(ctx: HandlerContext, args: dict[str, Any]) -> dict[str, Any]:
    def run(con: sqlite3.Connection, quem: Quem, agora: date) -> dict[str, Any]:
        mes = parse_mes(_texto(args, "mes", obrigatorio=True, maximo=7))
        unidade = _texto(args, "unidade", maximo=80)
        uid = _id_por_nome(con, "unidade", unidade, "Unidade") if unidade else None
        from mcp_engine.modules.financeiro import integridade

        violacoes = integridade.verificar(con)
        if violacoes:
            raise ErroFinanceiro(
                f"Há {len(violacoes)} violação(ões) de invariante: corrija antes de fechar o mês."
            )
        digest = lancamentos.fechar_mes(con, mes, uid, quem, hoje=agora)
        abertas = con.execute(
            "SELECT COUNT(*) FROM pendencia_operacional WHERE status = 'aberta'"
        ).fetchone()[0]
        return {
            "fechado": True,
            "mes": mes,
            "hash": digest,
            "pendencias_abertas": abertas,
            "aprovado_por": quem.aprovado_por or quem.actor,
            EVENTOS_CHAVE: [_evento("mes.fechado", f"mes:{mes}:{digest[:12]}", {"mes": mes})],
        }

    return _executar(ctx, run)


def reabrir_mes(ctx: HandlerContext, args: dict[str, Any]) -> dict[str, Any]:
    def run(con: sqlite3.Connection, quem: Quem, agora: date) -> dict[str, Any]:
        mes = parse_mes(_texto(args, "mes", obrigatorio=True, maximo=7))
        unidade = _texto(args, "unidade", maximo=80)
        uid = _id_por_nome(con, "unidade", unidade, "Unidade") if unidade else None
        lancamentos.reabrir_mes(con, mes, uid, _texto(args, "motivo", obrigatorio=True), quem)
        return {"reaberto": True, "mes": mes}

    return _executar(ctx, run)


def consultar_financeiro(
    ctx: HandlerContext,
    args: dict[str, Any],
    operacoes: Callable[[], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    gancho.atualizar(caminho_ledger(ctx.data_root, ctx.client_id))

    def run(con: sqlite3.Connection, quem: Quem, agora: date) -> dict[str, Any]:
        filtros = args.get("filtros")
        if filtros is not None and not isinstance(filtros, dict):
            raise ErroFinanceiro("filtros deve ser um objeto.")
        return consultas.consultar(
            con,
            quem,
            _texto(args, "consulta", obrigatorio=True, maximo=40),
            _texto(args, "periodo", maximo=30),
            filtros,
            hoje=agora,
            operacoes=operacoes,
        )

    return _executar(ctx, run, escrita=False)


def listar_pendencias(ctx: HandlerContext, args: dict[str, Any]) -> dict[str, Any]:
    return consultar_financeiro(ctx, {"consulta": "pendencias", "filtros": args.get("filtros")})


def exportar_planilha(ctx: HandlerContext, args: dict[str, Any]) -> dict[str, Any]:
    from mcp_engine.modules.financeiro.importers.exportador import exportar

    def run(con: sqlite3.Connection, quem: Quem, agora: date) -> dict[str, Any]:
        return exportar(
            con,
            quem,
            tipo=_texto(args, "tipo", obrigatorio=True, maximo=20),
            periodo=_texto(args, "periodo", obrigatorio=True, maximo=30),
            unidade=_texto(args, "unidade", maximo=80),
            destino=ctx.data_root / ctx.client_id / "exports",
            client_id=ctx.client_id,
        )

    return _executar(ctx, run, escrita=False)


HANDLERS: dict[str, Callable[[HandlerContext, dict[str, Any]], dict[str, Any]]] = {
    "registrar_recebimento": registrar_recebimento,
    "registrar_despesa": registrar_despesa,
    "estornar_lancamento": estornar_lancamento,
    "anexar_comprovante": anexar_comprovante,
    "atualizar_status_nf": atualizar_status_nf,
    "registrar_falta": registrar_falta,
    "registrar_pacote": registrar_pacote,
    "resolver_pendencia": resolver_pendencia,
    "fechar_mes": fechar_mes,
    "reabrir_mes": reabrir_mes,
    "consultar_financeiro": consultar_financeiro,
    "listar_pendencias": listar_pendencias,
    "exportar_planilha": exportar_planilha,
}
