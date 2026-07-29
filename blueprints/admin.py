"""
ÁREA ADMINISTRADOR
Usuários e permissões, centros de trabalho, equipamentos, defeitos/causas,
centros de custo, parâmetros do sistema e auditoria.
"""
import io
import csv

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, session, abort, Response)
from werkzeug.security import generate_password_hash

import db
import mailer
import email_config
from auth import exige, PERFIS

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/")
@exige("cadastros")
def index():
    stats = {
        "usuarios": db.scalar("SELECT COUNT(*) AS n FROM usuarios WHERE ativo=TRUE"),
        "equipamentos": db.scalar("SELECT COUNT(*) AS n FROM equipamentos WHERE ativo=TRUE"),
        "centros": db.scalar("SELECT COUNT(*) AS n FROM centros_trabalho WHERE ativo=TRUE"),
        "planos": db.scalar("SELECT COUNT(*) AS n FROM planos_preventiva WHERE ativo=TRUE"),
        "materiais": db.scalar("SELECT COUNT(*) AS n FROM materiais WHERE ativo=TRUE"),
        "os": db.scalar("SELECT COUNT(*) AS n FROM ordens_servico"),
    }
    return render_template("admin/index.html", stats=stats)


# ══════════════════════════════════════════════════════════════════
#  USUÁRIOS
# ══════════════════════════════════════════════════════════════════
@bp.route("/usuarios", methods=["GET", "POST"])
@exige("admin")
def usuarios():
    if request.method == "POST":
        acao = request.form.get("acao", "novo")
        if acao == "novo":
            usuario = request.form["usuario"].strip().lower()
            senha = request.form.get("senha") or "decio@2026"
            if db.um("SELECT id FROM usuarios WHERE lower(usuario)=%s", (usuario,)):
                flash("Este usuário já existe.", "danger")
            else:
                db.executar("""INSERT INTO usuarios
                               (usuario, senha_hash, nome, email, matricula, perfil, telefone)
                               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                            (usuario, generate_password_hash(senha),
                             request.form["nome"].strip(),
                             request.form.get("email", "").strip() or None,
                             request.form.get("matricula", "").strip() or None,
                             request.form.get("perfil", "solicitante"),
                             request.form.get("telefone", "").strip() or None))
                flash(f"Usuário {usuario} criado.", "success")
        elif acao == "editar":
            uid = request.form["uid"]
            db.executar("""UPDATE usuarios SET nome=%s, email=%s, matricula=%s,
                           perfil=%s, telefone=%s WHERE id=%s""",
                        (request.form["nome"].strip(),
                         request.form.get("email", "").strip() or None,
                         request.form.get("matricula", "").strip() or None,
                         request.form.get("perfil"),
                         request.form.get("telefone", "").strip() or None, uid))
            flash("Usuário atualizado.", "success")
        elif acao == "senha":
            uid = request.form["uid"]
            nova = request.form.get("senha", "")
            if len(nova) < 6:
                flash("A senha precisa ter ao menos 6 caracteres.", "warning")
            else:
                db.executar("UPDATE usuarios SET senha_hash=%s WHERE id=%s",
                            (generate_password_hash(nova), uid))
                flash("Senha redefinida.", "success")
        elif acao == "toggle":
            uid = int(request.form["uid"])
            if uid == session["uid"]:
                flash("Você não pode desativar o próprio usuário.", "warning")
            else:
                db.executar("UPDATE usuarios SET ativo = NOT ativo WHERE id=%s", (uid,))
                flash("Status alterado.", "success")
        return redirect(url_for("admin.usuarios"))

    itens = db.query("SELECT * FROM usuarios ORDER BY ativo DESC, nome")
    return render_template("admin/usuarios.html", itens=itens, PERFIS=PERFIS)


# ══════════════════════════════════════════════════════════════════
#  EQUIPAMENTOS
# ══════════════════════════════════════════════════════════════════
@bp.route("/equipamentos", methods=["GET", "POST"])
@exige("cadastros")
def equipamentos():
    if request.method == "POST":
        acao = request.form.get("acao", "novo")
        if acao == "novo":
            grupo = request.form["grupo_prev"].strip().upper()
            sub = request.form.get("subcodigo", "00").strip() or "00"
            codigo = f"{grupo}-{sub}"
            if db.um("SELECT id FROM equipamentos WHERE codigo=%s", (codigo,)):
                flash(f"O equipamento {codigo} já existe.", "danger")
            else:
                db.executar("""INSERT INTO equipamentos
                     (grupo_prev, subcodigo, codigo, nome, descricao, centro_trabalho_id,
                      estabelecimento_id, criticidade, tipo, n_serie, fabricante,
                      patrimonio, ano_fabricacao, capacidade, custo_hora_parada)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (grupo, sub, codigo, request.form["nome"].strip(),
                     request.form.get("descricao", "").strip() or None,
                     request.form.get("centro_trabalho_id") or None,
                     request.form.get("estabelecimento_id") or None,
                     request.form.get("criticidade", "C"),
                     request.form.get("tipo", "Industrial"),
                     request.form.get("n_serie", "").strip() or None,
                     request.form.get("fabricante", "").strip() or None,
                     request.form.get("patrimonio", "").strip() or None,
                     request.form.get("ano_fabricacao", "").strip() or None,
                     request.form.get("capacidade", "").strip() or None,
                     request.form.get("custo_hora_parada") or 0))
                flash(f"Equipamento {codigo} cadastrado.", "success")
        elif acao == "editar":
            eid = request.form["eid"]
            db.executar("""UPDATE equipamentos SET nome=%s, descricao=%s, centro_trabalho_id=%s,
                           estabelecimento_id=%s, criticidade=%s, tipo=%s, n_serie=%s,
                           fabricante=%s, patrimonio=%s, ano_fabricacao=%s, capacidade=%s,
                           custo_hora_parada=%s, status=%s, ativo=%s WHERE id=%s""",
                        (request.form["nome"].strip(),
                         request.form.get("descricao", "").strip() or None,
                         request.form.get("centro_trabalho_id") or None,
                         request.form.get("estabelecimento_id") or None,
                         request.form.get("criticidade", "C"),
                         request.form.get("tipo", "Industrial"),
                         request.form.get("n_serie", "").strip() or None,
                         request.form.get("fabricante", "").strip() or None,
                         request.form.get("patrimonio", "").strip() or None,
                         request.form.get("ano_fabricacao", "").strip() or None,
                         request.form.get("capacidade", "").strip() or None,
                         request.form.get("custo_hora_parada") or 0,
                         request.form.get("status", "operando"),
                         request.form.get("ativo", "1") == "1", eid))
            flash("Equipamento atualizado.", "success")
        elif acao == "status":
            db.executar("UPDATE equipamentos SET status=%s WHERE id=%s",
                        (request.form["status"], request.form["eid"]))
        return redirect(url_for("admin.equipamentos"))

    busca = request.args.get("q", "").strip()
    editar = request.args.get("editar")
    where, params = "1=1", []
    if busca:
        where = "(e.codigo ILIKE %s OR e.nome ILIKE %s)"
        params = [f"%{busca}%", f"%{busca}%"]
    itens = db.query(f"""SELECT e.*, ct.nome AS setor, est.nome AS estabelecimento
                         FROM equipamentos e
                         LEFT JOIN centros_trabalho ct ON ct.id=e.centro_trabalho_id
                         LEFT JOIN estabelecimentos est ON est.id=e.estabelecimento_id
                         WHERE {where} ORDER BY e.codigo""", params)
    equipamento = db.um("SELECT * FROM equipamentos WHERE id=%s", (editar,)) if editar else None
    centros = db.query("SELECT * FROM centros_trabalho WHERE ativo=TRUE ORDER BY nome")
    estabs = db.query("SELECT * FROM estabelecimentos ORDER BY nome")
    return render_template("admin/equipamentos.html", itens=itens, centros=centros,
                           estabs=estabs, busca=busca, equipamento=equipamento)


