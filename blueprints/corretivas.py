"""
MÓDULO — MANUTENÇÕES CORRETIVAS
Abertura de OS, fila por criticidade, cronômetro, apontamentos,
solicitação de peças, conclusão com defeito/causa e aprovação do solicitante.
"""
from datetime import datetime

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, session, Response, jsonify, abort)

import db
from auth import pode, exige, EXECUCAO

bp = Blueprint("os", __name__, url_prefix="/os")

# Tipos de cronômetro
TIPOS_TEMPO = {
    "trabalho": "Em execução",
    "pausa": "Pausada",
    "almoco": "Almoço",
    "aguardando_peca": "Aguardando peça",
}

STATUS_LABEL = {
    "aberta": "Aberta",
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
        where.append("o.status IN ('aberta','em_andamento','pausada','aguardando_peca','reprovada')")
    elif status == "aprovacao":
        where.append("o.status='aguardando_aprovacao'")
    elif status and status != "todas":
        where.append("o.status=%s")
        params.append(status)

    # Solicitante puro só enxerga as próprias OS
    if session.get("perfil") == "solicitante":
        where.append("o.solicitante_id=%s")
        params.append(session["uid"])

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

        db.notificar_perfis(("lider", "supervisao", "admin"),
                            f"Nova OS #{numero}",
                            descricao[:120], url_for("os.detalhe", os_id=os_id))
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
                           TIPOS_TEMPO=TIPOS_TEMPO, STATUS_LABEL=STATUS_LABEL)


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
@exige("os_executar")
def assumir(os_id):
    resp = request.form.get("responsavel_id") or session["uid"]
    nome = db.scalar("SELECT nome FROM usuarios WHERE id=%s", (resp,), default="")
    db.executar("UPDATE ordens_servico SET responsavel_id=%s WHERE id=%s", (resp, os_id))
    _apontar(os_id, "assumiu", f"{nome} assumiu a OS.")
    o = db.um("SELECT numero, solicitante_id FROM ordens_servico WHERE id=%s", (os_id,))
    db.notificar(o["solicitante_id"], f"OS #{o['numero']} — manutentor designado",
                 f"{nome} assumiu sua ordem de serviço.", url_for("os.detalhe", os_id=os_id))
    flash(f"OS atribuída a {nome}.", "success")
    return redirect(url_for("os.detalhe", os_id=os_id))


