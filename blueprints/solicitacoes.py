"""
SOLICITAÇÃO DE MATERIAL
Substitui o formulário + planilha "Monitoramento de Solicitação de Materiais".
Fluxo: Solicitado → Em cadastro → Cadastrado → Proc. de Compra → Pedido SAP
       → Recebido → Concluído  (ou Recusado / Cancelado)
"""
import io
import csv

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, session, abort, Response)

import db
from auth import exige, pode

bp = Blueprint("sol", __name__, url_prefix="/solicitacoes")

TIPOS = ["Estoque NLAG", "Cadastro", "Expansão", "Transferência", "HIBE/ERSA", "Compra direta"]

SITUACOES = ["Solicitado", "Em cadastro", "Cadastrado", "Proc. de Compra",
             "Pedido SAP", "Aguardando Cotação", "Pendente Aprovação",
             "Compra Aprovada", "Recebido", "Concluído", "Recusado", "Cancelado"]

CORES = {
    "Solicitado": "secondary", "Em cadastro": "info", "Cadastrado": "primary",
    "Proc. de Compra": "warning", "Pedido SAP": "warning",
    "Aguardando Cotação": "warning", "Pendente Aprovação": "warning",
    "Compra Aprovada": "success", "Recebido": "success",
    "Concluído": "success", "Recusado": "danger", "Cancelado": "dark",
}


@bp.route("/")
def lista():
    situacao = request.args.get("situacao", "abertas")
    busca = request.args.get("q", "").strip()

    where, params = ["1=1"], []
    if situacao == "abertas":
        where.append("s.situacao NOT IN ('Concluído','Cancelado','Recusado')")
    elif situacao and situacao != "todas":
        where.append("s.situacao=%s")
        params.append(situacao)
    if session.get("perfil") == "solicitante":
        where.append("s.solicitante_id=%s")
        params.append(session["uid"])
    if busca:
        where.append("(s.descricao ILIKE %s OR s.codigo ILIKE %s OR CAST(s.numero AS TEXT)=%s)")
        params += [f"%{busca}%", f"%{busca}%", busca]

    itens = db.query(f"""
        SELECT s.*, u.nome AS solicitante, cc.codigo AS cc_codigo, cc.nome AS cc_nome,
               o.numero AS os_numero,
               (CURRENT_DATE - s.criado_em::date) AS dias
        FROM solicitacoes_material s
        LEFT JOIN usuarios u ON u.id=s.solicitante_id
        LEFT JOIN centros_custo cc ON cc.id=s.centro_custo_id
        LEFT JOIN ordens_servico o ON o.id=s.os_id
        WHERE {' AND '.join(where)}
        ORDER BY s.criado_em DESC LIMIT 400""", params)

    resumo = {}
    for s in SITUACOES:
        resumo[s] = db.scalar("SELECT COUNT(*) AS n FROM solicitacoes_material WHERE situacao=%s",
                              (s,), default=0)

    return render_template("sol/lista.html", itens=itens, situacao=situacao, busca=busca,
                           SITUACOES=SITUACOES, CORES=CORES, resumo=resumo)


@bp.route("/nova", methods=["GET", "POST"])
@exige("solicitar_material")
def nova():
    os_id = request.args.get("os_id") or None
    if request.method == "POST":
        descricao = request.form.get("descricao", "").strip()
        if not descricao:
            flash("Informe a descrição / nome do item.", "warning")
            return redirect(url_for("sol.nova"))

        numero = db.proximo_numero("solicitacoes_material")
        sid = db.inserir("""
            INSERT INTO solicitacoes_material
              (numero, solicitante_id, codigo, descricao, link, tipo, quantidade,
               centro_custo_id, observacoes, os_id, dt_solicitacao, situacao)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_DATE,'Solicitado') RETURNING id""",
            (numero, session["uid"], request.form.get("codigo", "").strip().upper() or None,
             descricao, request.form.get("link", "").strip() or None,
             request.form.get("tipo", "Estoque NLAG"),
             float(request.form.get("quantidade") or 1),
             request.form.get("centro_custo_id") or None,
             request.form.get("observacoes", "").strip() or None,
             request.form.get("os_id") or None))

        db.executar("""INSERT INTO solicitacao_historico (solicitacao_id, usuario_id, situacao, comentario)
                       VALUES (%s,%s,'Solicitado','Solicitação criada.')""", (sid, session["uid"]))

        # Vincula à OS e registra apontamento
        vinc_os = request.form.get("os_id")
        if vinc_os:
            db.executar("""INSERT INTO os_apontamentos (os_id, usuario_id, tipo, descricao)
                           VALUES (%s,%s,'material',%s)""",
                        (vinc_os, session["uid"],
                         f"Solicitação de material SM #{numero}: {descricao} "
                         f"({request.form.get('quantidade') or 1})"))
            o = db.um("SELECT numero, solicitante_id FROM ordens_servico WHERE id=%s", (vinc_os,))
            if o:
                db.notificar(o["solicitante_id"], f"OS #{o['numero']} — material solicitado",
                             descricao[:120], url_for("os.detalhe", os_id=vinc_os))

        db.notificar_perfis(("analista", "lider"), f"Nova solicitação de material SM #{numero}",
                            f"{descricao[:100]} — qtd {request.form.get('quantidade') or 1}",
                            url_for("sol.detalhe", sid=sid))
        flash(f"Solicitação SM #{numero} registrada.", "success")
        return redirect(url_for("sol.detalhe", sid=sid))

    centros = db.query("SELECT * FROM centros_custo WHERE ativo=TRUE ORDER BY codigo")
    ordens = db.query("""SELECT id, numero, descricao_problema FROM ordens_servico
                         WHERE status IN ('aberta','em_andamento','pausada','aguardando_peca')
                         ORDER BY numero DESC LIMIT 100""")
    return render_template("sol/nova.html", centros=centros, ordens=ordens, TIPOS=TIPOS,
                           os_id=os_id)


