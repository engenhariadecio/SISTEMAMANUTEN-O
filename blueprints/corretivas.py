"""
MÓDULO — MANUTENÇÕES CORRETIVAS
Abertura de OS, fila por criticidade, cronômetro, apontamentos,
solicitação de peças, conclusão com defeito/causa e aprovação do solicitante.
"""
from datetime import datetime

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, session, Response, jsonify, abort)

import db
import mailer
from auth import pode, exige, EXECUCAO

bp = Blueprint("os", __name__, url_prefix="/os")

# Tipos de intervalo do cronômetro
TIPOS_TEMPO = {
    "trabalho": "Em execução",
    "pausa": "Pausada",
    "cafe": "Pausa para café",
    "almoco": "Almoço",
    "laboral": "Ginástica laboral",
    "reuniao": "Reunião",
    "treinamento": "Treinamento",
    "banheiro": "Pausa pessoal",
    "fim_turno": "Fim de turno",
    "outra_os": "Atendendo outra OS",
    "aguardando_peca": "Aguardando peça",
    "aguardando_terceiro": "Aguardando terceiro",
    "aguardando_producao": "Aguardando liberação da produção",
}

# Motivos oferecidos ao manutentor ao pausar: (chave, rótulo, ícone)
MOTIVOS_PAUSA = [
    ("cafe", "Café", "cup-hot-fill"),
    ("almoco", "Almoço", "egg-fried"),
    ("laboral", "Ginástica laboral", "person-arms-up"),
    ("banheiro", "Pausa pessoal", "person-walking"),
    ("reuniao", "Reunião", "people-fill"),
    ("treinamento", "Treinamento", "mortarboard-fill"),
    ("outra_os", "Atendendo outra OS", "arrow-left-right"),
    ("aguardando_peca", "Aguardando peça", "box-seam"),
    ("aguardando_terceiro", "Aguardando terceiro", "truck"),
    ("aguardando_producao", "Aguardando a produção liberar", "hourglass-split"),
    ("fim_turno", "Fim de turno", "moon-stars-fill"),
    ("pausa", "Outro motivo", "three-dots"),
]

# Situações consideradas "em aberto" na fila e nos filtros
ABERTAS = ("aberta", "atribuida", "em_andamento", "pausada",
           "aguardando_peca", "reprovada")

STATUS_LABEL = {
    "aberta": "Aguardando triagem",
    "atribuida": "Atribuída",
    "em_andamento": "Em andamento",
    "pausada": "Pausada",
    "aguardando_peca": "Aguardando peça",
    "aguardando_aprovacao": "Aguardando aprovação",
    "concluida": "Concluída",
    "reprovada": "Reprovada",
    "cancelada": "Cancelada",
}


# ══════════════════════════════════════════════════════════════════
#  LISTAGEM / FILA
# ══════════════════════════════════════════════════════════════════
@bp.route("/")
def lista():
    status = request.args.get("status", "abertas")
    equip = request.args.get("equipamento", "")
    crit = request.args.get("criticidade", "")
    busca = request.args.get("q", "").strip()
    resp = request.args.get("responsavel", "")

    where = ["1=1"]
    params = []

    if status == "abertas":
        where.append("o.status = ANY(%s)")
        params.append(list(ABERTAS))
    elif status == "triagem":
        where.append("o.status='aberta' AND o.responsavel_id IS NULL")
    elif status == "aprovacao":
        where.append("o.status='aguardando_aprovacao'")
    elif status and status != "todas":
        where.append("o.status=%s")
        params.append(status)

    # Solicitante vê o que abriu; manutentor vê o que lhe foi atribuído
    if session.get("perfil") == "solicitante":
        where.append("o.solicitante_id=%s")
        params.append(session["uid"])
    elif session.get("perfil") == "manutentor":
        where.append("(o.responsavel_id=%s OR o.solicitante_id=%s)")
        params += [session["uid"], session["uid"]]

    if equip:
        where.append("o.equipamento_id=%s")
        params.append(equip)
    if crit:
        where.append("o.criticidade=%s")
        params.append(crit)
    if resp:
        where.append("o.responsavel_id=%s")
        params.append(resp)
    if busca:
        where.append("(o.descricao_problema ILIKE %s OR e.nome ILIKE %s OR CAST(o.numero AS TEXT)=%s)")
        params += [f"%{busca}%", f"%{busca}%", busca]

    ordens = db.query(f"""
        SELECT o.*, e.codigo AS eq_codigo, e.nome AS eq_nome,
               s.nome AS solicitante, r.nome AS responsavel,
               ct.nome AS setor
        FROM ordens_servico o
        LEFT JOIN equipamentos e ON e.id=o.equipamento_id
        LEFT JOIN usuarios s ON s.id=o.solicitante_id
        LEFT JOIN usuarios r ON r.id=o.responsavel_id
        LEFT JOIN centros_trabalho ct ON ct.id=o.centro_trabalho_id
        WHERE {' AND '.join(where)}
        ORDER BY CASE o.status WHEN 'em_andamento' THEN 0 ELSE 1 END,
                 {db.ordem_crit('o.criticidade')},
                 o.maquina_parada DESC, o.data_abertura DESC
        LIMIT 400""", params)

    equipamentos = db.query(
        "SELECT id, codigo, nome FROM equipamentos WHERE ativo=TRUE ORDER BY codigo")
    manutentores = db.query(
        "SELECT id, nome FROM usuarios WHERE ativo=TRUE AND perfil IN "
        "('manutentor','lider','supervisao','admin') ORDER BY nome")

    return render_template("os/lista.html", ordens=ordens, equipamentos=equipamentos,
                           manutentores=manutentores, status=status,
                           STATUS_LABEL=STATUS_LABEL,
                           filtros={"equipamento": equip, "criticidade": crit,
                                    "q": busca, "responsavel": resp})


