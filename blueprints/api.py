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
    """Catálogo para o manutentor escolher a peça dentro da OS."""
    q = request.args.get("q", "").strip()
    so_com_saldo = request.args.get("com_saldo") == "1"

    where, params = ["m.ativo=TRUE"], []
    if q:
        where.append("(m.codigo ILIKE %s OR m.descricao ILIKE %s)")
        params += [f"%{q}%", f"%{q}%"]

    # O filtro de saldo precisa ser aplicado antes do limite, senão itens
    # com saldo ficam de fora só por estarem mais adiante no alfabeto.
    having = ""
    if so_com_saldo:
        having = """HAVING (CASE WHEN m.tipo='NLAG' THEN
                     COALESCE(SUM(CASE WHEN mv.tipo IN ('ENTRADA','AJUSTE') THEN mv.quantidade
                                       ELSE -mv.quantidade END),0)
                   ELSE COALESCE(m.saldo_sap,0) END) > 0"""

    itens = db.query(f"""
        SELECT m.codigo, m.descricao, m.unidade, m.tipo, m.localizacao,
               m.estoque_min, m.saldo_sap, (m.imagem IS NOT NULL) AS tem_foto,
          COALESCE(SUM(CASE WHEN mv.tipo IN ('ENTRADA','AJUSTE') THEN mv.quantidade
                            ELSE -mv.quantidade END),0) AS saldo_nlag
        FROM materiais m
        LEFT JOIN movimentacoes mv ON mv.codigo = m.codigo
        WHERE {' AND '.join(where)}
        GROUP BY m.id
        {having}
        ORDER BY (CASE WHEN m.tipo='NLAG' THEN 0 ELSE 1 END), m.descricao
        LIMIT 80""", params)

    saida = []
    for m in itens or []:
        saldo = (float(m["saldo_nlag"]) if m["tipo"] == "NLAG"
                 else float(m["saldo_sap"] or 0))
        saida.append({
            "codigo": m["codigo"], "descricao": m["descricao"],
            "unidade": m["unidade"], "tipo": m["tipo"], "saldo": saldo,
            "localizacao": m["localizacao"] or "", "tem_foto": m["tem_foto"],
            "minimo": float(m["estoque_min"] or 0),
        })
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
    """
    Estado do cronômetro. O 'acumulado' é o tempo de trabalho já fechado mais o
    intervalo em curso, para o relógio da tela nunca voltar a zero ao retomar.
    """
    aberto = db.um("""SELECT tipo, EXTRACT(EPOCH FROM (NOW()-inicio))::INT AS seg
                      FROM os_tempos WHERE os_id=%s AND fim IS NULL
                      ORDER BY inicio DESC LIMIT 1""", (os_id,))
    total = int(db.scalar("SELECT tempo_trabalho_seg AS t FROM ordens_servico WHERE id=%s",
                          (os_id,), default=0) or 0)
    if not aberto:
        return jsonify(rodando=False, tipo=None, parcial=0,
                       total=total, acumulado=total)

    trabalhando = aberto["tipo"] == "trabalho"
    parcial = int(aberto["seg"] or 0)
    return jsonify(rodando=True, tipo=aberto["tipo"], parcial=parcial, total=total,
                   acumulado=total + (parcial if trabalhando else 0),
                   trabalhando=trabalhando)


@bp.route("/notificacoes/nao-lidas")
def nao_lidas():
    from flask import session
    if "uid" not in session:
        return jsonify(n=0)
    n = db.scalar("SELECT COUNT(*) AS n FROM notificacoes WHERE usuario_id=%s AND lida=FALSE",
                  (session["uid"],), default=0)
    return jsonify(n=n)
