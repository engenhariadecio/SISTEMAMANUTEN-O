"""
MÓDULO — INDICADORES
MTBF, MTTR, % de atendimento de preventivas, atendimento por responsável,
custos por equipamento e visão geral do parque fabril.
"""
from datetime import date, timedelta

from flask import Blueprint, render_template, request, jsonify

import db
from auth import exige

bp = Blueprint("ind", __name__, url_prefix="/indicadores")

MESES = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN",
         "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]


def periodo_padrao():
    fim = db.hoje()
    ini = fim.replace(day=1) - timedelta(days=365)
    return ini, fim


@bp.route("/")
@exige("indicadores")
def painel():
    ini = request.args.get("ini") or (db.hoje() - timedelta(days=180)).isoformat()
    fim = request.args.get("fim") or db.hoje().isoformat()
    ano = int(request.args.get("ano") or db.hoje().year)

    # ── Números gerais de OS ──
    os_geral = db.um("""
        SELECT COUNT(*) FILTER (WHERE status='aberta') AS abertas,
               COUNT(*) FILTER (WHERE status IN ('em_andamento','pausada')) AS andamento,
               COUNT(*) FILTER (WHERE status='aguardando_peca') AS agu_peca,
               COUNT(*) FILTER (WHERE status='aguardando_aprovacao') AS agu_aprov,
               COUNT(*) FILTER (WHERE status='concluida') AS concluidas,
               COUNT(*) FILTER (WHERE status='reprovada') AS reprovadas,
               COUNT(*) AS total
        FROM ordens_servico WHERE data_abertura::date BETWEEN %s AND %s""", (ini, fim))

    # ── MTTR global (média do tempo de reparo das OS concluídas) ──
    mttr_seg = db.scalar("""SELECT COALESCE(AVG(tempo_trabalho_seg),0) AS m
                            FROM ordens_servico
                            WHERE status='concluida' AND tempo_trabalho_seg > 0
                              AND data_conclusao::date BETWEEN %s AND %s""",
                         (ini, fim), default=0)

    # ── MTBF / MTTR por equipamento ──
    dias_periodo = max((date.fromisoformat(fim) - date.fromisoformat(ini)).days, 1)
    horas_periodo = dias_periodo * 24

    equipamentos = db.query("""
        SELECT e.id, e.codigo, e.nome, e.criticidade, e.status,
               COUNT(o.id) AS n_falhas,
               COALESCE(SUM(o.tempo_trabalho_seg),0) AS tempo_reparo,
               COALESCE(SUM(o.custo_pecas),0) AS custo_pecas,
               COALESCE(SUM(o.custo_hh),0) AS custo_hh
        FROM equipamentos e
        LEFT JOIN ordens_servico o ON o.equipamento_id=e.id
             AND o.tipo_manutencao IN ('corretiva','intervencao')
             AND o.status='concluida'
             AND o.data_conclusao::date BETWEEN %s AND %s
        WHERE e.ativo=TRUE
        GROUP BY e.id ORDER BY n_falhas DESC, e.codigo""", (ini, fim))

    linhas = []
    for e in equipamentos or []:
        e = dict(e)
        falhas = e["n_falhas"] or 0
        reparo_h = (e["tempo_reparo"] or 0) / 3600
        e["mttr_h"] = round(reparo_h / falhas, 2) if falhas else 0
        e["mtbf_h"] = round((horas_periodo - reparo_h) / falhas, 1) if falhas else 0
        e["disponibilidade"] = round(
            (horas_periodo - reparo_h) / horas_periodo * 100, 2) if horas_periodo else 100
        e["custo_total"] = float(e["custo_pecas"] or 0) + float(e["custo_hh"] or 0)
        linhas.append(e)

    top_falhas = sorted([l for l in linhas if l["n_falhas"]],
                        key=lambda x: -x["n_falhas"])[:10]
    top_custo = sorted([l for l in linhas if l["custo_total"]],
                       key=lambda x: -x["custo_total"])[:10]

    # ── % de preventivas previstas x realizadas (por mês) ──
    prev_mes = []
    for m in range(1, 13):
        previsto = db.scalar("""SELECT COUNT(*) AS n FROM programacao pr
                                WHERE pr.ano=%s
                                AND EXTRACT(MONTH FROM
                                     make_date(pr.ano,1,1) + (pr.semana-1)*7) = %s""",
                             (ano, m), default=0)
        realizado = db.scalar("""SELECT COUNT(*) AS n FROM ordens_manutencao om
                                 WHERE om.status='concluida' AND om.ano=%s
                                   AND EXTRACT(MONTH FROM om.data_fim)=%s
                                   AND COALESCE(om.no_prazo, TRUE)=TRUE""",
                              (ano, m), default=0)
        pct = round(realizado / previsto * 100, 1) if previsto else 0
        prev_mes.append({"mes": MESES[m - 1], "previsto": previsto,
                         "realizado": realizado, "pct": pct})

    total_prev = sum(p["previsto"] for p in prev_mes)
    total_real = sum(p["realizado"] for p in prev_mes)
    pct_geral = round(total_real / total_prev * 100, 1) if total_prev else 0

    # ── Atendimento por responsável ──
    por_resp_os = db.query("""
        SELECT u.nome, COUNT(o.id) AS total,
               COUNT(*) FILTER (WHERE o.status='concluida') AS concluidas,
               COALESCE(AVG(o.tempo_trabalho_seg) FILTER (WHERE o.status='concluida'),0) AS media_seg
        FROM usuarios u
        JOIN ordens_servico o ON o.responsavel_id=u.id
             AND o.data_abertura::date BETWEEN %s AND %s
        GROUP BY u.id, u.nome ORDER BY total DESC""", (ini, fim))

    por_resp_prev = db.query("""
        SELECT u.nome,
               COUNT(om.id) AS total,
               COUNT(*) FILTER (WHERE om.status='concluida') AS concluidas,
               COUNT(*) FILTER (WHERE om.status='concluida' AND om.no_prazo) AS no_prazo
        FROM usuarios u
        JOIN ordens_manutencao om ON om.manutentor1_id=u.id AND om.ano=%s
        GROUP BY u.id, u.nome ORDER BY total DESC""", (ano,))

    # ── Defeitos e causas mais frequentes ──
    por_defeito = db.query("""SELECT d.nome, COUNT(*) AS n FROM ordens_servico o
                              JOIN defeitos d ON d.id=o.defeito_id
                              WHERE o.data_conclusao::date BETWEEN %s AND %s
                              GROUP BY d.nome ORDER BY n DESC LIMIT 10""", (ini, fim))
    por_causa = db.query("""SELECT c.nome, COUNT(*) AS n FROM ordens_servico o
                            JOIN causas c ON c.id=o.causa_id
                            WHERE o.data_conclusao::date BETWEEN %s AND %s
                            GROUP BY c.nome ORDER BY n DESC LIMIT 10""", (ini, fim))

    parados = [l for l in linhas if l["status"] == "parado"]

    return render_template("ind/painel.html", os_geral=os_geral, mttr_h=round(mttr_seg / 3600, 2),
                           linhas=linhas, top_falhas=top_falhas, top_custo=top_custo,
                           prev_mes=prev_mes, pct_geral=pct_geral, total_prev=total_prev,
                           total_real=total_real, por_resp_os=por_resp_os,
                           por_resp_prev=por_resp_prev, por_defeito=por_defeito,
                           por_causa=por_causa, parados=parados,
                           ini=ini, fim=fim, ano=ano)