# ══════════════════════════════════════════════════════════════════
#  ABERTURA
# ══════════════════════════════════════════════════════════════════
@bp.route("/nova", methods=["GET", "POST"])
def nova():
    if request.method == "POST":
        ct_id = request.form.get("centro_trabalho_id") or None
        eq_id = request.form.get("equipamento_id") or None
        eq_outro = request.form.get("equipamento_outro", "").strip()
        descricao = request.form.get("descricao_problema", "").strip()
        tipo = request.form.get("tipo", "Industrial")
        est_id = request.form.get("estabelecimento_id") or None
        parada = bool(request.form.get("maquina_parada"))

        if not descricao:
            flash("Descreva o problema encontrado.", "warning")
            return redirect(url_for("os.nova"))
        if not eq_id and not eq_outro:
            flash("Selecione o equipamento ou informe em 'Outro'.", "warning")
            return redirect(url_for("os.nova"))

        # Criticidade herdada do equipamento
        crit = "C"
        if eq_id:
            crit = db.scalar("SELECT criticidade FROM equipamentos WHERE id=%s",
                             (eq_id,), default="C")
        if parada:
            crit = db.escalar_criticidade(crit)  # máquina parada sobe um nível na fila

        numero = db.proximo_numero("ordens_servico")
        os_id = db.inserir("""
            INSERT INTO ordens_servico
              (numero, tipo_manutencao, tipo, estabelecimento_id, centro_trabalho_id,
               equipamento_id, equipamento_outro, descricao_problema, solicitante_id,
               criticidade, maquina_parada, status, origem)
            VALUES (%s,'corretiva',%s,%s,%s,%s,%s,%s,%s,%s,%s,'aberta','manual')
            RETURNING id""",
            (numero, tipo, est_id, ct_id, eq_id, eq_outro or None, descricao,
             session["uid"], crit, parada))

        _apontar(os_id, "abertura", f"OS aberta por {session['nome']}.")

        # Se marcou máquina parada, sinaliza no cadastro do equipamento
        if parada and eq_id:
            db.executar("UPDATE equipamentos SET status='parado' WHERE id=%s", (eq_id,))

        _salvar_anexos(os_id, request.files.getlist("anexos"))

        link = url_for("os.detalhe", os_id=os_id)
        db.notificar_perfis(("lider", "supervisao", "admin"),
                            f"Nova OS #{numero} aguardando triagem",
                            descricao[:120], link)

        eq_txt = _texto_equipamento(eq_id, eq_outro)
        mailer.avisar(
            "os_aberta",
            mailer.emails_dos_perfis(("lider", "supervisao")),
            assunto=f"[Manutenção] Nova OS #{numero} para distribuir — {eq_txt}",
            titulo=f"Nova OS #{numero} aguardando triagem",
            subtitulo=f"Aberta por {session['nome']} · escolha o manutentor",
            mensagem=descricao,
            itens=[("Equipamento", eq_txt),
                   ("Criticidade", _nome_criticidade(crit)),
                   ("Tipo", tipo),
                   ("Local", _texto_local(est_id, ct_id)),
                   ("Máquina parada", "SIM — produção interrompida" if parada else "Não"),
                   ("Solicitante", session["nome"]),
                   ("Aberta em", db.agora().strftime("%d/%m/%Y às %H:%M"))],
            botao=("Distribuir esta OS agora",
                   mailer.url(url_for("os.triagem"))),
            rodape="A OS fica parada até que a liderança escolha o manutentor.")

        db.registrar_log(session["uid"], session["nome"], "abrir_os", "ordens_servico", os_id)
        flash(f"Ordem de Serviço #{numero} aberta com sucesso.", "success")
        return redirect(url_for("os.detalhe", os_id=os_id))

    centros = db.query(f"""SELECT ct.*, e.nome AS estab FROM centros_trabalho ct
                          LEFT JOIN estabelecimentos e ON e.id=ct.estabelecimento_id
                          WHERE ct.ativo=TRUE ORDER BY ct.nome""")
    estabs = db.query("SELECT * FROM estabelecimentos WHERE ativo=TRUE ORDER BY nome")
    equipamentos = db.query("""SELECT id, codigo, nome, criticidade, centro_trabalho_id, tipo
                               FROM equipamentos WHERE ativo=TRUE ORDER BY codigo""")
    return render_template("os/nova.html", centros=centros, estabs=estabs,
                           equipamentos=equipamentos)


@bp.route("/intervencao", methods=["GET", "POST"])
@exige("os_executar")
def intervencao():
    """Intervenção automática: o próprio manutentor abre, executa e fecha."""
    if request.method == "POST":
        eq_id = request.form.get("equipamento_id") or None
        sintoma = request.form.get("sintoma", "").strip()
        if not eq_id or not sintoma:
            flash("Informe o equipamento e o sintoma.", "warning")
            return redirect(url_for("os.intervencao"))

        crit = db.scalar("SELECT criticidade FROM equipamentos WHERE id=%s", (eq_id,), default="C")
        numero = db.proximo_numero("ordens_servico")
        os_id = db.inserir("""
            INSERT INTO ordens_servico
              (numero, tipo_manutencao, equipamento_id, descricao_problema, solicitante_id,
               responsavel_id, criticidade, status, origem, data_inicio)
            VALUES (%s,'intervencao',%s,%s,%s,%s,%s,'em_andamento','intervencao',NOW())
            RETURNING id""",
            (numero, eq_id, sintoma, session["uid"], session["uid"], crit))
        _apontar(os_id, "abertura", "Intervenção automática iniciada.")
        _iniciar_tempo(os_id, "trabalho")
        flash(f"Intervenção #{numero} iniciada. O cronômetro está rodando.", "success")
        return redirect(url_for("os.detalhe", os_id=os_id))

    equipamentos = db.query(
        "SELECT id, codigo, nome FROM equipamentos WHERE ativo=TRUE ORDER BY codigo")
    return render_template("os/intervencao.html", equipamentos=equipamentos)


