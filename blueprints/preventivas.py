"""
MÓDULO — MANUTENÇÕES PREVENTIVAS
Planos e checklists centralizados, grade de 52 semanas, geração e execução
de Ordens de Manutenção (OM), reprogramações e geração automática de OS
a partir de pendências (itens NOK).
"""
import io
import csv
from datetime import date, timedelta

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, session, abort, Response)
import psycopg2

import db
from auth import exige

bp = Blueprint("prev", __name__, url_prefix="/preventivas")

PERIODICIDADES = {
    "SEM": ("Semanal", 1),
    "MEN": ("Mensal", 4),
    "BIM": ("Bimestral", 8),
    "TRI": ("Trimestral", 13),
    "QUA": ("Quadrimestral", 17),
    "SES": ("Semestral", 26),
    "ANU": ("Anual", 52),
    "BIE": ("Bienal", 104),
    "TRIE": ("Trienal", 156),
}


def semana_atual():
    a, s, _ = db.hoje().isocalendar()
    return a, s


def data_da_semana(ano, semana):
    """Segunda-feira da semana ISO."""
    try:
        return date.fromisocalendar(ano, semana, 1)
    except ValueError:
        return date(ano, 12, 28)


# ══════════════════════════════════════════════════════════════════
#  GRADE 52 SEMANAS
# ══════════════════════════════════════════════════════════════════
@bp.route("/")
def grade():
    ano = int(request.args.get("ano") or db.hoje().year)
    _, sem_atual = semana_atual()

    planos = db.query("""
        SELECT p.*, e.codigo AS eq_codigo, e.nome AS eq_nome, e.criticidade,
               est.nome AS estabelecimento, u.nome AS responsavel
        FROM planos_preventiva p
        JOIN equipamentos e ON e.id=p.equipamento_id
        LEFT JOIN estabelecimentos est ON est.id=e.estabelecimento_id
        LEFT JOIN usuarios u ON u.id=p.responsavel_id
        WHERE p.ativo=TRUE ORDER BY e.codigo""")

    prog = db.query("""SELECT id, plano_id, semana, periodicidade, status, om_id
                       FROM programacao WHERE ano=%s""", (ano,))
    grade_map = {}
    for p in prog or []:
        grade_map.setdefault(p["plano_id"], {})[p["semana"]] = p

    # Resumo
    total_prev = len(prog or [])
    realizados = len([p for p in (prog or []) if p["status"] == "realizado"])

    return render_template("prev/grade.html", planos=planos, grade=grade_map, ano=ano,
                           sem_atual=sem_atual, semanas=range(1, 53),
                           PERIODICIDADES=PERIODICIDADES,
                           total_prev=total_prev, realizados=realizados)


@bp.route("/programar", methods=["POST"])
@exige("preventiva_cad")
def programar():
    """Gera a programação anual de um plano a partir da periodicidade."""
    plano_id = int(request.form["plano_id"])
    ano = int(request.form.get("ano") or db.hoje().year)
    period = request.form.get("periodicidade", "MEN")
    inicio = int(request.form.get("semana_inicial") or 1)
    limpar = bool(request.form.get("limpar"))

    plano = db.um("SELECT * FROM planos_preventiva WHERE id=%s", (plano_id,))
    if not plano:
        abort(404)

    if limpar:
        db.executar("DELETE FROM programacao WHERE plano_id=%s AND ano=%s AND status='previsto'",
                    (plano_id, ano))

    intervalo = PERIODICIDADES.get(period, ("", 4))[1]
    criadas = 0
    sem = inicio
    while sem <= 52:
        try:
            db.executar("""INSERT INTO programacao
                           (plano_id, equipamento_id, ano, semana, periodicidade, status)
                           VALUES (%s,%s,%s,%s,%s,'previsto')
                           ON CONFLICT (plano_id, ano, semana, periodicidade) DO NOTHING""",
                        (plano_id, plano["equipamento_id"], ano, sem, period))
            criadas += 1
        except Exception:
            pass
        if intervalo >= 52:
            break
        sem += intervalo

    flash(f"{criadas} preventiva(s) {PERIODICIDADES[period][0].lower()} programada(s) para {ano}.",
          "success")
    return redirect(url_for("prev.grade", ano=ano))


# ══════════════════════════════════════════════════════════════════
#  PLANOS E CHECKLISTS
# ══════════════════════════════════════════════════════════════════
@bp.route("/planos")
def planos():
    itens = db.query("""
        SELECT p.*, e.codigo AS eq_codigo, e.nome AS eq_nome, u.nome AS responsavel,
               (SELECT COUNT(*) FROM checklist_itens ci WHERE ci.plano_id=p.id) AS n_itens,
               (SELECT COUNT(*) FROM plano_materiais pm WHERE pm.plano_id=p.id) AS n_mat
        FROM planos_preventiva p
        JOIN equipamentos e ON e.id=p.equipamento_id
        LEFT JOIN usuarios u ON u.id=p.responsavel_id
        ORDER BY e.codigo""")
    return render_template("prev/planos.html", itens=itens)


