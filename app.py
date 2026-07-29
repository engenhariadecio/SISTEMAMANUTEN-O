"""
SISTEMA CENTRALIZADO DE MANUTENÇÃO — DÉCIO METALÚRGICA
Aplicação Flask principal.
"""
import os
import secrets
from datetime import timedelta

from flask import Flask, session, redirect, url_for, request, g

import db
from auth import usuario_atual, pode, PERFIS


def criar_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    app.permanent_session_lifetime = timedelta(days=7)
    app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB por upload

    # ── Blueprints ──
    from blueprints.auth_bp import bp as auth_bp
    from blueprints.home import bp as home_bp
    from blueprints.corretivas import bp as corretivas_bp
    from blueprints.preventivas import bp as preventivas_bp
    from blueprints.rondas import bp as rondas_bp
    from blueprints.materiais import bp as materiais_bp
    from blueprints.solicitacoes import bp as solicitacoes_bp
    from blueprints.indicadores import bp as indicadores_bp
    from blueprints.relatorios import bp as relatorios_bp
    from blueprints.admin import bp as admin_bp
    from blueprints.api import bp as api_bp

    for b in (auth_bp, home_bp, corretivas_bp, preventivas_bp, rondas_bp,
              materiais_bp, solicitacoes_bp, indicadores_bp, relatorios_bp,
              admin_bp, api_bp):
        app.register_blueprint(b)

    # ── Guard global de login ──
    LIVRES = {"auth.login", "static", "auth.logout"}

    @app.before_request
    def _exigir_login():
        if request.endpoint in LIVRES or request.endpoint is None:
            return None
        if "uid" not in session:
            return redirect(url_for("auth.login", next=request.path))
        return None

    # ── Variáveis globais nos templates ──
    @app.context_processor
    def _globais():
        u = usuario_atual()
        nao_lidas = 0
        if u:
            try:
                nao_lidas = db.scalar(
                    "SELECT COUNT(*) AS n FROM notificacoes WHERE usuario_id=%s AND lida=FALSE",
                    (u["id"],), default=0)
            except Exception:
                nao_lidas = 0
        try:
            crits = db.mapa_criticidade()
        except Exception:
            crits = {}
        return {
            "u": u,
            "pode": pode,
            "PERFIS": PERFIS,
            "nao_lidas": nao_lidas,
            "empresa": "Décio Metalúrgica",
            "CRIT": crits,
            "CRIT_LISTA": sorted(crits.values(), key=lambda c: c["ordem"]),
        }

    # ── Filtros Jinja ──
    @app.template_filter("dt")
    def _dt(valor, fmt="%d/%m/%Y %H:%M"):
        if not valor:
            return "—"
        try:
            return valor.astimezone(db.TZ_BR).strftime(fmt)
        except Exception:
            try:
                return valor.strftime(fmt)
            except Exception:
                return str(valor)[:16]

    @app.template_filter("data")
    def _data(valor):
        return _dt(valor, "%d/%m/%Y")

    @app.template_filter("dur")
    def _dur(segundos):
        """Segundos → 2h 35min"""
        if not segundos:
            return "0min"
        s = int(segundos)
        d, s = divmod(s, 86400)
        h, s = divmod(s, 3600)
        m = s // 60
        partes = []
        if d:
            partes.append(f"{d}d")
        if h:
            partes.append(f"{h}h")
        if m or not partes:
            partes.append(f"{m}min")
        return " ".join(partes)

    @app.template_filter("num")
    def _num(valor, casas=2):
        try:
            return f"{float(valor):,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except Exception:
            return valor

    @app.template_filter("moeda")
    def _moeda(valor):
        return "R$ " + _num(valor or 0)

    # ── Tratamento de erros ──
    @app.errorhandler(404)
    def _404(e):
        from flask import render_template
        return render_template("erro.html", codigo=404,
                               msg="Página não encontrada."), 404

    @app.errorhandler(500)
    def _500(e):
        from flask import render_template
        return render_template("erro.html", codigo=500,
                               msg="Erro interno. Tente novamente ou avise o administrador."), 500

    @app.errorhandler(413)
    def _413(e):
        from flask import flash, redirect, request
        flash("Arquivo muito grande (máx. 25 MB).", "danger")
        return redirect(request.referrer or url_for("home.index")), 302

    return app


app = criar_app()

# Cria/atualiza o schema no start (gunicorn --preload roda uma vez)
try:
    if db.DATABASE_URL:
        db.init_db()
except Exception as e:  # não derruba o app se o banco ainda não subiu
    print(f"[init_db] {e}", flush=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