# ══════════════════════════════════════════════════════════════════
#  DETALHE
# ══════════════════════════════════════════════════════════════════
@bp.route("/<int:os_id>")
def detalhe(os_id):
    o = db.um("""
        SELECT o.*, e.codigo AS eq_codigo, e.nome AS eq_nome, e.criticidade AS eq_crit,
               s.nome AS solicitante, s.email AS solicitante_email,
               r.nome AS responsavel, ct.nome AS setor, est.nome AS estabelecimento,
               d.nome AS defeito, c.nome AS causa
        FROM ordens_servico o
        LEFT JOIN equipamentos e ON e.id=o.equipamento_id
        LEFT JOIN usuarios s ON s.id=o.solicitante_id
        LEFT JOIN usuarios r ON r.id=o.responsavel_id
        LEFT JOIN centros_trabalho ct ON ct.id=o.centro_trabalho_id
        LEFT JOIN estabelecimentos est ON est.id=o.estabelecimento_id
        LEFT JOIN defeitos d ON d.id=o.defeito_id
        LEFT JOIN causas c ON c.id=o.causa_id
        WHERE o.id=%s""", (os_id,))
    if not o:
        abort(404)

    # Solicitante só vê as próprias
    if session.get("perfil") == "solicitante" and o["solicitante_id"] != session["uid"]:
        flash("Você só pode acessar as OS que abriu.", "warning")
        return redirect(url_for("os.lista"))

    apontamentos = db.query("""
        SELECT a.*, u.nome AS usuario FROM os_apontamentos a
        LEFT JOIN usuarios u ON u.id=a.usuario_id
        WHERE a.os_id=%s ORDER BY a.criado_em""", (os_id,))

    tempos = db.query("""
        SELECT t.*, u.nome AS usuario FROM os_tempos t
        LEFT JOIN usuarios u ON u.id=t.usuario_id
        WHERE t.os_id=%s ORDER BY t.inicio""", (os_id,))

    aberto = db.um("SELECT * FROM os_tempos WHERE os_id=%s AND fim IS NULL "
                   "ORDER BY inicio DESC LIMIT 1", (os_id,))

    materiais = db.query("SELECT * FROM os_materiais WHERE os_id=%s ORDER BY criado_em", (os_id,))
    anexos = db.query("SELECT id, nome, mime, criado_em FROM os_anexos WHERE os_id=%s", (os_id,))
    solicitacoes = db.query(
        "SELECT * FROM solicitacoes_material WHERE os_id=%s ORDER BY criado_em", (os_id,))

    manutentores = db.query(
        "SELECT id, nome FROM usuarios WHERE ativo=TRUE AND perfil IN "
        "('manutentor','lider','supervisao','admin') ORDER BY nome")
    defeitos = db.query("SELECT * FROM defeitos WHERE ativo=TRUE ORDER BY nome")
    causas = db.query("SELECT * FROM causas WHERE ativo=TRUE ORDER BY nome")

    # Totais por tipo de tempo
    totais = {}
    for t in tempos:
        dur = t["duracao_seg"] or 0
        totais[t["tipo"]] = totais.get(t["tipo"], 0) + dur

    return render_template("os/detalhe.html", o=o, apontamentos=apontamentos, tempos=tempos,
                           aberto=aberto, materiais=materiais, anexos=anexos,
                           solicitacoes=solicitacoes, manutentores=manutentores,
                           defeitos=defeitos, causas=causas, totais=totais,
                           TIPOS_TEMPO=TIPOS_TEMPO, MOTIVOS_PAUSA=MOTIVOS_PAUSA,
                           STATUS_LABEL=STATUS_LABEL,
                           triagem_obrigatoria=triagem_obrigatoria(),
                           materiais_pendentes=_materiais_pendentes(os_id))


# ══════════════════════════════════════════════════════════════════
#  CRONÔMETRO E AÇÕES DO MANUTENTOR
# ══════════════════════════════════════════════════════════════════
def _apontar(os_id, tipo, descricao):
    db.executar("""INSERT INTO os_apontamentos (os_id, usuario_id, tipo, descricao)
                   VALUES (%s,%s,%s,%s)""", (os_id, session.get("uid"), tipo, descricao))


def _fechar_tempo(os_id):
    """Fecha qualquer intervalo aberto e acumula a duração."""
    aberto = db.um("SELECT * FROM os_tempos WHERE os_id=%s AND fim IS NULL "
                   "ORDER BY inicio DESC LIMIT 1", (os_id,))
    if not aberto:
        return
    db.executar("""UPDATE os_tempos
                   SET fim=NOW(), duracao_seg=EXTRACT(EPOCH FROM (NOW()-inicio))::INT
                   WHERE id=%s""", (aberto["id"],))
    if aberto["tipo"] == "trabalho":
        dur = db.scalar("SELECT duracao_seg AS d FROM os_tempos WHERE id=%s",
                        (aberto["id"],), default=0)
        db.executar("UPDATE ordens_servico SET tempo_trabalho_seg=tempo_trabalho_seg+%s "
                    "WHERE id=%s", (int(dur or 0), os_id))


def _iniciar_tempo(os_id, tipo):
    _fechar_tempo(os_id)
    db.executar("INSERT INTO os_tempos (os_id, usuario_id, tipo) VALUES (%s,%s,%s)",
                (os_id, session.get("uid"), tipo))