@bp.route("/planos/novo", methods=["GET", "POST"])
@exige("preventiva_cad")
def plano_novo():
    if request.method == "POST":
        pid = db.inserir("""INSERT INTO planos_preventiva
                            (equipamento_id, nome, codigo_doc, responsavel_id, interna, empresa)
                            VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                         (request.form["equipamento_id"], request.form["nome"].strip(),
                          request.form.get("codigo_doc", "").strip() or None,
                          request.form.get("responsavel_id") or None,
                          request.form.get("interna") == "1",
                          request.form.get("empresa", "").strip() or None))
        flash("Plano criado. Agora cadastre os itens do check list.", "success")
        return redirect(url_for("prev.plano", plano_id=pid))

    equipamentos = db.query("SELECT id, codigo, nome FROM equipamentos WHERE ativo=TRUE ORDER BY codigo")
    manutentores = db.query("""SELECT id, nome FROM usuarios WHERE ativo=TRUE
                               AND perfil IN ('manutentor','lider','supervisao','admin')
                               ORDER BY nome""")
    return render_template("prev/plano_form.html", equipamentos=equipamentos,
                           manutentores=manutentores)


@bp.route("/planos/<int:plano_id>", methods=["GET", "POST"])
@exige("preventiva_ver")
def plano(plano_id):
    p = db.um("""SELECT p.*, e.codigo AS eq_codigo, e.nome AS eq_nome
                 FROM planos_preventiva p JOIN equipamentos e ON e.id=p.equipamento_id
                 WHERE p.id=%s""", (plano_id,))
    if not p:
        abort(404)

    if request.method == "POST":
        acao = request.form.get("acao")
        if acao == "add_item":
            ordem = db.scalar("SELECT COALESCE(MAX(ordem),0)+1 AS n FROM checklist_itens "
                              "WHERE plano_id=%s", (plano_id,), default=1)
            db.executar("""INSERT INTO checklist_itens
                           (plano_id, ordem, numero, descricao, periodicidade, tipo_resposta)
                           VALUES (%s,%s,%s,%s,%s,%s)""",
                        (plano_id, ordem, request.form.get("numero") or str(ordem),
                         request.form["descricao"].strip(),
                         request.form.get("periodicidade", "MEN"),
                         request.form.get("tipo_resposta", "ok_nok")))
            flash("Item adicionado ao check list.", "success")
        elif acao == "del_item":
            db.executar("DELETE FROM checklist_itens WHERE id=%s AND plano_id=%s",
                        (request.form["item_id"], plano_id))
        elif acao == "add_material":
            db.executar("""INSERT INTO plano_materiais
                           (plano_id, codigo, descricao, umb, qt_sem, qt_men, qt_bim,
                            qt_tri, qt_qua, qt_ses, qt_anu)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (plano_id, request.form["codigo"].strip().upper(),
                         request.form.get("descricao", "").strip(),
                         request.form.get("umb", "UNI"),
                         request.form.get("qt_sem") or 0, request.form.get("qt_men") or 0,
                         request.form.get("qt_bim") or 0, request.form.get("qt_tri") or 0,
                         request.form.get("qt_qua") or 0, request.form.get("qt_ses") or 0,
                         request.form.get("qt_anu") or 0))
            flash("Material vinculado ao plano.", "success")
        elif acao == "del_material":
            db.executar("DELETE FROM plano_materiais WHERE id=%s AND plano_id=%s",
                        (request.form["mat_id"], plano_id))
        elif acao == "editar":
            db.executar("""UPDATE planos_preventiva SET nome=%s, codigo_doc=%s,
                           responsavel_id=%s, interna=%s, empresa=%s, ativo=%s WHERE id=%s""",
                        (request.form["nome"].strip(),
                         request.form.get("codigo_doc") or None,
                         request.form.get("responsavel_id") or None,
                         request.form.get("interna") == "1",
                         request.form.get("empresa") or None,
                         request.form.get("ativo") == "1", plano_id))
            flash("Plano atualizado.", "success")
        return redirect(url_for("prev.plano", plano_id=plano_id))

    itens = db.query("SELECT * FROM checklist_itens WHERE plano_id=%s ORDER BY ordem", (plano_id,))
    materiais = db.query("SELECT * FROM plano_materiais WHERE plano_id=%s ORDER BY codigo",
                         (plano_id,))
    manutentores = db.query("""SELECT id, nome FROM usuarios WHERE ativo=TRUE
                               AND perfil IN ('manutentor','lider','supervisao','admin')
                               ORDER BY nome""")
    return render_template("prev/plano.html", p=p, itens=itens, materiais=materiais,
                           manutentores=manutentores, PERIODICIDADES=PERIODICIDADES)


