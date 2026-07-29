"""
SUB-MÓDULO — RONDAS DIÁRIAS DE INSPEÇÃO
Check list diário de pontos de verificação (água, lubrificação da monovia,
compressor, secador, purgadores...). Registro com fotos e geração automática
de OS de corretiva planejada quando algo estiver fora do padrão.
"""
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, session, abort, Response)
import psycopg2

import db
from auth import exige

bp = Blueprint("rondas", __name__, url_prefix="/rondas")


@bp.route("/")
@exige("ronda_exec")
def lista():
    rondas = db.query("""SELECT r.*,
        (SELECT COUNT(*) FROM ronda_pontos p WHERE p.ronda_id=r.id AND p.ativo=TRUE) AS n_pontos,
        (SELECT id FROM ronda_execucoes e WHERE e.ronda_id=r.id AND e.data=CURRENT_DATE
          ORDER BY id DESC LIMIT 1) AS exec_hoje
        FROM rondas r WHERE r.ativo=TRUE ORDER BY r.nome""")
    historico = db.query("""SELECT e.*, r.nome AS ronda, u.nome AS usuario,
        (SELECT COUNT(*) FROM ronda_respostas rr WHERE rr.execucao_id=e.id AND rr.resposta='NOK') AS noks
        FROM ronda_execucoes e
        JOIN rondas r ON r.id=e.ronda_id
        LEFT JOIN usuarios u ON u.id=e.usuario_id
        ORDER BY e.data DESC, e.id DESC LIMIT 60""")
    return render_template("rondas/lista.html", rondas=rondas, historico=historico)


@bp.route("/<int:ronda_id>/iniciar", methods=["POST"])
@exige("ronda_exec")
def iniciar(ronda_id):
    existente = db.um("""SELECT id FROM ronda_execucoes
                         WHERE ronda_id=%s AND data=CURRENT_DATE AND status='em_andamento'
                         ORDER BY id DESC LIMIT 1""", (ronda_id,))
    if existente:
        return redirect(url_for("rondas.executar", exec_id=existente["id"]))
    eid = db.inserir("""INSERT INTO ronda_execucoes (ronda_id, usuario_id, data, status)
                        VALUES (%s,%s,CURRENT_DATE,'em_andamento') RETURNING id""",
                     (ronda_id, session["uid"]))
    return redirect(url_for("rondas.executar", exec_id=eid))


@bp.route("/exec/<int:exec_id>", methods=["GET", "POST"])
@exige("ronda_exec")
def executar(exec_id):
    e = db.um("""SELECT e.*, r.nome AS ronda FROM ronda_execucoes e
                 JOIN rondas r ON r.id=e.ronda_id WHERE e.id=%s""", (exec_id,))
    if not e:
        abort(404)

    if request.method == "POST":
        concluir = request.form.get("acao") == "concluir"
        for chave, valor in request.form.items():
            if not chave.startswith("ponto_"):
                continue
            ponto_id = int(chave.split("_")[1])
            obs = request.form.get(f"obs_{ponto_id}", "").strip()
            foto = request.files.get(f"foto_{ponto_id}")
            dados = foto.read() if foto and foto.filename else None

            existente = db.um("SELECT id FROM ronda_respostas WHERE execucao_id=%s AND ponto_id=%s",
                              (exec_id, ponto_id))
            if existente:
                if dados:
                    db.executar("""UPDATE ronda_respostas SET resposta=%s, observacao=%s, foto=%s
                                   WHERE id=%s""",
                                (valor, obs, psycopg2.Binary(dados), existente["id"]))
                else:
                    db.executar("UPDATE ronda_respostas SET resposta=%s, observacao=%s WHERE id=%s",
                                (valor, obs, existente["id"]))
            else:
                db.executar("""INSERT INTO ronda_respostas
                               (execucao_id, ponto_id, resposta, observacao, foto)
                               VALUES (%s,%s,%s,%s,%s)""",
                            (exec_id, ponto_id, valor, obs,
                             psycopg2.Binary(dados) if dados else None))

        db.executar("UPDATE ronda_execucoes SET observacoes=%s WHERE id=%s",
                    (request.form.get("observacoes", "").strip(), exec_id))

        if concluir:
            n = _gerar_os_nok(exec_id, e)
            db.executar("""UPDATE ronda_execucoes SET status='concluida', concluido_em=NOW()
                           WHERE id=%s""", (exec_id,))
            msg = "Ronda concluída."
            if n:
                msg += f" {n} OS de corretiva planejada gerada(s)."
            flash(msg, "success")
            return redirect(url_for("rondas.lista"))

        flash("Ronda salva.", "success")
        return redirect(url_for("rondas.executar", exec_id=exec_id))

    pontos = db.query("""SELECT p.*, rr.resposta, rr.observacao, rr.os_gerada,
                                (rr.foto IS NOT NULL) AS tem_foto, rr.id AS resp_id
                         FROM ronda_pontos p
                         LEFT JOIN ronda_respostas rr ON rr.ponto_id=p.id AND rr.execucao_id=%s
                         WHERE p.ronda_id=%s AND p.ativo=TRUE ORDER BY p.ordem""",
                      (exec_id, e["ronda_id"]))
    return render_template("rondas/executar.html", e=e, pontos=pontos)