@bp.route("/<int:os_id>/assumir", methods=["POST"])
@exige("os_triagem")
def assumir(os_id):
    """Triagem: a liderança escolhe qual manutentor vai atender."""
    resp = request.form.get("responsavel_id") or session["uid"]
    nome = db.scalar("SELECT nome FROM usuarios WHERE id=%s", (resp,), default="")
    db.executar("""UPDATE ordens_servico SET responsavel_id=%s,
                   status = CASE WHEN status='aberta' THEN 'atribuida' ELSE status END
                   WHERE id=%s""", (resp, os_id))
    _apontar(os_id, "atribuicao",
             f"{session['nome']} distribuiu a OS para {nome}.")
    o = db.um("""SELECT o.*, e.codigo AS eq_codigo, e.nome AS eq_nome,
                        s.nome AS solicitante
                 FROM ordens_servico o
                 LEFT JOIN equipamentos e ON e.id=o.equipamento_id
                 LEFT JOIN usuarios s ON s.id=o.solicitante_id
                 WHERE o.id=%s""", (os_id,))
    link = url_for("os.detalhe", os_id=os_id)
    db.notificar(o["solicitante_id"], f"OS #{o['numero']} — manutentor designado",
                 f"{nome} assumiu sua ordem de serviço.", link)
    db.notificar(int(resp), f"OS #{o['numero']} atribuída a você",
                 o["descricao_problema"][:120], link)

    mailer.avisar(
        "os_atribuida", mailer.emails_dos_usuarios([resp]),
        assunto=f"[Manutenção] OS #{o['numero']} atribuída a você",
        titulo=f"OS #{o['numero']} atribuída a você",
        subtitulo=_eq_txt(o),
        mensagem=o["descricao_problema"],
        itens=[("Equipamento", _eq_txt(o)),
               ("Criticidade", _nome_criticidade(o["criticidade"])),
               ("Solicitante", o["solicitante"]),
               ("Aberta em", db.fmt(o["data_abertura"])),
               ("Máquina parada", "SIM" if o["maquina_parada"] else "Não")],
        botao=("Iniciar o atendimento", mailer.url(link) + "#cronometro"))
    flash(f"OS atribuída a {nome}, que já foi notificado.", "success")
    destino = request.form.get("voltar")
    return redirect(url_for("os.triagem") if destino == "triagem"
                    else url_for("os.detalhe", os_id=os_id))


@bp.route("/<int:os_id>/assumir-para-mim", methods=["POST"])
@exige("os_executar")
def assumir_para_mim(os_id):
    """O manutentor puxa para si uma OS ainda sem responsável."""
    o = db.um("SELECT * FROM ordens_servico WHERE id=%s", (os_id,))
    if not o:
        abort(404)
    if o["responsavel_id"]:
        flash("Esta OS já tem um responsável.", "warning")
        return redirect(url_for("os.detalhe", os_id=os_id))
    if triagem_obrigatoria() and session.get("perfil") == "manutentor":
        flash("A liderança precisa distribuir esta OS antes.", "warning")
        return redirect(url_for("os.detalhe", os_id=os_id))

    db.executar("""UPDATE ordens_servico SET responsavel_id=%s,
                   status = CASE WHEN status='aberta' THEN 'atribuida' ELSE status END
                   WHERE id=%s""", (session["uid"], os_id))
    _apontar(os_id, "atribuicao", f"{session['nome']} assumiu a OS.")
    db.notificar(o["solicitante_id"], f"OS #{o['numero']} — manutentor designado",
                 f"{session['nome']} assumiu sua ordem de serviço.",
                 url_for("os.detalhe", os_id=os_id))
    flash("Você assumiu esta OS. Pode iniciar o atendimento.", "success")
    return redirect(url_for("os.detalhe", os_id=os_id))


# ══════════════════════════════════════════════════════════════════
#  TRIAGEM — tela do líder
# ══════════════════════════════════════════════════════════════════
@bp.route("/triagem")
@exige("os_triagem")
def triagem():
    """OS abertas pelos solicitantes, aguardando a escolha do manutentor."""
    fila = db.query(f"""
        SELECT o.*, e.codigo AS eq_codigo, e.nome AS eq_nome, e.status AS eq_status,
               s.nome AS solicitante, s.email AS solicitante_email,
               ct.nome AS setor, est.nome AS estabelecimento,
               EXTRACT(EPOCH FROM (NOW() - o.data_abertura))/3600 AS horas_espera,
               (SELECT COUNT(*) FROM os_anexos a WHERE a.os_id=o.id) AS anexos
        FROM ordens_servico o
        LEFT JOIN equipamentos e ON e.id=o.equipamento_id
        LEFT JOIN usuarios s ON s.id=o.solicitante_id
        LEFT JOIN centros_trabalho ct ON ct.id=o.centro_trabalho_id
        LEFT JOIN estabelecimentos est ON est.id=o.estabelecimento_id
        WHERE o.status='aberta' AND o.responsavel_id IS NULL
        ORDER BY {db.ordem_crit('o.criticidade')},
                 o.maquina_parada DESC, o.data_abertura""")

    # Carga atual de cada manutentor, para ajudar a decidir
    equipe = db.query(f"""
        SELECT u.id, u.nome,
               COUNT(o.id) FILTER (WHERE o.status = ANY(%s)) AS em_aberto,
               COUNT(o.id) FILTER (WHERE o.status='em_andamento') AS em_execucao
        FROM usuarios u
        LEFT JOIN ordens_servico o ON o.responsavel_id=u.id
        WHERE u.ativo=TRUE AND u.perfil IN ('manutentor','lider')
        GROUP BY u.id, u.nome ORDER BY em_aberto, u.nome""", (list(ABERTAS),))

    return render_template("os/triagem.html", fila=fila, equipe=equipe,
                           STATUS_LABEL=STATUS_LABEL)


def _materiais_pendentes(os_id):
    """Pedidos de peça daquela OS que o analista ainda não liberou."""
    from blueprints.solicitacoes import ATENDIDAS
    return db.scalar("""SELECT COUNT(*) AS n FROM solicitacoes_material
                        WHERE os_id=%s AND situacao <> ALL(%s)""",
                     (os_id, list(ATENDIDAS)), default=0)


def triagem_obrigatoria():
    """Quando desligada, o manutentor pode puxar uma OS ainda não distribuída."""
    return str(db.scalar("SELECT valor FROM parametros WHERE chave='triagem_obrigatoria'",
                         default="1") or "1") == "1"


def _responsavel_ou_gestao(o):
    """Só o manutentor designado — ou a liderança — mexe na OS."""
    if session.get("perfil") in ("admin", "supervisao", "lider"):
        return True
    return o and o["responsavel_id"] == session.get("uid")