@bp.route("/equipamentos/importar", methods=["POST"])
@exige("cadastros")
def importar_equipamentos():
    f = request.files.get("arquivo")
    if not f or not f.filename:
        flash("Selecione um arquivo.", "warning")
        return redirect(url_for("admin.equipamentos"))
    try:
        conteudo = f.read().decode("utf-8-sig", errors="ignore")
        delim = ";" if conteudo.count(";") > conteudo.count(",") else ","
        linhas = list(csv.DictReader(io.StringIO(conteudo), delimiter=delim))
    except Exception as e:
        flash(f"Erro ao ler o arquivo: {e}", "danger")
        return redirect(url_for("admin.equipamentos"))

    n = 0
    for l in linhas:
        norm = {str(k).strip().lower(): (v or "").strip() for k, v in l.items() if k}
        grupo = (norm.get("grupo prev") or norm.get("grupo_prev") or norm.get("cod") or "").upper()
        if not grupo:
            continue
        sub = norm.get("cód") or norm.get("subcodigo") or "00"
        codigo = f"{grupo}-{sub}"
        db.executar("""INSERT INTO equipamentos
             (grupo_prev, subcodigo, codigo, nome, criticidade, n_serie, fabricante,
              patrimonio, ano_fabricacao, capacidade)
             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
             ON CONFLICT (codigo) DO UPDATE SET nome=EXCLUDED.nome""",
            (grupo, sub, codigo,
             norm.get("equipamento") or norm.get("nome") or codigo,
             (norm.get("criticidade") or "C")[:1].upper(),
             norm.get("n° série") or norm.get("n serie") or None,
             norm.get("fabricante") or None, norm.get("patrimônio") or None,
             norm.get("ano fabricação") or None, norm.get("capacidade") or None))
        n += 1
    flash(f"{n} equipamento(s) importado(s).", "success")
    return redirect(url_for("admin.equipamentos"))