# ══════════════════════════════════════════════════════════════════
#  ORDENS DE MANUTENÇÃO (execução)
# ══════════════════════════════════════════════════════════════════
@bp.route("/oms")
@exige("preventiva_ver")
def oms():
    status = request.args.get("status", "abertas")
    where = "1=1"
    if status == "abertas":
        where = "om.status IN ('aberta','em_andamento')"
    elif status != "todas":
        where = f"om.status='{status}'" if status in ("concluida", "cancelada") else "1=1"

    itens = db.query(f"""
        SELECT om.*, e.codigo AS eq_codigo, e.nome AS eq_nome, p.nome AS plano,
               m1.nome AS manutentor1, m2.nome AS manutentor2
        FROM ordens_manutencao om
        LEFT JOIN equipamentos e ON e.id=om.equipamento_id
        LEFT JOIN planos_preventiva p ON p.id=om.plano_id
        LEFT JOIN usuarios m1 ON m1.id=om.manutentor1_id
        LEFT JOIN usuarios m2 ON m2.id=om.manutentor2_id
        WHERE {where}
        ORDER BY om.status, om.data_prevista NULLS LAST, om.numero DESC LIMIT 300""")
    return render_template("prev/oms.html", itens=itens, status=status)


@bp.route("/gerar-om/<int:prog_id>", methods=["POST"])
@exige("preventiva_exec")
def gerar_om(prog_id):
    pr = db.um("""SELECT pr.*, p.nome AS plano_nome, p.responsavel_id
                  FROM programacao pr JOIN planos_preventiva p ON p.id=pr.plano_id
                  WHERE pr.id=%s""", (prog_id,))
    if not pr:
        abort(404)
    if pr["om_id"]:
        return redirect(url_for("prev.om", om_id=pr["om_id"]))

    numero = db.proximo_numero("ordens_manutencao")
    om_id = db.inserir("""
        INSERT INTO ordens_manutencao
          (numero, plano_id, programacao_id, equipamento_id, ano, semana, periodicidade,
           data_prevista, manutentor1_id, status)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'aberta') RETURNING id""",
        (numero, pr["plano_id"], prog_id, pr["equipamento_id"], pr["ano"], pr["semana"],
         pr["periodicidade"], data_da_semana(pr["ano"], pr["semana"]), pr["responsavel_id"]))
    db.executar("UPDATE programacao SET om_id=%s WHERE id=%s", (om_id, prog_id))
    flash(f"OM #{numero} gerada.", "success")
    return redirect(url_for("prev.om", om_id=om_id))


@bp.route("/gerar-semana", methods=["POST"])
@exige("preventiva_exec")
def gerar_semana():
    """Gera todas as OMs previstas para uma semana."""
    ano = int(request.form.get("ano") or db.hoje().year)
    semana = int(request.form.get("semana") or semana_atual()[1])
    pendentes = db.query("""SELECT pr.*, p.responsavel_id FROM programacao pr
                            JOIN planos_preventiva p ON p.id=pr.plano_id
                            WHERE pr.ano=%s AND pr.semana=%s AND pr.om_id IS NULL
                              AND pr.status='previsto'""", (ano, semana))
    n = 0
    for pr in pendentes or []:
        numero = db.proximo_numero("ordens_manutencao")
        om_id = db.inserir("""
            INSERT INTO ordens_manutencao
              (numero, plano_id, programacao_id, equipamento_id, ano, semana, periodicidade,
               data_prevista, manutentor1_id, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'aberta') RETURNING id""",
            (numero, pr["plano_id"], pr["id"], pr["equipamento_id"], ano, semana,
             pr["periodicidade"], data_da_semana(ano, semana), pr["responsavel_id"]))
        db.executar("UPDATE programacao SET om_id=%s WHERE id=%s", (om_id, pr["id"]))
        db.notificar(pr["responsavel_id"], f"OM #{numero} programada",
                     f"Preventiva da semana {semana}.", url_for("prev.om", om_id=om_id))
        n += 1
    flash(f"{n} Ordem(ns) de Manutenção gerada(s) para a semana {semana}.", "success")
    return redirect(url_for("prev.oms"))


