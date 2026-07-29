"""API interna para os formulários dinâmicos (JS)."""
from flask import Blueprint, jsonify, request

import db

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/equipamentos")
def equipamentos():
    ct = request.args.get("centro_trabalho_id")
    est = request.args.get("estabelecimento_id")
    where, params = ["ativo=TRUE"], []
    if ct:
        where.append("centro_trabalho_id=%s")
        params.append(ct)
    if est:
        where.append("estabelecimento_id=%s")
        params.append(est)
    itens = db.query(f"""SELECT id, codigo, nome, criticidade, tipo, status
                         FROM equipamentos WHERE {' AND '.join(where)} ORDER BY codigo""", params)
    return jsonify([dict(i) for i in (itens or [])])


@bp.route("/materiais/busca")
def busca_material():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    itens = db.query("""SELECT codigo, descricao, unidade, tipo FROM materiais
                        WHERE ativo=TRUE AND (codigo ILIKE %s OR descricao ILIKE %s)
                        ORDER BY codigo LIMIT 20""", (f"%{q}%", f"%{q}%"))
    saida = []
    for m in itens or []:
        d = dict(m)
        d["saldo"] = db.saldo_material(m["codigo"]) if m["tipo"] == "NLAG" else None
        saida.append(d)
    return jsonify(saida)


@bp.route("/causas")
def causas():
    defeito = request.args.get("defeito_id")
    if defeito:
        itens = db.query("""SELECT id, nome FROM causas WHERE ativo=TRUE
                            AND (defeito_id=%s OR defeito_id IS NULL) ORDER BY nome""", (defeito,))
    else:
        itens = db.query("SELECT id, nome FROM causas WHERE ativo=TRUE ORDER BY nome")
    return jsonify([dict(i) for i in (itens or [])])


@bp.route("/os/<int:os_id>/cronometro")
def cronometro(os_id):
    aberto = db.um("""SELECT tipo, EXTRACT(EPOCH FROM (NOW()-inicio))::INT AS seg
                      FROM os_tempos WHERE os_id=%s AND fim IS NULL
                      ORDER BY inicio DESC LIMIT 1""", (os_id,))
    total = db.scalar("SELECT tempo_trabalho_seg AS t FROM ordens_servico WHERE id=%s",
                      (os_id,), default=0)
    if not aberto:
        return jsonify(rodando=False, total=total)
    return jsonify(rodando=True, tipo=aberto["tipo"], parcial=aberto["seg"], total=total)


@bp.route("/notificacoes/nao-lidas")
def nao_lidas():
    from flask import session
    if "uid" not in session:
        return jsonify(n=0)
    n = db.scalar("SELECT COUNT(*) AS n FROM notificacoes WHERE usuario_id=%s AND lida=FALSE",
                  (session["uid"],), default=0)
    return jsonify(n=n)