# ══════════════════════════════════════════════════════════════════
#  CRITICIDADE — níveis configuráveis e matriz de classificação
# ══════════════════════════════════════════════════════════════════
@bp.route("/criticidades", methods=["GET", "POST"])
@exige("cadastros")
def criticidades():
    if request.method == "POST":
        acao = request.form.get("acao")

        if acao == "salvar":
            for cod in request.form.getlist("codigo"):
                db.executar("""UPDATE criticidades SET nome=%s, ordem=%s, cor=%s,
                               sla_resposta_h=%s, sla_conclusao_h=%s,
                               descricao=%s, ativo=%s
                               WHERE codigo=%s""",
                            (request.form.get(f"nome_{cod}", "").strip() or cod,
                             request.form.get(f"ordem_{cod}") or 99,
                             request.form.get(f"cor_{cod}", "#5B93C4"),
                             request.form.get(f"resp_{cod}") or None,
                             request.form.get(f"concl_{cod}") or None,
                             request.form.get(f"desc_{cod}", "").strip() or None,
                             request.form.get(f"ativo_{cod}") == "1",
                             cod))
            flash("Níveis de criticidade atualizados.", "success")

        elif acao == "novo":
            cod = (request.form.get("novo_codigo") or "").strip().upper()[:1]
            if not cod:
                flash("Informe a letra do nível.", "warning")
            elif db.um("SELECT codigo FROM criticidades WHERE codigo=%s", (cod,)):
                flash(f"O nível {cod} já existe.", "danger")
            else:
                prox = db.scalar("SELECT COALESCE(MAX(ordem),0)+1 AS n FROM criticidades",
                                 default=1)
                db.executar("""INSERT INTO criticidades (codigo, nome, ordem, cor,
                               sla_resposta_h, sla_conclusao_h, descricao)
                               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                            (cod, request.form.get("novo_nome", "").strip() or cod,
                             prox, request.form.get("nova_cor", "#5B93C4"),
                             request.form.get("nova_resp") or None,
                             request.form.get("nova_concl") or None,
                             request.form.get("nova_desc", "").strip() or None))
                flash(f"Nível {cod} criado.", "success")

        elif acao == "reclassificar":
            n = _reclassificar_todos()
            flash(f"{n} equipamento(s) reclassificado(s) pela matriz.", "success")

        return redirect(url_for("admin.criticidades"))

    niveis = db.niveis_criticidade(ativos=False)
    uso = {n["codigo"]: db.scalar(
        "SELECT COUNT(*) AS n FROM equipamentos WHERE criticidade=%s AND ativo=TRUE",
        (n["codigo"],), default=0) for n in niveis}
    sem_matriz = db.scalar(
        "SELECT COUNT(*) AS n FROM equipamentos WHERE ativo=TRUE AND mtz_pontuacao IS NULL")
    return render_template("admin/criticidades.html", niveis=niveis, uso=uso,
                           sem_matriz=sem_matriz, CRITERIOS=db.CRITERIOS_MATRIZ)


def _reclassificar_todos():
    """Recalcula a criticidade de todo equipamento que já tem a matriz preenchida."""
    equips = db.query("""SELECT * FROM equipamentos
                         WHERE ativo=TRUE AND mtz_pontuacao IS NOT NULL""")
    n = 0
    for e in equips or []:
        nota = db.classificar(float(e["mtz_pontuacao"]))
        if nota != e["criticidade"]:
            db.executar("UPDATE equipamentos SET criticidade=%s WHERE id=%s", (nota, e["id"]))
            n += 1
    return n


@bp.route("/equipamentos/<int:eq_id>/matriz", methods=["GET", "POST"])
@exige("cadastros")
def matriz(eq_id):
    e = db.um("SELECT * FROM equipamentos WHERE id=%s", (eq_id,))
    if not e:
        abort(404)

    if request.method == "POST":
        notas = {campo: request.form.get(campo) for campo, *_ in db.CRITERIOS_MATRIZ}
        pontuacao = db.pontuar_matriz(notas)
        sugerido = db.classificar(pontuacao)
        aplicar = request.form.get("aplicar") == "1"
        manual = (request.form.get("criticidade_manual") or "").strip().upper()

        final = manual if (manual and not aplicar) else sugerido
        db.executar("""UPDATE equipamentos SET
                       mtz_seguranca=%s, mtz_producao=%s, mtz_qualidade=%s,
                       mtz_frequencia=%s, mtz_reparo=%s, mtz_redundancia=%s,
                       mtz_pontuacao=%s, mtz_avaliado_em=NOW(), mtz_avaliado_por=%s,
                       mtz_justificativa=%s, criticidade=%s
                       WHERE id=%s""",
                    (notas["mtz_seguranca"] or 0, notas["mtz_producao"] or 0,
                     notas["mtz_qualidade"] or 0, notas["mtz_frequencia"] or 0,
                     notas["mtz_reparo"] or 0, notas["mtz_redundancia"] or 0,
                     pontuacao, session["uid"],
                     request.form.get("justificativa", "").strip() or None,
                     final, eq_id))
        db.registrar_log(session["uid"], session["nome"], "classificar_criticidade",
                         "equipamentos", eq_id,
                         f"pontuação {pontuacao} → {final}")
        if final != sugerido:
            flash(f"Pontuação {pontuacao:g} sugeria {sugerido}; "
                  f"gravado {final} conforme sua escolha.", "warning")
        else:
            flash(f"Pontuação {pontuacao:g} — criticidade {final} aplicada.", "success")
        return redirect(url_for("admin.matriz", eq_id=eq_id))

    avaliador = None
    if e["mtz_avaliado_por"]:
        avaliador = db.scalar("SELECT nome FROM usuarios WHERE id=%s",
                              (e["mtz_avaliado_por"],), default=None)
    niveis = db.niveis_criticidade()
    faixa = 100.0 / len(niveis) if niveis else 100.0
    escala = [{"codigo": n["codigo"], "nome": n["nome"], "cor": n["cor"],
               "de": round(100 - (i + 1) * faixa, 1), "ate": round(100 - i * faixa, 1)}
              for i, n in enumerate(niveis)]
    return render_template("admin/matriz.html", e=e, CRITERIOS=db.CRITERIOS_MATRIZ,
                           niveis=niveis, escala=escala, avaliador=avaliador)


# ══════════════════════════════════════════════════════════════════
#  CADASTROS AUXILIARES
# ══════════════════════════════════════════════════════════════════
@bp.route("/cadastros", methods=["GET", "POST"])
@exige("cadastros")
def cadastros():
    if request.method == "POST":
        acao = request.form.get("acao")
        if acao == "centro_trabalho":
            db.executar("""INSERT INTO centros_trabalho (codigo, nome, estabelecimento_id)
                           VALUES (%s,%s,%s) ON CONFLICT (codigo) DO UPDATE SET nome=EXCLUDED.nome""",
                        (request.form["codigo"].strip().upper(), request.form["nome"].strip(),
                         request.form.get("estabelecimento_id") or None))
        elif acao == "centro_custo":
            db.executar("""INSERT INTO centros_custo (codigo, nome) VALUES (%s,%s)
                           ON CONFLICT (codigo) DO UPDATE SET nome=EXCLUDED.nome""",
                        (request.form["codigo"].strip(), request.form["nome"].strip()))
        elif acao == "defeito":
            db.executar("INSERT INTO defeitos (nome) VALUES (%s) ON CONFLICT DO NOTHING",
                        (request.form["nome"].strip(),))
        elif acao == "causa":
            db.executar("INSERT INTO causas (nome, defeito_id) VALUES (%s,%s) "
                        "ON CONFLICT DO NOTHING",
                        (request.form["nome"].strip(), request.form.get("defeito_id") or None))
        elif acao == "estabelecimento":
            db.executar("INSERT INTO estabelecimentos (nome, codigo) VALUES (%s,%s) "
                        "ON CONFLICT (nome) DO NOTHING",
                        (request.form["nome"].strip(), request.form.get("codigo", "").strip()))
        elif acao == "desativar":
            tabela = request.form["tabela"]
            if tabela in ("centros_trabalho", "centros_custo", "defeitos", "causas",
                          "estabelecimentos"):
                db.executar(f"UPDATE {tabela} SET ativo = NOT ativo WHERE id=%s",
                            (request.form["id"],))
        flash("Cadastro atualizado.", "success")
        return redirect(url_for("admin.cadastros"))

    dados = {
        "centros": db.query("""SELECT ct.*, e.nome AS estab FROM centros_trabalho ct
                               LEFT JOIN estabelecimentos e ON e.id=ct.estabelecimento_id
                               ORDER BY ct.nome"""),
        "custos": db.query("SELECT * FROM centros_custo ORDER BY codigo"),
        "defeitos": db.query("SELECT * FROM defeitos ORDER BY nome"),
        "causas": db.query("""SELECT c.*, d.nome AS defeito FROM causas c
                              LEFT JOIN defeitos d ON d.id=c.defeito_id ORDER BY c.nome"""),
        "estabs": db.query("SELECT * FROM estabelecimentos ORDER BY nome"),
    }
    return render_template("admin/cadastros.html", **dados)


# ══════════════════════════════════════════════════════════════════
#  TERCEIROS
# ══════════════════════════════════════════════════════════════════
@bp.route("/terceiros", methods=["GET", "POST"])
@exige("cadastros")
def terceiros():
    if request.method == "POST":
        db.executar("""INSERT INTO manutencoes_terceiros
                       (equipamento_id, manutentor_id, empresa, tipo_servico, descricao,
                        data_envio, data_retorno, valor, recebido_por)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (request.form.get("equipamento_id") or None, session["uid"],
                     request.form["empresa"].strip(),
                     request.form.get("tipo_servico", "Corretiva"),
                     request.form.get("descricao", "").strip(),
                     request.form.get("data_envio") or None,
                     request.form.get("data_retorno") or None,
                     request.form.get("valor") or 0,
                     request.form.get("recebido_por", "").strip() or None))
        flash("Registro adicionado.", "success")
        return redirect(url_for("admin.terceiros"))

    itens = db.query("""SELECT t.*, e.codigo AS eq_codigo, e.nome AS eq_nome, u.nome AS manutentor
                        FROM manutencoes_terceiros t
                        LEFT JOIN equipamentos e ON e.id=t.equipamento_id
                        LEFT JOIN usuarios u ON u.id=t.manutentor_id
                        ORDER BY t.data_envio DESC NULLS LAST LIMIT 200""")
    equipamentos = db.query("SELECT id, codigo, nome FROM equipamentos WHERE ativo=TRUE ORDER BY codigo")
    return render_template("admin/terceiros.html", itens=itens, equipamentos=equipamentos)