@bp.route("/om/<int:om_id>", methods=["GET", "POST"])
@exige("preventiva_ver")
def om(om_id):
    o = db.um("""SELECT om.*, e.codigo AS eq_codigo, e.nome AS eq_nome, e.horimetro AS eq_hor,
                        p.nome AS plano, p.codigo_doc,
                        m1.nome AS manutentor1, m2.nome AS manutentor2, l.nome AS lider
                 FROM ordens_manutencao om
                 LEFT JOIN equipamentos e ON e.id=om.equipamento_id
                 LEFT JOIN planos_preventiva p ON p.id=om.plano_id
                 LEFT JOIN usuarios m1 ON m1.id=om.manutentor1_id
                 LEFT JOIN usuarios m2 ON m2.id=om.manutentor2_id
                 LEFT JOIN usuarios l ON l.id=om.visto_lider_id
                 WHERE om.id=%s""", (om_id,))
    if not o:
        abort(404)

    if request.method == "POST":
        acao = request.form.get("acao")

        if acao == "iniciar":
            db.executar("""UPDATE ordens_manutencao SET status='em_andamento',
                           data_inicio=COALESCE(data_inicio, NOW()),
                           manutentor1_id=COALESCE(manutentor1_id,%s) WHERE id=%s""",
                        (session["uid"], om_id))
            flash("Execução iniciada.", "success")

        elif acao == "salvar":
            _salvar_respostas(om_id, request.form, request.files)
            db.executar("""UPDATE ordens_manutencao SET observacoes=%s, horimetro=%s,
                           manutentor1_id=%s, manutentor2_id=%s, terceirizado=%s, empresa=%s
                           WHERE id=%s""",
                        (request.form.get("observacoes", "").strip(),
                         request.form.get("horimetro") or None,
                         request.form.get("manutentor1_id") or None,
                         request.form.get("manutentor2_id") or None,
                         request.form.get("terceirizado") == "1",
                         request.form.get("empresa", "").strip() or None, om_id))
            flash("Check list salvo automaticamente.", "success")

        elif acao == "concluir":
            _salvar_respostas(om_id, request.form, request.files)
            tempo = request.form.get("tempo_minutos")
            seg = int(float(tempo) * 60) if tempo else 0
            # Verifica se está no prazo (tolerância configurável)
            tol = int(db.scalar("SELECT valor FROM parametros "
                                "WHERE chave='tolerancia_preventiva_dias'", default="7") or 7)
            no_prazo = True
            if o["data_prevista"]:
                no_prazo = (db.hoje() - o["data_prevista"]).days <= tol

            db.executar("""UPDATE ordens_manutencao
                           SET status='concluida', data_fim=NOW(), tempo_total_seg=%s,
                               observacoes=%s, horimetro=%s, no_prazo=%s,
                               manutentor1_id=%s, manutentor2_id=%s,
                               terceirizado=%s, empresa=%s
                           WHERE id=%s""",
                        (seg, request.form.get("observacoes", "").strip(),
                         request.form.get("horimetro") or None, no_prazo,
                         request.form.get("manutentor1_id") or None,
                         request.form.get("manutentor2_id") or None,
                         request.form.get("terceirizado") == "1",
                         request.form.get("empresa", "").strip() or None, om_id))
            if o["programacao_id"]:
                db.executar("UPDATE programacao SET status='realizado' WHERE id=%s",
                            (o["programacao_id"],))
            if request.form.get("horimetro"):
                db.executar("UPDATE equipamentos SET horimetro=%s WHERE id=%s",
                            (request.form["horimetro"], o["equipamento_id"]))

            n_os = _gerar_os_pendencias(om_id, o)
            msg = "OM concluída."
            if n_os:
                msg += f" {n_os} OS de corretiva planejada gerada(s) a partir das pendências."
            if not no_prazo:
                msg += " Atenção: realizada fora do prazo de tolerância."
            flash(msg, "success" if no_prazo else "warning")

        elif acao == "visto_lider":
            db.executar("""UPDATE ordens_manutencao SET visto_lider_id=%s, visto_lider_em=NOW()
                           WHERE id=%s""", (session["uid"], om_id))
            flash("Visto do líder registrado — máquina liberada.", "success")

        elif acao == "reprogramar":
            nova = int(request.form["nova_semana"])
            motivo = request.form.get("motivo", "").strip()
            db.executar("""INSERT INTO reprogramacoes
                           (plano_id, tipo, ano, de_semana, para_semana, motivo, usuario_id)
                           VALUES (%s,'Reprogramação',%s,%s,%s,%s,%s)""",
                        (o["plano_id"], o["ano"], o["semana"], nova, motivo, session["uid"]))
            db.executar("UPDATE ordens_manutencao SET semana=%s, data_prevista=%s WHERE id=%s",
                        (nova, data_da_semana(o["ano"], nova), om_id))
            if o["programacao_id"]:
                db.executar("UPDATE programacao SET semana=%s WHERE id=%s",
                            (nova, o["programacao_id"]))
            flash(f"Preventiva reprogramada para a semana {nova}.", "success")

        return redirect(url_for("prev.om", om_id=om_id))

    itens = db.query("""SELECT ci.*, r.resposta, r.observacao, r.os_gerada, r.id AS resp_id
                        FROM checklist_itens ci
                        LEFT JOIN om_respostas r ON r.item_id=ci.id AND r.om_id=%s
                        WHERE ci.plano_id=%s AND ci.ativo=TRUE ORDER BY ci.ordem""",
                     (om_id, o["plano_id"]))
    materiais = db.query("SELECT * FROM plano_materiais WHERE plano_id=%s", (o["plano_id"],))
    anexos = db.query("SELECT id, nome, mime, tipo, criado_em FROM om_anexos WHERE om_id=%s",
                      (om_id,))
    manutentores = db.query("""SELECT id, nome FROM usuarios WHERE ativo=TRUE
                               AND perfil IN ('manutentor','lider','supervisao','admin')
                               ORDER BY nome""")
    return render_template("prev/om.html", o=o, itens=itens, materiais=materiais,
                           anexos=anexos, manutentores=manutentores,
                           PERIODICIDADES=PERIODICIDADES)


