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

import psycopg2

import db
import mailer
from auth import exige, pode

bp = Blueprint("sol", __name__, url_prefix="/solicitacoes")

TIPOS = ["Estoque NLAG", "Cadastro", "Expansão", "Transferência", "HIBE/ERSA", "Compra direta"]

SITUACOES = ["Solicitado", "Em cadastro", "Cadastrado", "Proc. de Compra",
             "Pedido SAP", "Aguardando Cotação", "Pendente Aprovação",
             "Compra Aprovada", "Recebido", "Liberado", "Concluído",
             "Recusado", "Cancelado"]

# Situações em que a peça já está na mão do manutentor — a OS destrava
ATENDIDAS = ("Liberado", "Concluído", "Recusado", "Cancelado")

CORES = {
    "Solicitado": "secondary", "Em cadastro": "info", "Cadastrado": "primary",
    "Proc. de Compra": "warning", "Pedido SAP": "warning",
    "Aguardando Cotação": "warning", "Pendente Aprovação": "warning",
    "Compra Aprovada": "success", "Recebido": "success", "Liberado": "success",
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
                         WHERE status IN ('aberta','atribuida','em_andamento','pausada','aguardando_peca')
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
                link = url_for("os.detalhe", os_id=s["os_id"])
                db.notificar(o["responsavel_id"], f"OS #{o['numero']} — material chegou",
                             f"{s['descricao']} disponível. A OS pode ser retomada.", link)
                mailer.avisar(
                    "material_recebido",
                    mailer.emails_dos_usuarios([o["responsavel_id"], s["solicitante_id"]]),
                    assunto=f"[Manutenção] Material da SM #{s['numero']} chegou — OS #{o['numero']}",
                    titulo=f"Material disponível — SM #{s['numero']}",
                    subtitulo=f"A OS #{o['numero']} pode ser retomada",
                    mensagem=comentario or "O material solicitado já está disponível.",
                    itens=[("Item", s["descricao"]),
                           ("Código", s["codigo_final"] or s["codigo"] or "—"),
                           ("Quantidade", f"{float(s['quantidade']):g}"),
                           ("Situação", nova_sit),
                           ("Liberado por", session["nome"])],
                    botao=("Retomar a OS", mailer.url(link) + "#cronometro"))
        flash("Solicitação atualizada.", "success")
        return redirect(url_for("sol.detalhe", sid=sid))

    historico = db.query("""SELECT h.*, u.nome AS usuario FROM solicitacao_historico h
                            LEFT JOIN usuarios u ON u.id=h.usuario_id
                            WHERE h.solicitacao_id=%s ORDER BY h.criado_em""", (sid,))
    mat = _material_da_solicitacao(s)
    # Decimal do banco e float do saldo não se misturam — normaliza aqui
    qtd = float(s["quantidade"] or 0)
    saldo = db.saldo_material(mat["codigo"]) if mat and mat["tipo"] == "NLAG" else None
    falta = round(max(qtd - saldo, 0), 3) if saldo is not None else 0
    return render_template("sol/detalhe.html", s=s, historico=historico,
                           SITUACOES=SITUACOES, CORES=CORES, mat=mat, saldo=saldo,
                           qtd=qtd, falta=falta,
                           pendentes=_pendentes_da_os(s["os_id"]),
                           ATENDIDAS=ATENDIDAS)


# ══════════════════════════════════════════════════════════════════
#  ATENDIMENTO DO ANALISTA
# ══════════════════════════════════════════════════════════════════
def _material_da_solicitacao(s):
    """O material já cadastrado, se houver — pelo código final ou pelo informado."""
    for cod in (s.get("codigo_final"), s.get("codigo")):
        if cod:
            m = db.um("SELECT * FROM materiais WHERE codigo=%s", (cod.strip().upper(),))
            if m:
                return m
    return None


@bp.route("/<int:sid>/liberar", methods=["POST"])
@exige("tratar_solicitacao")
def liberar(sid):
    """Um clique: entrega a peça ao manutentor e dá baixa no NLAG."""
    s = db.um("""SELECT sm.*, u.nome AS solicitante, o.numero AS os_numero
                 FROM solicitacoes_material sm
                 LEFT JOIN usuarios u ON u.id=sm.solicitante_id
                 LEFT JOIN ordens_servico o ON o.id=sm.os_id
                 WHERE sm.id=%s""", (sid,))
    if not s:
        abort(404)

    mat = _material_da_solicitacao(s)
    if not mat:
        flash("Esta peça ainda não tem cadastro. Cadastre antes de liberar.", "warning")
        return redirect(url_for("sol.detalhe", sid=sid))

    try:
        qtd = float(request.form.get("quantidade") or s["quantidade"])
    except ValueError:
        qtd = float(s["quantidade"])
    if qtd <= 0:
        flash("Informe a quantidade liberada.", "warning")
        return redirect(url_for("sol.detalhe", sid=sid))

    # Entrada prévia, quando o analista está recebendo a compra agora
    try:
        entrada = float(request.form.get("entrada") or 0)
    except ValueError:
        entrada = 0
    if entrada > 0:
        db.executar("""INSERT INTO movimentacoes (codigo, tipo, quantidade, usuario, observacao)
                       VALUES (%s,'ENTRADA',%s,%s,%s)""",
                    (mat["codigo"], entrada, session["nome"],
                     f"Recebimento para a SM #{s['numero']}"))

    saldo = db.saldo_material(mat["codigo"]) if mat["tipo"] == "NLAG" else None
    if mat["tipo"] == "NLAG":
        if saldo < qtd:
            flash(f"Saldo insuficiente de {mat['codigo']}: há {saldo:g} "
                  f"{mat['unidade']} e a liberação é de {qtd:g}. "
                  "Registre a entrada antes de liberar.", "danger")
            return redirect(url_for("sol.detalhe", sid=sid))
        db.executar("""INSERT INTO movimentacoes
                       (codigo, tipo, quantidade, usuario, observacao, os_id)
                       VALUES (%s,'SAIDA',%s,%s,%s,%s)""",
                    (mat["codigo"], qtd, session["nome"],
                     f"Liberado na SM #{s['numero']}", s["os_id"]))
        saldo = db.saldo_material(mat["codigo"])

    db.executar("""UPDATE solicitacoes_material
                   SET situacao='Liberado', codigo_final=COALESCE(codigo_final,%s),
                       dt_chegada=COALESCE(dt_chegada, CURRENT_DATE), atualizado_em=NOW()
                   WHERE id=%s""", (mat["codigo"], sid))
    db.executar("""INSERT INTO solicitacao_historico
                   (solicitacao_id, usuario_id, situacao, comentario)
                   VALUES (%s,%s,'Liberado',%s)""",
                (sid, session["uid"],
                 f"Material liberado pelo analista: {qtd:g} {mat['unidade']} de "
                 f"{mat['descricao']} ({mat['codigo']})."))

    # Registra na OS e no consumo
    if s["os_id"]:
        db.executar("""INSERT INTO os_materiais
                       (os_id, material_id, codigo, descricao, quantidade, origem,
                        valor_unit, usuario_id, baixado)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (s["os_id"], mat["id"], mat["codigo"], mat["descricao"], qtd,
                     mat["tipo"], mat["valor_unit"] or 0, session["uid"],
                     mat["tipo"] == "NLAG"))
        db.executar("""INSERT INTO os_apontamentos (os_id, usuario_id, tipo, descricao)
                       VALUES (%s,%s,'material',%s)""",
                    (s["os_id"], session["uid"],
                     f"Material liberado pelo analista: {qtd:g} {mat['unidade']} de "
                     f"{mat['descricao']} ({mat['codigo']}) — SM #{s['numero']}."))

    _avisar_liberacao(s, mat, qtd)
    resta = _pendentes_da_os(s["os_id"]) if s["os_id"] else 0
    msg = f"Material liberado para {s['solicitante']}."
    if mat["tipo"] == "NLAG":
        msg += f" Saldo restante de {mat['codigo']}: {saldo:g} {mat['unidade']}."
    if s["os_id"]:
        msg += (" Todos os materiais da OS foram atendidos." if resta == 0
                else f" Ainda restam {resta} pedido(s) nesta OS.")
    flash(msg, "success")
    return redirect(url_for("sol.detalhe", sid=sid))


@bp.route("/<int:sid>/cadastrar", methods=["POST"])
@exige("material_cad")
def cadastrar(sid):
    """Cadastra a peça que ainda não existe e já deixa pronta para liberar."""
    s = db.um("SELECT * FROM solicitacoes_material WHERE id=%s", (sid,))
    if not s:
        abort(404)

    codigo = (request.form.get("codigo") or "").strip().upper()
    descricao = (request.form.get("descricao") or "").strip()
    if not codigo or not descricao:
        flash("Informe o código e a descrição do material.", "warning")
        return redirect(url_for("sol.detalhe", sid=sid))

    if db.um("SELECT id FROM materiais WHERE codigo=%s", (codigo,)):
        db.executar("UPDATE materiais SET ativo=TRUE WHERE codigo=%s", (codigo,))
        flash(f"O código {codigo} já existia e foi reativado.", "info")
    else:
        imagem = None
        f = request.files.get("imagem")
        if f and f.filename:
            from blueprints.materiais import _processar_imagem
            imagem = _processar_imagem(f)
        db.executar("""INSERT INTO materiais
                       (codigo, descricao, unidade, tipo, aplicacao, estoque_min,
                        estoque_max, valor_unit, localizacao, critico, imagem)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (codigo, descricao, request.form.get("unidade", "UNI"),
                     request.form.get("tipo", "NLAG"),
                     request.form.get("aplicacao", "").strip() or None,
                     request.form.get("estoque_min") or 0,
                     request.form.get("estoque_max") or 0,
                     request.form.get("valor_unit") or 0,
                     request.form.get("localizacao", "").strip() or None,
                     request.form.get("critico") == "1",
                     psycopg2.Binary(imagem) if imagem else None))
        flash(f"Material {codigo} cadastrado.", "success")

    db.executar("""UPDATE solicitacoes_material
                   SET codigo_final=%s, situacao='Cadastrado',
                       dt_cadastro=COALESCE(dt_cadastro, CURRENT_DATE), atualizado_em=NOW()
                   WHERE id=%s""", (codigo, sid))
    db.executar("""INSERT INTO solicitacao_historico
                   (solicitacao_id, usuario_id, situacao, comentario)
                   VALUES (%s,%s,'Cadastrado',%s)""",
                (sid, session["uid"],
                 f"Material cadastrado pelo analista com o código {codigo}."))
    db.notificar(s["solicitante_id"], f"SM #{s['numero']} — material cadastrado",
                 f"{descricao} agora tem o código {codigo}.",
                 url_for("sol.detalhe", sid=sid))
    return redirect(url_for("sol.detalhe", sid=sid))


def _pendentes_da_os(os_id):
    """Quantos pedidos daquela OS ainda não foram atendidos pelo analista."""
    if not os_id:
        return 0
    return db.scalar("""SELECT COUNT(*) AS n FROM solicitacoes_material
                        WHERE os_id=%s AND situacao <> ALL(%s)""",
                     (os_id, list(ATENDIDAS)), default=0)


def _avisar_liberacao(s, mat, qtd):
    """Notifica o manutentor de que a peça está disponível."""
    link = url_for("os.detalhe", os_id=s["os_id"]) if s["os_id"] else \
        url_for("sol.detalhe", sid=s["id"])
    titulo = (f"Material liberado — OS #{s['os_numero']}" if s.get("os_numero")
              else f"Material liberado — SM #{s['numero']}")
    db.notificar(s["solicitante_id"], titulo,
                 f"{qtd:g} {mat['unidade']} de {mat['descricao']} já pode ser retirado.",
                 link)
    mailer.avisar(
        "material_recebido", mailer.emails_dos_usuarios([s["solicitante_id"]]),
        assunto=f"[Manutenção] {titulo}",
        titulo="Material liberado pelo analista",
        subtitulo=f"SM #{s['numero']}"
                  + (f" · OS #{s['os_numero']}" if s.get("os_numero") else ""),
        mensagem="A peça já está disponível para retirada no almoxarifado.",
        itens=[("Item", mat["descricao"]),
               ("Código", mat["codigo"]),
               ("Quantidade", f"{qtd:g} {mat['unidade']}"),
               ("Liberado por", session["nome"]),
               ("Data", db.agora().strftime("%d/%m/%Y às %H:%M"))],
        botao=("Retomar a OS", mailer.url(link) + "#cronometro"))


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