@bp.route("/parque")
def parque():
    """Visão geral do parque fabril — vermelho = parado."""
    equipamentos = db.query("""
        SELECT e.*, ct.nome AS setor, est.nome AS estabelecimento,
          (SELECT COUNT(*) FROM ordens_servico o WHERE o.equipamento_id=e.id
             AND o.status IN ('aberta','atribuida','em_andamento','pausada','aguardando_peca','reprovada'))
           AS os_abertas
        FROM equipamentos e
        LEFT JOIN centros_trabalho ct ON ct.id=e.centro_trabalho_id
        LEFT JOIN estabelecimentos est ON est.id=e.estabelecimento_id
        WHERE e.ativo=TRUE
        ORDER BY ct.nome NULLS LAST, e.codigo""")

    setores = {}
    for e in equipamentos or []:
        setores.setdefault(e["setor"] or "Sem setor", []).append(e)

    resumo = {
        "total": len(equipamentos or []),
        "parados": len([e for e in (equipamentos or []) if e["status"] == "parado"]),
        "manutencao": len([e for e in (equipamentos or []) if e["status"] == "manutencao"]),
    }
    resumo["operando"] = resumo["total"] - resumo["parados"] - resumo["manutencao"]
    return render_template("ind/parque.html", setores=setores, resumo=resumo)