def _salvar_respostas(om_id, form, files):
    """Salvamento automático das respostas do check list (sem pastas)."""
    for chave, valor in form.items():
        if not chave.startswith("item_"):
            continue
        item_id = int(chave.split("_")[1])
        obs = form.get(f"obs_{item_id}", "").strip()
        existente = db.um("SELECT id FROM om_respostas WHERE om_id=%s AND item_id=%s",
                          (om_id, item_id))
        if existente:
            db.executar("UPDATE om_respostas SET resposta=%s, observacao=%s WHERE id=%s",
                        (valor, obs, existente["id"]))
        else:
            db.executar("""INSERT INTO om_respostas (om_id, item_id, resposta, observacao)
                           VALUES (%s,%s,%s,%s)""", (om_id, item_id, valor, obs))

    # Fotos e relatórios de terceiros
    for f in files.getlist("fotos") or []:
        if f and f.filename:
            dados = f.read()
            if dados:
                db.executar("""INSERT INTO om_anexos (om_id, nome, mime, tipo, dados)
                               VALUES (%s,%s,%s,'foto',%s)""",
                            (om_id, f.filename, f.mimetype, psycopg2.Binary(dados)))
    for f in files.getlist("relatorios") or []:
        if f and f.filename:
            dados = f.read()
            if dados:
                db.executar("""INSERT INTO om_anexos (om_id, nome, mime, tipo, dados)
                               VALUES (%s,%s,%s,'relatorio',%s)""",
                            (om_id, f.filename, f.mimetype, psycopg2.Binary(dados)))


def _gerar_os_pendencias(om_id, o):
    """Cada item NOK vira uma OS de corretiva planejada."""
    noks = db.query("""SELECT r.*, ci.descricao FROM om_respostas r
                       JOIN checklist_itens ci ON ci.id=r.item_id
                       WHERE r.om_id=%s AND r.resposta='NOK' AND r.os_gerada IS NULL""",
                    (om_id,))
    crit = db.scalar("SELECT criticidade FROM equipamentos WHERE id=%s",
                     (o["equipamento_id"],), default="C")
    n = 0
    for r in noks or []:
        numero = db.proximo_numero("ordens_servico")
        desc = (f"[Pendência da preventiva OM #{o['numero']}] {r['descricao']}"
                + (f" — Obs.: {r['observacao']}" if r["observacao"] else ""))
        os_id = db.inserir("""
            INSERT INTO ordens_servico
              (numero, tipo_manutencao, equipamento_id, descricao_problema, solicitante_id,
               criticidade, status, origem, origem_id)
            VALUES (%s,'planejada',%s,%s,%s,%s,'aberta','preventiva',%s) RETURNING id""",
            (numero, o["equipamento_id"], desc, session["uid"], crit, om_id))
        db.executar("""INSERT INTO os_apontamentos (os_id, usuario_id, tipo, descricao)
                       VALUES (%s,%s,'abertura','OS gerada automaticamente por pendência na preventiva.')""",
                    (os_id, session["uid"]))
        db.executar("UPDATE om_respostas SET os_gerada=%s WHERE id=%s", (os_id, r["id"]))
        n += 1
    if n:
        db.notificar_perfis(("lider", "supervisao"),
                            f"{n} OS gerada(s) por pendência de preventiva",
                            f"OM #{o['numero']} — {o['eq_codigo']}", url_for("os.lista"))
    return n


