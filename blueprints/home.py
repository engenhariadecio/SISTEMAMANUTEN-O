from datetime import date, timedelta

from flask import Blueprint, render_template, session

import db
from auth import pode

bp = Blueprint("home", __name__)


@bp.route("/")
def index():
    perfil = session.get("perfil")
    uid = session.get("uid")
    hoje = db.hoje()
    ano, semana, _ = hoje.isocalendar()

    # ── Cartões de resumo ──
    resumo = {
        "triagem": db.scalar("""SELECT COUNT(*) AS n FROM ordens_servico
                                WHERE status='aberta' AND responsavel_id IS NULL"""),
        "abertas": db.scalar("""SELECT COUNT(*) AS n FROM ordens_servico
                                WHERE status IN ('aberta','atribuida')"""),
        "andamento": db.scalar(
            "SELECT COUNT(*) AS n FROM ordens_servico WHERE status IN ('em_andamento','pausada')"),
        "aguardando_peca": db.scalar(
            "SELECT COUNT(*) AS n FROM ordens_servico WHERE status='aguardando_peca'"),
        "aguardando_aprov": db.scalar(
            "SELECT COUNT(*) AS n FROM ordens_servico WHERE status='aguardando_aprovacao'"),
        "reprovadas": db.scalar(
            "SELECT COUNT(*) AS n FROM ordens_servico WHERE status='reprovada'"),
        "parados": db.scalar(
            "SELECT COUNT(*) AS n FROM equipamentos WHERE status='parado' AND ativo=TRUE"),
    }

    # ── Preventivas da semana ──
    resumo["prev_semana"] = db.scalar(
        "SELECT COUNT(*) AS n FROM programacao WHERE ano=%s AND semana=%s AND status='previsto'",
        (ano, semana))
    resumo["prev_atrasadas"] = db.scalar(
        """SELECT COUNT(*) AS n FROM programacao
           WHERE status='previsto' AND (ano < %s OR (ano=%s AND semana < %s))""",
        (ano, ano, semana))

    # ── Materiais abaixo do mínimo ──
    resumo["material_critico"] = db.scalar("""
        SELECT COUNT(*) AS n FROM (
          SELECT m.codigo, m.estoque_min,
                 COALESCE(SUM(CASE WHEN mv.tipo IN ('ENTRADA','AJUSTE') THEN mv.quantidade
                                   ELSE -mv.quantidade END),0) AS saldo
          FROM materiais m
          LEFT JOIN movimentacoes mv ON mv.codigo = m.codigo
          WHERE m.ativo=TRUE AND m.tipo='NLAG' AND m.estoque_min > 0
          GROUP BY m.codigo, m.estoque_min
        ) t WHERE t.saldo < t.estoque_min""")

    resumo["solic_abertas"] = db.scalar(
        """SELECT COUNT(*) AS n FROM solicitacoes_material
           WHERE situacao NOT IN ('Concluído','Cancelado','Recusado')""")

    # ── Fila de atendimento ──
    ABERTAS = ("aberta", "atribuida", "em_andamento", "pausada",
               "aguardando_peca", "reprovada")
    filtro, params = "", [list(ABERTAS)]
    if perfil == "manutentor":
        filtro = "AND o.responsavel_id=%s"
        params.append(uid)
    fila = db.query(f"""
        SELECT o.*, e.codigo AS eq_codigo, e.nome AS eq_nome,
               s.nome AS solicitante, r.nome AS responsavel
        FROM ordens_servico o
        LEFT JOIN equipamentos e ON e.id = o.equipamento_id
        LEFT JOIN usuarios s ON s.id = o.solicitante_id
        LEFT JOIN usuarios r ON r.id = o.responsavel_id
        WHERE o.status = ANY(%s) {filtro}
        ORDER BY CASE WHEN o.responsavel_id IS NULL THEN 0 ELSE 1 END,
                 {db.ordem_crit('o.criticidade')},
                 o.maquina_parada DESC, o.data_abertura
        LIMIT 12""", params)

    # ── Minhas OS (solicitante) ──
    minhas = db.query("""
        SELECT o.*, e.codigo AS eq_codigo, e.nome AS eq_nome, r.nome AS responsavel
        FROM ordens_servico o
        LEFT JOIN equipamentos e ON e.id = o.equipamento_id
        LEFT JOIN usuarios r ON r.id = o.responsavel_id
        WHERE o.solicitante_id = %s
        ORDER BY o.data_abertura DESC LIMIT 8""", (uid,))

    # ── Equipamentos parados ──
    parados = db.query(f"""
        SELECT e.*, ct.nome AS setor FROM equipamentos e
        LEFT JOIN centros_trabalho ct ON ct.id = e.centro_trabalho_id
        WHERE e.status='parado' AND e.ativo=TRUE
        ORDER BY {db.ordem_crit('e.criticidade')}, e.codigo""")

    # ── Gráfico: OS abertas nos últimos 14 dias ──
    serie = db.query("""
        SELECT to_char(d.dia,'DD/MM') AS dia,
               COUNT(o.id) AS total
        FROM generate_series(%s::date, %s::date, '1 day') AS d(dia)
        LEFT JOIN ordens_servico o ON o.data_abertura::date = d.dia
        GROUP BY d.dia ORDER BY d.dia""",
        (hoje - timedelta(days=13), hoje))

    return render_template("index.html", resumo=resumo, fila=fila, minhas=minhas,
                           parados=parados, serie=serie, semana=semana, ano=ano)