def _gerar_os_nok(exec_id, e):
    noks = db.query("""SELECT rr.*, p.descricao, p.equipamento_id
                       FROM ronda_respostas rr
                       JOIN ronda_pontos p ON p.id=rr.ponto_id
                       WHERE rr.execucao_id=%s AND rr.resposta='NOK' AND rr.os_gerada IS NULL""",
                    (exec_id,))
    n = 0
    for r in noks or []:
        crit = "C"
        if r["equipamento_id"]:
            crit = db.scalar("SELECT criticidade FROM equipamentos WHERE id=%s",
                             (r["equipamento_id"],), default="C")
        numero = db.proximo_numero("ordens_servico")
        desc = (f"[Ronda diária — {e['ronda']}] {r['descricao']}"
                + (f" — Obs.: {r['observacao']}" if r["observacao"] else ""))
        os_id = db.inserir("""INSERT INTO ordens_servico
              (numero, tipo_manutencao, equipamento_id, descricao_problema, solicitante_id,
               criticidade, status, origem, origem_id)
              VALUES (%s,'planejada',%s,%s,%s,%s,'aberta','ronda',%s) RETURNING id""",
            (numero, r["equipamento_id"], desc, session["uid"], crit, exec_id))
        db.executar("""INSERT INTO os_apontamentos (os_id, usuario_id, tipo, descricao)
                       VALUES (%s,%s,'abertura','OS gerada automaticamente pela ronda diária.')""",
                    (os_id, session["uid"]))
        # copia a foto do ponto para a OS
        if r["foto"]:
            db.executar("""INSERT INTO os_anexos (os_id, nome, mime, dados, usuario_id)
                           VALUES (%s,'ronda.jpg','image/jpeg',%s,%s)""",
                        (os_id, r["foto"], session["uid"]))
        db.executar("UPDATE ronda_respostas SET os_gerada=%s WHERE id=%s", (os_id, r["id"]))
        n += 1
    return n


@bp.route("/foto/<int:resp_id>")
def foto(resp_id):
    r = db.um("SELECT foto FROM ronda_respostas WHERE id=%s", (resp_id,))
    if not r or not r["foto"]:
        abort(404)
    return Response(bytes(r["foto"]), mimetype="image/jpeg")


# ── Cadastro de rondas e pontos ────────────────────────────────────
@bp.route("/cadastro", methods=["GET", "POST"])
@exige("preventiva_cad")
def cadastro():
    if request.method == "POST":
        acao = request.form.get("acao")
        if acao == "nova_ronda":
            db.executar("INSERT INTO rondas (nome, turno) VALUES (%s,%s)",
                        (request.form["nome"].strip(), request.form.get("turno", "").strip()))
        elif acao == "novo_ponto":
            rid = request.form["ronda_id"]
            ordem = db.scalar("SELECT COALESCE(MAX(ordem),0)+1 AS n FROM ronda_pontos "
                              "WHERE ronda_id=%s", (rid,), default=1)
            db.executar("""INSERT INTO ronda_pontos (ronda_id, ordem, descricao, equipamento_id)
                           VALUES (%s,%s,%s,%s)""",
                        (rid, ordem, request.form["descricao"].strip(),
                         request.form.get("equipamento_id") or None))
        elif acao == "del_ponto":
            db.executar("UPDATE ronda_pontos SET ativo=FALSE WHERE id=%s",
                        (request.form["ponto_id"],))
        flash("Cadastro atualizado.", "success")
        return redirect(url_for("rondas.cadastro"))

    rondas = db.query("SELECT * FROM rondas ORDER BY nome")
    pontos = db.query("""SELECT p.*, e.codigo AS eq_codigo FROM ronda_pontos p
                         LEFT JOIN equipamentos e ON e.id=p.equipamento_id
                         WHERE p.ativo=TRUE ORDER BY p.ronda_id, p.ordem""")
    equipamentos = db.query("SELECT id, codigo, nome FROM equipamentos WHERE ativo=TRUE ORDER BY codigo")
    return render_template("rondas/cadastro.html", rondas=rondas, pontos=pontos,
                           equipamentos=equipamentos)