@bp.route("/om/anexo/<int:anexo_id>")
def om_anexo(anexo_id):
    a = db.um("SELECT * FROM om_anexos WHERE id=%s", (anexo_id,))
    if not a:
        abort(404)
    return Response(bytes(a["dados"]), mimetype=a["mime"] or "application/octet-stream",
                    headers={"Content-Disposition": f'inline; filename="{a["nome"]}"'})


# ══════════════════════════════════════════════════════════════════
#  PLANO DE MATERIAIS (necessidades futuras)
# ══════════════════════════════════════════════════════════════════
# ── Coluna de quantidade correspondente a cada periodicidade ──
COL_PERIODICIDADE = {
    "SEM": "qt_sem", "MEN": "qt_men", "BIM": "qt_bim", "TRI": "qt_tri",
    "QUA": "qt_qua", "SES": "qt_ses", "ANU": "qt_anu",
    # periodicidades longas consomem a mesma lista da anual
    "BIE": "qt_anu", "TRIE": "qt_anu",
}


def semanas_do_periodo(d_ini, d_fim):
    """Lista de tuplas (ano, semana) ISO cobertas pelo intervalo de datas."""
    semanas, d = [], d_ini
    while d <= d_fim:
        a, s, _ = d.isocalendar()
        if (a, s) not in semanas:
            semanas.append((a, s))
        d += timedelta(days=1)
    return semanas


def calcular_necessidade(d_ini, d_fim, considerar_min=False, apenas_pendentes=True):
    """
    Varre a grade de 52 semanas no intervalo informado, soma a necessidade de
    materiais de cada preventiva programada e cruza com o saldo atual.

    Devolve (linhas, detalhe, resumo).
    """
    semanas = semanas_do_periodo(d_ini, d_fim)
    if not semanas:
        return [], {}, {}

    filtro_status = "AND pr.status='previsto'" if apenas_pendentes else ""

    # Todas as preventivas programadas no período, com seus materiais
    prog = db.query(f"""
        SELECT pr.id AS prog_id, pr.ano, pr.semana, pr.periodicidade, pr.status,
               pr.om_id, p.id AS plano_id, p.nome AS plano,
               e.codigo AS eq_codigo, e.nome AS eq_nome, e.criticidade
        FROM programacao pr
        JOIN planos_preventiva p ON p.id = pr.plano_id AND p.ativo = TRUE
        JOIN equipamentos e ON e.id = pr.equipamento_id
        WHERE (pr.ano, pr.semana) IN %s {filtro_status}
        ORDER BY pr.ano, pr.semana, e.codigo""", (tuple(semanas),))

    necessidade, detalhe = {}, {}
    for pr in prog or []:
        col = COL_PERIODICIDADE.get(pr["periodicidade"])
        if not col:
            continue
        mats = db.query(f"""SELECT codigo, descricao, umb, {col} AS qt
                            FROM plano_materiais
                            WHERE plano_id = %s AND {col} > 0""", (pr["plano_id"],))
        for m in mats or []:
            cod = (m["codigo"] or "").strip().upper()
            if not cod:
                continue
            qt = float(m["qt"] or 0)
            if cod not in necessidade:
                necessidade[cod] = {"codigo": cod, "descricao": m["descricao"] or "",
                                    "umb": m["umb"] or "UNI", "necessario": 0.0,
                                    "n_preventivas": 0}
            necessidade[cod]["necessario"] += qt
            necessidade[cod]["n_preventivas"] += 1
            detalhe.setdefault(cod, []).append({
                "eq_codigo": pr["eq_codigo"], "eq_nome": pr["eq_nome"],
                "criticidade": pr["criticidade"], "plano": pr["plano"],
                "semana": pr["semana"], "ano": pr["ano"],
                "periodicidade": pr["periodicidade"], "qt": qt,
                "data": data_da_semana(pr["ano"], pr["semana"]),
                "om_id": pr["om_id"],
            })

    # ── Cruzamento com o estoque ──
    linhas = []
    for cod, v in necessidade.items():
        mat = db.um("SELECT * FROM materiais WHERE codigo = %s", (cod,))
        if mat:
            saldo = (db.saldo_material(cod) if mat["tipo"] == "NLAG"
                     else float(mat["saldo_sap"] or 0))
            minimo = float(mat["estoque_min"] or 0)
            v.update({
                "cadastrado": True, "tipo": mat["tipo"], "saldo": saldo,
                "estoque_min": minimo, "estoque_max": float(mat["estoque_max"] or 0),
                "valor_unit": float(mat["valor_unit"] or 0),
                "critico": mat["critico"], "localizacao": mat["localizacao"] or "",
                "umb": mat["unidade"] or v["umb"],
                "descricao": v["descricao"] or mat["descricao"],
            })
            # Se "reservar o estoque mínimo", o disponível desconta o mínimo
            disponivel = saldo - minimo if considerar_min else saldo
        else:
            v.update({"cadastrado": False, "tipo": "—", "saldo": 0.0,
                      "estoque_min": 0.0, "estoque_max": 0.0, "valor_unit": 0.0,
                      "critico": False, "localizacao": ""})
            disponivel = 0.0

        v["disponivel"] = disponivel
        v["falta"] = max(0.0, round(v["necessario"] - disponivel, 3))
        v["custo_falta"] = round(v["falta"] * v["valor_unit"], 2)

        if not v["cadastrado"]:
            v["situacao"] = "nao_cadastrado"
        elif v["falta"] <= 0:
            v["situacao"] = "disponivel"
        elif disponivel <= 0:
            v["situacao"] = "sem_estoque"
        else:
            v["situacao"] = "parcial"

        # Data da primeira preventiva que precisa do item
        datas = [d["data"] for d in detalhe.get(cod, []) if d["data"]]
        v["primeira_data"] = min(datas) if datas else None
        linhas.append(v)

    ordem = {"sem_estoque": 0, "nao_cadastrado": 1, "parcial": 2, "disponivel": 3}
    linhas.sort(key=lambda x: (ordem[x["situacao"]],
                               x["primeira_data"] or d_fim, x["codigo"]))

    resumo = {
        "itens": len(linhas),
        "preventivas": len(prog or []),
        "disponivel": len([l for l in linhas if l["situacao"] == "disponivel"]),
        "parcial": len([l for l in linhas if l["situacao"] == "parcial"]),
        "sem_estoque": len([l for l in linhas if l["situacao"] == "sem_estoque"]),
        "nao_cadastrado": len([l for l in linhas if l["situacao"] == "nao_cadastrado"]),
        "custo_total": round(sum(l["custo_falta"] for l in linhas), 2),
    }
    resumo["a_comprar"] = resumo["parcial"] + resumo["sem_estoque"] + resumo["nao_cadastrado"]
    return linhas, detalhe, resumo


