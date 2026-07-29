from flask import (Blueprint, render_template, request, redirect,
                   url_for, session, flash)
from werkzeug.security import check_password_hash, generate_password_hash

import db

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip().lower()
        senha = request.form.get("senha", "")
        u = db.um("SELECT * FROM usuarios WHERE lower(usuario)=%s AND ativo=TRUE", (usuario,))
        if u and check_password_hash(u["senha_hash"], senha):
            session.permanent = True
            session["uid"] = u["id"]
            session["usuario"] = u["usuario"]
            session["nome"] = u["nome"]
            session["perfil"] = u["perfil"]
            session["email"] = u["email"]
            db.executar("UPDATE usuarios SET ultimo_acesso=NOW() WHERE id=%s", (u["id"],))
            db.registrar_log(u["id"], u["usuario"], "login")
            destino = request.args.get("next") or url_for("home.index")
            return redirect(destino)
        flash("Usuário ou senha inválidos.", "danger")
    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/meu-perfil", methods=["GET", "POST"])
def meu_perfil():
    if "uid" not in session:
        return redirect(url_for("auth.login"))
    uid = session["uid"]
    if request.method == "POST":
        acao = request.form.get("acao")
        if acao == "dados":
            nome = request.form.get("nome", "").strip()
            email = request.form.get("email", "").strip()
            telefone = request.form.get("telefone", "").strip()
            db.executar("UPDATE usuarios SET nome=%s, email=%s, telefone=%s WHERE id=%s",
                        (nome, email, telefone, uid))
            session["nome"] = nome
            session["email"] = email
            flash("Dados atualizados.", "success")
        elif acao == "senha":
            atual = request.form.get("senha_atual", "")
            nova = request.form.get("senha_nova", "")
            conf = request.form.get("senha_conf", "")
            u = db.um("SELECT senha_hash FROM usuarios WHERE id=%s", (uid,))
            if not check_password_hash(u["senha_hash"], atual):
                flash("Senha atual incorreta.", "danger")
            elif len(nova) < 6:
                flash("A nova senha precisa ter ao menos 6 caracteres.", "warning")
            elif nova != conf:
                flash("A confirmação não confere.", "warning")
            else:
                db.executar("UPDATE usuarios SET senha_hash=%s WHERE id=%s",
                            (generate_password_hash(nova), uid))
                flash("Senha alterada com sucesso.", "success")
        return redirect(url_for("auth.meu_perfil"))

    usuario = db.um("SELECT * FROM usuarios WHERE id=%s", (uid,))
    return render_template("meu_perfil.html", usuario=usuario)


@bp.route("/notificacoes")
def notificacoes():
    if "uid" not in session:
        return redirect(url_for("auth.login"))
    itens = db.query("""SELECT * FROM notificacoes WHERE usuario_id=%s
                        ORDER BY criado_em DESC LIMIT 100""", (session["uid"],))
    db.executar("UPDATE notificacoes SET lida=TRUE WHERE usuario_id=%s AND lida=FALSE",
                (session["uid"],))
    return render_template("notificacoes.html", itens=itens)