@bp.route("/<int:os_id>/acao/<acao>", methods=["POST"])
@exige("os_executar")
def acao(os_id, acao):
    o = db.um("SELECT * FROM ordens_servico WHERE id=%s", (os_id,))
    if not o:
        abort(404)
    if not _responsavel_ou_gestao(o):
        flash("Esta OS está atribuída a outro manutentor.", "warning")
        return redirect(url_for("os.detalhe", os_id=os_id))
    if o["responsavel_id"] is None:
        if triagem_obrigatoria() and session.get("perfil") == "manutentor":
            flash("A OS ainda não foi distribuída pela liderança.", "warning")
            return redirect(url_for("os.detalhe", os_id=os_id))
        # Sem triagem obrigatória (ou sendo gestão), quem age assume a OS
        db.executar("""UPDATE ordens_servico SET responsavel_id=%s,
                       status = CASE WHEN status='aberta' THEN 'atribuida' ELSE status END
                       WHERE id=%s""", (session["uid"], os_id))
        _apontar(os_id, "atribuicao", f"{session['nome']} assumiu a OS.")
        o = db.um("SELECT * FROM ordens_servico WHERE id=%s", (os_id,))

    if acao in ("iniciar", "retomar"):
        pendentes = _materiais_pendentes(os_id)
        if pendentes:
            flash(f"Há {pendentes} material(is) aguardando liberação do analista. "
                  "A OS libera assim que todos forem entregues.", "warning")
            return redirect(url_for("os.detalhe", os_id=os_id))

    if acao == "iniciar":
        if not o["data_inicio"]:
            db.executar("UPDATE ordens_servico SET data_inicio=NOW() WHERE id=%s", (os_id,))
        db.executar("UPDATE ordens_servico SET status='em_andamento' WHERE id=%s", (os_id,))
        _iniciar_tempo(os_id, "trabalho")
        _apontar(os_id, "inicio", "Execução iniciada.")
        db.notificar(o["solicitante_id"], f"OS #{o['numero']} iniciada",
                     f"{session['nome']} começou o atendimento.",
                     url_for("os.detalhe", os_id=os_id))

    elif acao in ("pausar", "almoco", "aguardando_peca"):
        if acao == "aguardando_peca":
            tipo = "aguardando_peca"
        elif acao == "almoco":
            tipo = "almoco"
        else:
            tipo = request.form.get("motivo") or "pausa"
            if tipo not in TIPOS_TEMPO:
                tipo = "pausa"

        novo_status = "aguardando_peca" if tipo.startswith("aguardando") else "pausada"
        db.executar("UPDATE ordens_servico SET status=%s WHERE id=%s", (novo_status, os_id))
        _iniciar_tempo(os_id, tipo)

        observacao = request.form.get("observacao", "").strip()
        texto = TIPOS_TEMPO.get(tipo, "Pausada")
        _apontar(os_id, tipo, f"{texto}." + (f" {observacao}" if observacao else ""))

        if tipo == "aguardando_peca":
            db.notificar(o["solicitante_id"], f"OS #{o['numero']} aguardando peça",
                         "A execução está pausada até a chegada do material.",
                         url_for("os.detalhe", os_id=os_id))

    elif acao == "retomar":
        db.executar("UPDATE ordens_servico SET status='em_andamento' WHERE id=%s", (os_id,))
        _iniciar_tempo(os_id, "trabalho")
        _apontar(os_id, "retomada", "Execução retomada.")

    elif acao == "cancelar":
        _fechar_tempo(os_id)
        motivo = request.form.get("motivo", "").strip()
        db.executar("UPDATE ordens_servico SET status='cancelada' WHERE id=%s", (os_id,))
        _apontar(os_id, "cancelamento", f"OS cancelada. {motivo}")

    return redirect(url_for("os.detalhe", os_id=os_id))


@bp.route("/<int:os_id>/comentar", methods=["POST"])
def comentar(os_id):
    texto = request.form.get("comentario", "").strip()
    if texto:
        _apontar(os_id, "comentario", texto)
        o = db.um("SELECT numero, solicitante_id, responsavel_id FROM ordens_servico WHERE id=%s",
                  (os_id,))
        # avisa a outra parte
        destino = o["responsavel_id"] if session["uid"] == o["solicitante_id"] else o["solicitante_id"]
        db.notificar(destino, f"OS #{o['numero']} — novo comentário",
                     texto[:120], url_for("os.detalhe", os_id=os_id))
        flash("Comentário registrado.", "success")
    return redirect(url_for("os.detalhe", os_id=os_id))


@bp.route("/<int:os_id>/anexar", methods=["POST"])
def anexar(os_id):
    n = _salvar_anexos(os_id, request.files.getlist("anexos"))
    flash(f"{n} arquivo(s) anexado(s).", "success" if n else "warning")
    return redirect(url_for("os.detalhe", os_id=os_id))