def _periodo_do_request():
    """Lê dias / datas da querystring e devolve (d_ini, d_fim, dias)."""
    ini = request.args.get("ini")
    fim = request.args.get("fim")
    if ini and fim:
        try:
            d_ini, d_fim = date.fromisoformat(ini), date.fromisoformat(fim)
            if d_fim < d_ini:
                d_ini, d_fim = d_fim, d_ini
            return d_ini, d_fim, (d_fim - d_ini).days + 1
        except ValueError:
            pass
    try:
        dias = max(1, min(int(request.args.get("dias", 30)), 730))
    except ValueError:
        dias = 30
    d_ini = db.hoje()
    return d_ini, d_ini + timedelta(days=dias - 1), dias


@bp.route("/plano-materiais")
@exige("preventiva_ver")
def plano_materiais():
    """
    Lista de materiais das preventivas programadas na grade de 52 semanas,
    dentro de um horizonte em dias (padrão 30, customizável), comparada
    com o saldo atual de estoque.
    """
    d_ini, d_fim, dias = _periodo_do_request()
    considerar_min = request.args.get("considerar_min") == "1"
    apenas_pendentes = request.args.get("todas") != "1"
    situacao = request.args.get("situacao", "")

    linhas, detalhe, resumo = calcular_necessidade(
        d_ini, d_fim, considerar_min, apenas_pendentes)

    if situacao == "faltantes":
        linhas = [l for l in linhas if l["situacao"] != "disponivel"]
    elif situacao:
        linhas = [l for l in linhas if l["situacao"] == situacao]

    return render_template("prev/plano_materiais.html", linhas=linhas, detalhe=detalhe,
                           resumo=resumo, d_ini=d_ini, d_fim=d_fim, dias=dias,
                           considerar_min=considerar_min,
                           apenas_pendentes=apenas_pendentes, situacao=situacao,
                           semanas=semanas_do_periodo(d_ini, d_fim))