# ══════════════════════════════════════════════════════════════════
#  E-MAIL
# ══════════════════════════════════════════════════════════════════
@bp.route("/email", methods=["GET", "POST"])
@exige("admin")
def email():
    if request.method == "POST":
        acao = request.form.get("acao")

        if acao == "servidor":
            email_config.salvar_formulario(request.form)
            db.registrar_log(session["uid"], session["nome"], "config_email",
                             detalhe=request.form.get("smtp_usuario", ""))
            flash("Configuração de envio salva.", "success")

        elif acao == "eventos":
            for chave in mailer.EVENTOS:
                ligado = "1" if request.form.get("ev_" + chave) == "1" else "0"
                db.executar("""INSERT INTO parametros (chave, valor) VALUES (%s,%s)
                               ON CONFLICT (chave) DO UPDATE SET valor=EXCLUDED.valor""",
                            ("email_" + chave, ligado))
            flash("Preferências de e-mail salvas.", "success")

        elif acao == "limpar_senha":
            email_config.apagar_senha()
            flash("Senha removida. Cadastre uma nova para voltar a enviar.", "warning")

        elif acao == "teste":
            destino = (request.form.get("destino") or "").strip()
            if not mailer.email_valido(destino):
                flash("Informe um endereço de e-mail válido.", "warning")
            else:
                c = email_config.configuracao()
                html = mailer.montar_html(
                    "Teste de configuração", "Se você recebeu, está tudo certo",
                    [("Servidor", f"{c['smtp_host']}:{c['smtp_porta']}"),
                     ("Segurança", (c["smtp_seguranca"] or "").upper()),
                     ("Conta de envio", c["smtp_usuario"]),
                     ("Remetente", c["smtp_remetente"]),
                     ("Enviado por", session["nome"]),
                     ("Data", db.agora().strftime("%d/%m/%Y às %H:%M"))],
                    mensagem="Este é um envio de teste do Sistema Centralizado "
                             "de Manutenção da Décio Metalúrgica.",
                    botao=("Abrir o sistema", mailer.url(url_for("home.index"))))
                ok, erro = mailer.enviar_agora(
                    [destino], "[Manutenção] Teste de configuração de e-mail", html)
                if ok:
                    flash(f"E-mail de teste enviado para {destino}. "
                          "Confira também a caixa de spam.", "success")
                else:
                    flash(f"Falha no envio: {erro}", "danger")
            db.registrar_log(session["uid"], session["nome"], "teste_email",
                             detalhe=destino)

        return redirect(url_for("admin.email"))

    ativos = {chave: mailer.evento_ativo(chave) for chave in mailer.EVENTOS}
    sem_email = db.query("""SELECT nome, usuario, perfil FROM usuarios
                            WHERE ativo=TRUE AND (email IS NULL OR email='')
                            ORDER BY perfil, nome""")
    com_email = db.scalar("""SELECT COUNT(*) AS n FROM usuarios
                             WHERE ativo=TRUE AND email IS NOT NULL AND email<>''""")
    return render_template("admin/email.html", st=mailer.status(),
                           cfg=email_config.configuracao(),
                           PROVEDORES=email_config.PROVEDORES,
                           EVENTOS=mailer.EVENTOS, ativos=ativos,
                           sem_email=sem_email, com_email=com_email,
                           url_sistema=request.host_url.rstrip("/"))


# ══════════════════════════════════════════════════════════════════
#  PARÂMETROS E AUDITORIA
# ══════════════════════════════════════════════════════════════════
@bp.route("/parametros", methods=["GET", "POST"])
@exige("admin")
def parametros():
    if request.method == "POST":
        for chave, valor in request.form.items():
            if chave.startswith("p_"):
                db.executar("""INSERT INTO parametros (chave, valor) VALUES (%s,%s)
                               ON CONFLICT (chave) DO UPDATE SET valor=EXCLUDED.valor""",
                            (chave[2:], valor.strip()))
        flash("Parâmetros salvos.", "success")
        return redirect(url_for("admin.parametros"))
    itens = db.query("SELECT * FROM parametros "
                     "WHERE chave NOT LIKE 'email%%' ORDER BY chave")
    return render_template("admin/parametros.html", itens=itens)


@bp.route("/auditoria")
@exige("admin")
def auditoria():
    itens = db.query("SELECT * FROM log_auditoria ORDER BY criado_em DESC LIMIT 500")
    return render_template("admin/auditoria.html", itens=itens)