@bp.route("/equipamento/<int:eq_id>")
@exige("indicadores")
def equipamento(eq_id):
    e = db.um("""SELECT e.*, ct.nome AS setor, est.nome AS estabelecimento
                 FROM equipamentos e
                 LEFT JOIN centros_trabalho ct ON ct.id=e.centro_trabalho_id
                 LEFT JOIN estabelecimentos est ON est.id=e.estabelecimento_id
                 WHERE e.id=%s""", (eq_id,))
    if not e:
        from flask import abort
        abort(404)

    historico = db.query("""SELECT o.*, d.nome AS defeito, c.nome AS causa, u.nome AS responsavel
                            FROM ordens_servico o
                            LEFT JOIN defeitos d ON d.id=o.defeito_id
                            LEFT JOIN causas c ON c.id=o.causa_id
                            LEFT JOIN usuarios u ON u.id=o.responsavel_id
                            WHERE o.equipamento_id=%s ORDER BY o.data_abertura DESC LIMIT 100""",
                         (eq_id,))
    oms = db.query("""SELECT om.*, p.nome AS plano FROM ordens_manutencao om
                      LEFT JOIN planos_preventiva p ON p.id=om.plano_id
                      WHERE om.equipamento_id=%s ORDER BY om.data_prevista DESC LIMIT 60""",
                   (eq_id,))
    custos = db.um("""SELECT COALESCE(SUM(custo_pecas),0) AS pecas,
                             COALESCE(SUM(custo_hh),0) AS hh,
                             COALESCE(SUM(tempo_trabalho_seg),0) AS tempo,
                             COUNT(*) AS n
                      FROM ordens_servico WHERE equipamento_id=%s AND status='concluida'""",
                   (eq_id,))
    terceiros = db.query("""SELECT * FROM manutencoes_terceiros WHERE equipamento_id=%s
                            ORDER BY data_envio DESC LIMIT 30""", (eq_id,))
    return render_template("ind/equipamento.html", e=e, historico=historico, oms=oms,
                           custos=custos, terceiros=terceiros)


@bp.route("/api/serie-os")
@exige("indicadores")
def api_serie_os():
    dias = int(request.args.get("dias", 30))
    dados = db.query("""
        SELECT to_char(d.dia,'DD/MM') AS dia,
               COUNT(o.id) FILTER (WHERE o.id IS NOT NULL) AS abertas,
               COUNT(c.id) FILTER (WHERE c.id IS NOT NULL) AS concluidas
        FROM generate_series(CURRENT_DATE - %s::int, CURRENT_DATE, '1 day') AS d(dia)
        LEFT JOIN ordens_servico o ON o.data_abertura::date = d.dia
        LEFT JOIN ordens_servico c ON c.data_conclusao::date = d.dia
        GROUP BY d.dia ORDER BY d.dia""", (dias,))
    return jsonify(dados or [])