@bp.route("/<int:os_id>/acao/<acao>", methods=["POST"])
@exige("os_executar")
def acao(os_id, acao):
    o = db.um("SELECT * FROM ordens_servico WHERE id=%s", (os_id,))
    if not o:
        abort(404)

    if acao == "iniciar":
        if not o["responsavel_id"]:
            db.executar("UPDATE ordens_servico SET responsavel_id=%s WHERE id=%s",
                        (session["uid"], os_id))
            _apontar(os_id, "assumiu", f"{session['nome']} assumiu a OS.")
        if not o["data_inicio"]:
            db.executar("UPDATE ordens_servico SET data_inicio=NOW() WHERE id=%s", (os_id,))
        db.executar("UPDATE ordens_servico SET status='em_andamento' WHERE id=%s", (os_id,))
        _iniciar_tempo(os_id, "trabalho")
        _apontar(os_id, "inicio", "Execução iniciada.")
        db.notificar(o["solicitante_id"], f"OS #{o['numero']} iniciada",
                     f"{session['nome']} começou o atendimento.",
                     url_for("os.detalhe", os_id=os_id))

    elif acao in ("pausar", "almoco", "aguardando_peca"):
        mapa = {"pausar": "pausa", "almoco": "almoco", "aguardando_peca": "aguardando_peca"}
        tipo = mapa[acao]
        novo_status = "aguardando_peca" if acao == "aguardando_peca" else "pausada"
        db.executar("UPDATE ordens_servico SET status=%s WHERE id=%s", (novo_status, os_id))
        _iniciar_tempo(os_id, tipo)
        _apontar(os_id, tipo, TIPOS_TEMPO[tipo] + ".")
        if acao == "aguardando_peca":
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

    # ── Quanto dá para atender agora pelo NLAG ──
    disponivel = 0.0
    if mat and mat["tipo"] == "NLAG":
        disponivel = max(db.saldo_material(mat["codigo"]), 0.0)
    atendido = min(qtd, disponivel)
    faltante = round(qtd - atendido, 3)

    # ── 1. Baixa do que existe ──
    if atendido > 0:
        db.executar("""INSERT INTO movimentacoes
                       (codigo, tipo, quantidade, usuario, observacao, os_id)
                       VALUES (%s,'SAIDA',%s,%s,%s,%s)""",
                    (mat["codigo"], atendido, session["nome"],
                     f"Consumo na OS #{o['numero']}", os_id))
        db.executar("""INSERT INTO os_materiais
                       (os_id, material_id, codigo, descricao, quantidade, origem,
                        valor_unit, usuario_id, baixado)
                       VALUES (%s,%s,%s,%s,%s,'NLAG',%s,%s,TRUE)""",
                    (os_id, mat["id"], mat["codigo"], descricao, atendido,
                     mat["valor_unit"] or 0, session["uid"]))
        novo_saldo = db.saldo_material(mat["codigo"])
        _apontar(os_id, "material",
                 f"Retirado do depósito NLAG: {atendido:g} {mat['unidade']} de "
                 f"{descricao} ({mat['codigo']}).")
        flash(f"Baixa de {atendido:g} {mat['unidade']} registrada. "
              f"Saldo restante: {novo_saldo:g}.", "success")
        _avisar_estoque_minimo(mat, novo_saldo)

    # ── 2. Solicitação do que faltou ──
    if faltante > 0:
        if not mat:
            tipo_sm = "Cadastro"
            obs = "Peça sem cadastro no sistema. Solicitada pelo manutentor durante a OS."
        elif mat["tipo"] == "NLAG":
            tipo_sm = "Estoque NLAG"
            obs = (f"Saldo NLAG insuficiente no momento do pedido "
                   f"(disponível {disponivel:g}, necessário {qtd:g}).")
        else:
            saldo_sap = float(mat["saldo_sap"] or 0)
            tipo_sm = "HIBE/ERSA"
            obs = (f"Material {mat['tipo']} — saldo do SAP na última importação: "
                   f"{saldo_sap:g}.")

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
                 f"Solicitação SM #{numero} enviada ao analista de materiais: "
                 f"{faltante:g} de {descricao or codigo}.")

        db.notificar_perfis(("analista",),
                            f"Peça solicitada na OS #{o['numero']} — SM #{numero}",
                            f"{descricao or codigo} · qtd {faltante:g} · "
                            f"{eq or 'sem equipamento'}",
                            url_for("sol.detalhe", sid=sid))
        db.notificar(o["solicitante_id"], f"OS #{o['numero']} — material solicitado",
                     f"{descricao or codigo} foi solicitado ao almoxarifado.",
                     url_for("os.detalhe", os_id=os_id))

        if atendido > 0:
            flash(f"Faltaram {faltante:g} — solicitação SM #{numero} aberta "
                  "para o analista de materiais.", "warning")
        else:
            flash(f"Sem saldo no depósito NLAG. Solicitação SM #{numero} enviada "
                  "ao analista de materiais.", "warning")

        # Pausa a OS aguardando a peça
        if pausar and o["status"] == "em_andamento":
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
    _recalcular_custo(os_id)
    _apontar(os_id, "conclusao", f"Serviço concluído: {acao_txt}")

    o = db.um("SELECT numero, solicitante_id, equipamento_id FROM ordens_servico WHERE id=%s",
              (os_id,))
    if liberar and o["equipamento_id"]:
        db.executar("UPDATE equipamentos SET status='operando' WHERE id=%s", (o["equipamento_id"],))

    db.notificar(o["solicitante_id"], f"OS #{o['numero']} concluída — aprove ou reprove",
                 acao_txt[:150], url_for("os.detalhe", os_id=os_id))
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
        db.notificar(o["responsavel_id"], f"OS #{o['numero']} aprovada",
                     "O solicitante aprovou o serviço. OS finalizada.",
                     url_for("os.detalhe", os_id=os_id))
        flash("OS aprovada e finalizada.", "success")
    else:
        if not comentario:
            flash("Descreva no comentário o que continua ocorrendo.", "warning")
            return redirect(url_for("os.detalhe", os_id=os_id))
        db.executar("""UPDATE ordens_servico SET status='reprovada', aprovado=FALSE,
                       comentario_reprova=%s WHERE id=%s""", (comentario, os_id))
        _apontar(os_id, "reprovacao", f"OS REPROVADA: {comentario}")
        db.notificar(o["responsavel_id"], f"OS #{o['numero']} reprovada",
                     comentario[:150], url_for("os.detalhe", os_id=os_id))
        db.notificar_perfis(("lider", "supervisao"), f"OS #{o['numero']} reprovada",
                            comentario[:150], url_for("os.detalhe", os_id=os_id))
        flash("OS reprovada — ela volta para a fila da manutenção.", "warning")
    return redirect(url_for("os.detalhe", os_id=os_id))


@bp.route("/<int:os_id>/reabrir", methods=["POST"])
@exige("os_executar")
def reabrir(os_id):
    db.executar("UPDATE ordens_servico SET status='em_andamento' WHERE id=%s", (os_id,))
    _iniciar_tempo(os_id, "trabalho")
    _apontar(os_id, "inicio", "OS reaberta para tratamento da reprovação.")
    return redirect(url_for("os.detalhe", os_id=os_id))


# ══════════════════════════════════════════════════════════════════
#  MODO TABLET — fila simplificada de chão de fábrica
# ══════════════════════════════════════════════════════════════════
@bp.route("/tablet")
@exige("os_executar")
def tablet():
    fila = db.query(f"""
        SELECT o.*, e.codigo AS eq_codigo, e.nome AS eq_nome, r.nome AS responsavel
        FROM ordens_servico o
        LEFT JOIN equipamentos e ON e.id=o.equipamento_id
        LEFT JOIN usuarios r ON r.id=o.responsavel_id
        WHERE o.status IN ('aberta','em_andamento','pausada','aguardando_peca','reprovada')
        ORDER BY CASE o.status WHEN 'em_andamento' THEN 0 ELSE 1 END,
                 {db.ordem_crit('o.criticidade')},
                 o.maquina_parada DESC, o.data_abertura""")
    return render_template("os/tablet.html", fila=fila, STATUS_LABEL=STATUS_LABEL)