@bp.route("/plano-materiais/exportar")
@exige("preventiva_ver")
def plano_materiais_exportar():
    d_ini, d_fim, dias = _periodo_do_request()
    considerar_min = request.args.get("considerar_min") == "1"
    apenas_pendentes = request.args.get("todas") != "1"
    linhas, detalhe, _ = calcular_necessidade(
        d_ini, d_fim, considerar_min, apenas_pendentes)

    rotulo = {"disponivel": "Em estoque", "parcial": "Parcial",
              "sem_estoque": "Sem estoque", "nao_cadastrado": "Nao cadastrado"}

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([f"PLANO DE MATERIAIS DE MANUTENCAO PREVENTIVA — "
                f"{d_ini.strftime('%d/%m/%Y')} a {d_fim.strftime('%d/%m/%Y')} ({dias} dias)"])
    w.writerow([])
    w.writerow(["Codigo", "Descricao", "UMB", "Deposito", "Necessario", "Saldo atual",
                "Estoque minimo", "Disponivel", "Falta", "Situacao", "Valor unit.",
                "Custo da falta", "1a preventiva", "Equipamentos"])
    for l in linhas:
        equipos = ", ".join(sorted({d["eq_codigo"] for d in detalhe.get(l["codigo"], [])}))
        w.writerow([
            l["codigo"], l["descricao"], l["umb"], l["tipo"],
            f"{l['necessario']:g}", f"{l['saldo']:g}", f"{l['estoque_min']:g}",
            f"{l['disponivel']:g}", f"{l['falta']:g}", rotulo[l["situacao"]],
            f"{l['valor_unit']:.2f}".replace(".", ","),
            f"{l['custo_falta']:.2f}".replace(".", ","),
            l["primeira_data"].strftime("%d/%m/%Y") if l["primeira_data"] else "",
            equipos,
        ])
    nome = f"plano_materiais_{d_ini:%Y%m%d}_{d_fim:%Y%m%d}.csv"
    return Response(buf.getvalue().encode("utf-8-sig"), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={nome}"})


@bp.route("/plano-materiais/solicitar", methods=["POST"])
@exige("preventiva_ver")
def plano_materiais_solicitar():
    """Gera solicitações de material para os itens marcados na lista."""
    codigos = request.form.getlist("codigo")
    if not codigos:
        flash("Selecione ao menos um item.", "warning")
        return redirect(request.referrer or url_for("prev.plano_materiais"))

    d_ini, d_fim, dias = _periodo_do_request()
    linhas, detalhe, _ = calcular_necessidade(
        d_ini, d_fim, request.form.get("considerar_min") == "1")
    por_codigo = {l["codigo"]: l for l in linhas}

    n = 0
    for cod in codigos:
        l = por_codigo.get(cod)
        if not l or l["falta"] <= 0:
            continue
        equipos = ", ".join(sorted({d["eq_codigo"] for d in detalhe.get(cod, [])})[:6])
        numero = db.proximo_numero("solicitacoes_material")
        sid = db.inserir("""
            INSERT INTO solicitacoes_material
              (numero, solicitante_id, codigo, descricao, tipo, quantidade,
               observacoes, dt_solicitacao, situacao)
            VALUES (%s,%s,%s,%s,%s,%s,%s,CURRENT_DATE,'Solicitado') RETURNING id""",
            (numero, session["uid"], cod if l["cadastrado"] else None,
             l["descricao"] or cod,
             "Estoque NLAG" if l["cadastrado"] else "Cadastro",
             l["falta"],
             f"Plano de materiais das preventivas de "
             f"{d_ini:%d/%m/%Y} a {d_fim:%d/%m/%Y}. Equipamentos: {equipos}."))
        db.executar("""INSERT INTO solicitacao_historico
                       (solicitacao_id, usuario_id, situacao, comentario)
                       VALUES (%s,%s,'Solicitado','Gerada pelo plano de materiais.')""",
                    (sid, session["uid"]))
        n += 1

    if n:
        db.notificar_perfis(("analista", "lider"),
                            f"{n} solicitação(ões) do plano de materiais",
                            f"Necessidade das preventivas até {d_fim:%d/%m/%Y}.",
                            url_for("sol.lista"))
        flash(f"{n} solicitação(ões) de material gerada(s).", "success")
    else:
        flash("Nenhum item selecionado tinha falta de saldo.", "warning")
    return redirect(url_for("sol.lista"))


@bp.route("/reprogramacoes")
@exige("preventiva_ver")
def reprogramacoes():
    itens = db.query("""SELECT r.*, p.nome AS plano, e.codigo AS eq_codigo,
                               u.nome AS usuario
                        FROM reprogramacoes r
                        LEFT JOIN planos_preventiva p ON p.id=r.plano_id
                        LEFT JOIN equipamentos e ON e.id=p.equipamento_id
                        LEFT JOIN usuarios u ON u.id=r.usuario_id
                        ORDER BY r.criado_em DESC LIMIT 300""")
    return render_template("prev/reprogramacoes.html", itens=itens)