def _salvar_anexos(os_id, arquivos):
    salvos = 0
    for f in arquivos or []:
        if not f or not f.filename:
            continue
        dados = f.read()
        if not dados:
            continue
        db.executar("""INSERT INTO os_anexos (os_id, nome, mime, dados, usuario_id)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (os_id, f.filename, f.mimetype, psycopg2_bytes(dados), session.get("uid")))
        salvos += 1
    return salvos


def psycopg2_bytes(dados):
    import psycopg2
    return psycopg2.Binary(dados)


@bp.route("/anexo/<int:anexo_id>")
def baixar_anexo(anexo_id):
    a = db.um("SELECT * FROM os_anexos WHERE id=%s", (anexo_id,))
    if not a:
        abort(404)
    return Response(bytes(a["dados"]), mimetype=a["mime"] or "application/octet-stream",
                    headers={"Content-Disposition": f'inline; filename="{a["nome"]}"'})


# ══════════════════════════════════════════════════════════════════
#  MATERIAIS NA OS
# ══════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════
#  PEDIDO DE PEÇA — fluxo único
#  O manutentor informa código e quantidade. O sistema decide:
#   • há saldo no NLAG  → dá baixa na hora e registra o consumo na OS;
#   • não há (ou é parcial) → gera solicitação para o Analista de Materiais.
# ══════════════════════════════════════════════════════════════════
@bp.route("/<int:os_id>/material", methods=["POST"])
@exige("os_executar")
def add_material(os_id):
    o = db.um("SELECT * FROM ordens_servico WHERE id=%s", (os_id,))
    if not o:
        abort(404)
    if not _responsavel_ou_gestao(o):
        flash("Esta OS está atribuída a outro manutentor.", "warning")
        return redirect(url_for("os.detalhe", os_id=os_id))

    codigo = request.form.get("codigo", "").strip().upper()
    descricao = request.form.get("descricao", "").strip()
    pausar = request.form.get("pausar") == "1"
    try:
        qtd = float(request.form.get("quantidade") or 0)
    except ValueError:
        qtd = 0
    if qtd <= 0:
        flash("Informe uma quantidade válida.", "warning")
        return redirect(url_for("os.detalhe", os_id=os_id))
    if not codigo and not descricao:
        flash("Informe o código ou a descrição da peça.", "warning")
        return redirect(url_for("os.detalhe", os_id=os_id))

    mat = db.um("SELECT * FROM materiais WHERE codigo=%s AND ativo=TRUE",
                (codigo,)) if codigo else None
    if mat and not descricao:
        descricao = mat["descricao"]

    # ── Saldo apenas informativo: quem entrega a peça é o analista ──
    disponivel = 0.0
    if mat:
        disponivel = (max(db.saldo_material(mat["codigo"]), 0.0)
                      if mat["tipo"] == "NLAG" else float(mat["saldo_sap"] or 0))

    # ── Todo pedido vira solicitação, mesmo havendo saldo ──
    if True:
        if not mat:
            tipo_sm = "Cadastro"
            obs = "Peça sem cadastro no sistema. Solicitada pelo manutentor durante a OS."
        elif mat["tipo"] == "NLAG":
            tipo_sm = "Estoque NLAG"
            obs = (f"Saldo no NLAG no momento do pedido: {disponivel:g} "
                   f"{mat['unidade']}." if disponivel >= qtd else
                   f"Saldo NLAG insuficiente no momento do pedido "
                   f"(disponível {disponivel:g}, necessário {qtd:g}).")
        else:
            tipo_sm = "HIBE/ERSA"
            obs = (f"Material {mat['tipo']} — saldo do SAP na última importação: "
                   f"{disponivel:g}.")
        faltante = qtd

        numero = db.proximo_numero("solicitacoes_material")
        eq = o["equipamento_id"] and db.scalar(
            "SELECT codigo FROM equipamentos WHERE id=%s", (o["equipamento_id"],), default="")
        sid = db.inserir("""
            INSERT INTO solicitacoes_material
              (numero, solicitante_id, codigo, descricao, tipo, quantidade,
               observacoes, os_id, dt_solicitacao, situacao)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,CURRENT_DATE,'Solicitado') RETURNING id""",
            (numero, session["uid"], mat["codigo"] if mat else None,
             descricao or codigo, tipo_sm, faltante,
             f"{obs} OS #{o['numero']}" + (f" — equipamento {eq}." if eq else "."),
             os_id))
        db.executar("""INSERT INTO solicitacao_historico
                       (solicitacao_id, usuario_id, situacao, comentario)
                       VALUES (%s,%s,'Solicitado','Solicitada pelo manutentor dentro da OS.')""",
                    (sid, session["uid"]))
        _apontar(os_id, "material",
                 f"Material requisitado ao analista: {qtd:g} de "
                 f"{descricao or codigo} — SM #{numero}."
                 + (f" Saldo no depósito: {disponivel:g}." if mat else
                    " Peça sem cadastro."))

        link_sm = url_for("sol.detalhe", sid=sid)
        db.notificar_perfis(("analista",),
                            f"Peça solicitada na OS #{o['numero']} — SM #{numero}",
                            f"{descricao or codigo} · qtd {faltante:g} · "
                            f"{eq or 'sem equipamento'}", link_sm)
        mailer.avisar(
            "material_solicitado", mailer.emails_dos_perfis(("analista", "lider")),
            assunto=f"[Manutenção] SM #{numero} — peça solicitada na OS #{o['numero']}",
            titulo=f"Solicitação de material SM #{numero}",
            subtitulo=f"Aberta na OS #{o['numero']} por {session['nome']}",
            itens=[("Peça", descricao or codigo),
                   ("Código", mat["codigo"] if mat else "sem cadastro"),
                   ("Quantidade", f"{faltante:g}"),
                   ("Tipo", tipo_sm),
                   ("Equipamento", eq or "—"),
                   ("Motivo", obs)],
            botao=("Tratar esta solicitação", mailer.url(link_sm)))
        db.notificar(o["solicitante_id"], f"OS #{o['numero']} — material solicitado",
                     f"{descricao or codigo} foi solicitado ao almoxarifado.",
                     url_for("os.detalhe", os_id=os_id))

        if mat and disponivel >= qtd:
            flash(f"Pedido SM #{numero} enviado ao analista. Há saldo no depósito "
                  f"({disponivel:g} {mat['unidade']}) — a liberação deve ser rápida.",
                  "success")
        elif mat:
            flash(f"Pedido SM #{numero} enviado ao analista. Saldo atual: "
                  f"{disponivel:g} {mat['unidade']} — pode ser preciso comprar.",
                  "warning")
        else:
            flash(f"Pedido SM #{numero} enviado ao analista. A peça ainda não tem "
                  "cadastro e será cadastrada por ele.", "warning")

        # Sem a peça liberada a OS não anda — pausa o cronômetro
        if o["status"] == "em_andamento":
            db.executar("UPDATE ordens_servico SET status='aguardando_peca' WHERE id=%s",
                        (os_id,))
            _iniciar_tempo(os_id, "aguardando_peca")
            _apontar(os_id, "aguardando_peca", "OS pausada aguardando a chegada da peça.")

    _recalcular_custo(os_id)
    return redirect(url_for("os.detalhe", os_id=os_id))


def _avisar_estoque_minimo(material, saldo_novo):
    """Dispara o alerta ao analista quando a baixa cruza o estoque mínimo."""
    minimo = float(material["estoque_min"] or 0)
    if minimo and saldo_novo < minimo:
        sugestao = max(float(material["estoque_max"] or 0) - saldo_novo, minimo - saldo_novo)
        db.notificar_perfis(
            ("analista", "lider", "supervisao"),
            f"Estoque mínimo atingido — {material['codigo']}",
            f"{material['descricao']} — saldo {saldo_novo:g} {material['unidade']} "
            f"(mín. {minimo:g}). Sugestão de compra: {max(sugestao, 0):g}.",
            url_for("mat.alertas"))


@bp.route("/<int:os_id>/material/<int:mid>/remover", methods=["POST"])
@exige("os_executar")
def del_material(os_id, mid):
    db.executar("DELETE FROM os_materiais WHERE id=%s AND os_id=%s", (mid, os_id))
    _recalcular_custo(os_id)
    return redirect(url_for("os.detalhe", os_id=os_id))


def _recalcular_custo(os_id):
    custo = db.scalar("""SELECT COALESCE(SUM(quantidade*COALESCE(valor_unit,0)),0) AS c
                         FROM os_materiais WHERE os_id=%s""", (os_id,), default=0)
    hh = float(db.scalar("SELECT valor FROM parametros WHERE chave='custo_hh_padrao'",
                         default="45") or 45)
    tempo = db.scalar("SELECT tempo_trabalho_seg AS t FROM ordens_servico WHERE id=%s",
                      (os_id,), default=0)
    db.executar("UPDATE ordens_servico SET custo_pecas=%s, custo_hh=%s WHERE id=%s",
                (custo, round((tempo or 0) / 3600 * hh, 2), os_id))


# ══════════════════════════════════════════════════════════════════
#  CONCLUSÃO E APROVAÇÃO
# ══════════════════════════════════════════════════════════════════
@bp.route("/<int:os_id>/concluir", methods=["POST"])
@exige("os_executar")
def concluir(os_id):
    if not _responsavel_ou_gestao(_detalhe_os(os_id)):
        flash("Esta OS está atribuída a outro manutentor.", "warning")
        return redirect(url_for("os.detalhe", os_id=os_id))
    defeito = request.form.get("defeito_id") or None
    causa = request.form.get("causa_id") or None
    acao_txt = request.form.get("acao_realizada", "").strip()
    liberar = bool(request.form.get("liberar_equipamento"))

    if not defeito or not causa:
        flash("Informe o tipo de defeito e a causa antes de concluir.", "warning")
        return redirect(url_for("os.detalhe", os_id=os_id))
    if not acao_txt:
        flash("Descreva a ação realizada.", "warning")
        return redirect(url_for("os.detalhe", os_id=os_id))

    _fechar_tempo(os_id)
    db.executar("""UPDATE ordens_servico
                   SET status='aguardando_aprovacao', defeito_id=%s, causa_id=%s,
                       acao_realizada=%s, data_conclusao=NOW()
                   WHERE id=%s""", (defeito, causa, acao_txt, os_id))

    # Fotos, vídeos e relatórios do serviço executado
    n_anexos = _salvar_anexos(os_id, request.files.getlist("evidencias"))
    if n_anexos:
        _apontar(os_id, "material" if False else "comentario",
                 f"{n_anexos} evidência(s) anexada(s) na conclusão.")
    _recalcular_custo(os_id)
    _apontar(os_id, "conclusao", f"Serviço concluído: {acao_txt}")

    o = db.um("SELECT numero, solicitante_id, equipamento_id FROM ordens_servico WHERE id=%s",
              (os_id,))
    if liberar and o["equipamento_id"]:
        db.executar("UPDATE equipamentos SET status='operando' WHERE id=%s", (o["equipamento_id"],))

    link = url_for("os.detalhe", os_id=os_id)
    db.notificar(o["solicitante_id"], f"OS #{o['numero']} concluída — aprove ou reprove",
                 acao_txt[:150], link)
    n_evid = db.scalar("SELECT COUNT(*) AS n FROM os_anexos WHERE os_id=%s",
                       (os_id,), default=0)

    det = db.um("""SELECT o.*, e.codigo AS eq_codigo, e.nome AS eq_nome,
                          d.nome AS defeito, c.nome AS causa, r.nome AS responsavel
                   FROM ordens_servico o
                   LEFT JOIN equipamentos e ON e.id=o.equipamento_id
                   LEFT JOIN defeitos d ON d.id=o.defeito_id
                   LEFT JOIN causas c ON c.id=o.causa_id
                   LEFT JOIN usuarios r ON r.id=o.responsavel_id
                   WHERE o.id=%s""", (os_id,))
    mailer.avisar(
        "os_concluida", mailer.emails_dos_usuarios([o["solicitante_id"]]),
        assunto=f"[Manutenção] OS #{o['numero']} concluída — precisa da sua aprovação",
        titulo=f"OS #{o['numero']} concluída",
        subtitulo="Confirme se o problema foi resolvido",
        mensagem=acao_txt,
        itens=[("Equipamento", _eq_txt(det)),
               ("Problema relatado", det["descricao_problema"]),
               ("Executado por", det["responsavel"]),
               ("Tipo de defeito", det["defeito"]),
               ("Causa", det["causa"]),
               ("Tempo de reparo", _horas(det["tempo_trabalho_seg"])),
               ("Evidências anexadas", f"{n_evid} arquivo(s)" if n_evid else "—"),
               ("Concluída em", db.agora().strftime("%d/%m/%Y às %H:%M"))],
        botao=("Aprovar ou reprovar o serviço", mailer.url(link) + "#aprovacao"),
        rodape="Enquanto você não aprovar, a OS permanece pendente no sistema.")
    flash("OS concluída. Aguardando aprovação do solicitante.", "success")
    return redirect(url_for("os.detalhe", os_id=os_id))


@bp.route("/<int:os_id>/aprovar", methods=["POST"])
def aprovar(os_id):
    o = db.um("SELECT * FROM ordens_servico WHERE id=%s", (os_id,))
    if not o:
        abort(404)
    # Só o solicitante (ou gestão) aprova
    if o["solicitante_id"] != session["uid"] and session.get("perfil") not in \
            ("admin", "supervisao", "lider"):
        flash("Apenas o solicitante pode aprovar esta OS.", "warning")
        return redirect(url_for("os.detalhe", os_id=os_id))

    decisao = request.form.get("decisao")
    comentario = request.form.get("comentario", "").strip()

    if decisao == "aprovar":
        db.executar("""UPDATE ordens_servico SET status='concluida', aprovado=TRUE,
                       data_aprovacao=NOW() WHERE id=%s""", (os_id,))
        _apontar(os_id, "aprovacao", f"OS aprovada pelo solicitante. {comentario}")
        link = url_for("os.detalhe", os_id=os_id)
        db.notificar(o["responsavel_id"], f"OS #{o['numero']} aprovada",
                     "O solicitante aprovou o serviço. OS finalizada.", link)
        mailer.avisar(
            "os_aprovada", mailer.emails_dos_usuarios([o["responsavel_id"]]),
            assunto=f"[Manutenção] OS #{o['numero']} aprovada e finalizada",
            titulo=f"OS #{o['numero']} aprovada",
            subtitulo="O solicitante confirmou a solução",
            mensagem=comentario or "Serviço aprovado sem observações.",
            itens=[("Equipamento", _eq_txt(_detalhe_os(os_id))),
                   ("Aprovada por", session["nome"]),
                   ("Aprovada em", db.agora().strftime("%d/%m/%Y às %H:%M"))],
            botao=("Ver a OS", mailer.url(link)))
        flash("OS aprovada e finalizada.", "success")
    else:
        if not comentario:
            flash("Descreva no comentário o que continua ocorrendo.", "warning")
            return redirect(url_for("os.detalhe", os_id=os_id))
        db.executar("""UPDATE ordens_servico SET status='reprovada', aprovado=FALSE,
                       comentario_reprova=%s WHERE id=%s""", (comentario, os_id))
        _apontar(os_id, "reprovacao", f"OS REPROVADA: {comentario}")
        link = url_for("os.detalhe", os_id=os_id)
        db.notificar(o["responsavel_id"], f"OS #{o['numero']} reprovada",
                     comentario[:150], link)
        db.notificar_perfis(("lider", "supervisao"), f"OS #{o['numero']} reprovada",
                            comentario[:150], link)
        mailer.avisar(
            "os_reprovada",
            mailer.emails_dos_usuarios([o["responsavel_id"]])
            + mailer.emails_dos_perfis(("lider", "supervisao")),
            assunto=f"[Manutenção] OS #{o['numero']} REPROVADA pelo solicitante",
            titulo=f"OS #{o['numero']} reprovada",
            subtitulo="O problema não foi resolvido",
            mensagem=comentario,
            itens=[("Equipamento", _eq_txt(_detalhe_os(os_id))),
                   ("Reprovada por", session["nome"]),
                   ("Reprovada em", db.agora().strftime("%d/%m/%Y às %H:%M"))],
            botao=("Retomar o atendimento", mailer.url(link) + "#cronometro"),
            rodape="A OS voltou para a fila com a situação 'Reprovada'.")
        flash("OS reprovada — ela volta para a fila da manutenção.", "warning")
    return redirect(url_for("os.detalhe", os_id=os_id))


@bp.route("/<int:os_id>/reabrir", methods=["POST"])
@exige("os_executar")
def reabrir(os_id):
    db.executar("UPDATE ordens_servico SET status='em_andamento' WHERE id=%s", (os_id,))
    _iniciar_tempo(os_id, "trabalho")
    _apontar(os_id, "inicio", "OS reaberta para tratamento da reprovação.")
    return redirect(url_for("os.detalhe", os_id=os_id))


@bp.route("/tablet")
@exige("os_executar")
def tablet():
    """A tela única já se adapta ao tablet — mantido só para links antigos."""
    return redirect(url_for("os.lista"))


# ══════════════════════════════════════════════════════════════════
#  APOIO AOS AVISOS POR E-MAIL
# ══════════════════════════════════════════════════════════════════
def _detalhe_os(os_id):
    return db.um("""SELECT o.*, e.codigo AS eq_codigo, e.nome AS eq_nome
                    FROM ordens_servico o
                    LEFT JOIN equipamentos e ON e.id=o.equipamento_id
                    WHERE o.id=%s""", (os_id,))


def _eq_txt(o):
    """Texto legível do equipamento a partir de um registro de OS."""
    if not o:
        return "—"
    if o.get("eq_codigo"):
        return f"{o['eq_codigo']} — {o.get('eq_nome') or ''}".strip(" —")
    return o.get("equipamento_outro") or "Equipamento não cadastrado"


def _texto_equipamento(eq_id, eq_outro):
    if eq_id:
        e = db.um("SELECT codigo, nome FROM equipamentos WHERE id=%s", (eq_id,))
        if e:
            return f"{e['codigo']} — {e['nome']}"
    return eq_outro or "Equipamento não cadastrado"


def _texto_local(est_id, ct_id):
    partes = []
    if est_id:
        partes.append(db.scalar("SELECT nome FROM estabelecimentos WHERE id=%s",
                                (est_id,), default="") or "")
    if ct_id:
        partes.append(db.scalar("SELECT nome FROM centros_trabalho WHERE id=%s",
                                (ct_id,), default="") or "")
    return " · ".join([p for p in partes if p]) or "—"


def _nome_criticidade(codigo):
    n = db.um("SELECT nome FROM criticidades WHERE codigo=%s", (codigo,))
    return f"{codigo} — {n['nome']}" if n else codigo


def _horas(segundos):
    s = int(segundos or 0)
    h, m = s // 3600, (s % 3600) // 60
    if h and m:
        return f"{h}h {m}min"
    if h:
        return f"{h}h"
    return f"{m}min"