@bp.route("/<int:sid>", methods=["GET", "POST"])
def detalhe(sid):
    s = db.um("""SELECT s.*, u.nome AS solicitante, u.email AS solicitante_email,
                        cc.codigo AS cc_codigo, cc.nome AS cc_nome, o.numero AS os_numero
                 FROM solicitacoes_material s
                 LEFT JOIN usuarios u ON u.id=s.solicitante_id
                 LEFT JOIN centros_custo cc ON cc.id=s.centro_custo_id
                 LEFT JOIN ordens_servico o ON o.id=s.os_id
                 WHERE s.id=%s""", (sid,))
    if not s:
        abort(404)

    if request.method == "POST":
        if not pode("tratar_solicitacao"):
            flash("Seu perfil não pode atualizar solicitações.", "warning")
            return redirect(url_for("sol.detalhe", sid=sid))

        nova_sit = request.form.get("situacao", s["situacao"])
        comentario = request.form.get("comentario", "").strip()
        db.executar("""UPDATE solicitacoes_material
                       SET situacao=%s, num_ficha=%s, id_4mdg=%s, num_pr=%s,
                           codigo_final=%s, tipo_material=%s,
                           dt_cadastro=%s, dt_chegada=%s, atualizado_em=NOW()
                       WHERE id=%s""",
                    (nova_sit,
                     request.form.get("num_ficha", "").strip() or None,
                     request.form.get("id_4mdg", "").strip() or None,
                     request.form.get("num_pr", "").strip() or None,
                     request.form.get("codigo_final", "").strip().upper() or None,
                     request.form.get("tipo_material", "").strip() or None,
                     request.form.get("dt_cadastro") or None,
                     request.form.get("dt_chegada") or None, sid))

        db.executar("""INSERT INTO solicitacao_historico
                       (solicitacao_id, usuario_id, situacao, comentario)
                       VALUES (%s,%s,%s,%s)""", (sid, session["uid"], nova_sit, comentario))

        db.notificar(s["solicitante_id"], f"SM #{s['numero']} — {nova_sit}",
                     comentario or f"Situação atualizada para {nova_sit}.",
                     url_for("sol.detalhe", sid=sid))

        # Ao receber, avisa quem está com a OS aguardando peça
        if nova_sit in ("Recebido", "Concluído") and s["os_id"]:
            o = db.um("SELECT numero, responsavel_id, status FROM ordens_servico WHERE id=%s",
                      (s["os_id"],))
            if o:
                db.executar("""INSERT INTO os_apontamentos (os_id, usuario_id, tipo, descricao)
                               VALUES (%s,%s,'material',%s)""",
                            (s["os_id"], session["uid"],
                             f"Material da SM #{s['numero']} recebido: {s['descricao']}"))
                db.notificar(o["responsavel_id"], f"OS #{o['numero']} — material chegou",
                             f"{s['descricao']} disponível. A OS pode ser retomada.",
                             url_for("os.detalhe", os_id=s["os_id"]))
        flash("Solicitação atualizada.", "success")
        return redirect(url_for("sol.detalhe", sid=sid))

    historico = db.query("""SELECT h.*, u.nome AS usuario FROM solicitacao_historico h
                            LEFT JOIN usuarios u ON u.id=h.usuario_id
                            WHERE h.solicitacao_id=%s ORDER BY h.criado_em""", (sid,))
    return render_template("sol/detalhe.html", s=s, historico=historico,
                           SITUACOES=SITUACOES, CORES=CORES)


@bp.route("/exportar")
@exige("tratar_solicitacao")
def exportar():
    itens = db.query("""SELECT s.numero, s.criado_em, u.nome AS solicitante, s.codigo,
                               s.descricao, s.tipo, s.quantidade, cc.codigo AS cc,
                               s.num_ficha, s.id_4mdg, s.num_pr, s.codigo_final,
                               s.dt_cadastro, s.dt_chegada, s.situacao
                        FROM solicitacoes_material s
                        LEFT JOIN usuarios u ON u.id=s.solicitante_id
                        LEFT JOIN centros_custo cc ON cc.id=s.centro_custo_id
                        ORDER BY s.numero""")
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["SM", "Data", "Solicitante", "Codigo", "Descricao", "Tipo", "Qtd",
                "Centro de Custo", "Ficha/FDS", "ID 4MDG", "PR", "Codigo Final",
                "Dt Cadastro", "Dt Chegada", "Situacao"])
    for s in itens or []:
        w.writerow([s["numero"], s["criado_em"].strftime("%d/%m/%Y") if s["criado_em"] else "",
                    s["solicitante"], s["codigo"] or "", s["descricao"], s["tipo"],
                    f"{float(s['quantidade']):g}", s["cc"] or "", s["num_ficha"] or "",
                    s["id_4mdg"] or "", s["num_pr"] or "", s["codigo_final"] or "",
                    s["dt_cadastro"] or "", s["dt_chegada"] or "", s["situacao"]])
    return Response(buf.getvalue().encode("utf-8-sig"), mimetype="text/csv",
                    headers={"Content-Disposition":
                             "attachment; filename=solicitacoes_material.csv"})
