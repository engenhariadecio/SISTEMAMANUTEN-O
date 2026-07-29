"""Teste do plano de materiais por dias, com comparação de estoque."""
import os
from datetime import timedelta

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:teste@localhost:5432/manutencao")
os.environ.setdefault("SECRET_KEY", "teste")

from app import app
import db
from blueprints.preventivas import calcular_necessidade, semanas_do_periodo

c = app.test_client()
c.post("/login", data={"usuario": "admin", "senha": "teste123"})

# ══ CENÁRIO ══════════════════════════════════════════════════
print("── Montando cenário realista ──")

# Materiais com saldos diferentes para cobrir os 4 casos
MATS = [
    # codigo,     descricao,                        umb,  saldo, min, valor
    ("7000590", "OLEO LUBRIF MINERAL 20L S2 M 32", "L",   100, 20, 480.00),  # sobra
    ("1990361", "PANO MULTIUSO LINHO COL 25X18MM", "UNI",  30, 200,  1.20),  # parcial
    ("6001740", "FILTRO OLEO HIDRAULICO W962",     "UNI",   0,  2, 145.00),  # sem estoque
    ("7604374", "CABO DE IGNICAO",                 "UNI",   4,  1,  89.90),  # parcial
]
for cod, desc, umb, saldo, minimo, valor in MATS:
    db.executar("""INSERT INTO materiais (codigo, descricao, unidade, tipo,
                     estoque_min, estoque_max, valor_unit, critico)
                   VALUES (%s,%s,%s,'NLAG',%s,%s,%s,TRUE)
                   ON CONFLICT (codigo) DO UPDATE SET estoque_min=EXCLUDED.estoque_min,
                     valor_unit=EXCLUDED.valor_unit""",
                (cod, desc, umb, minimo, minimo * 3, valor))
    atual = db.saldo_material(cod)
    if saldo - atual != 0:
        db.executar("""INSERT INTO movimentacoes (codigo, tipo, quantidade, usuario, observacao)
                       VALUES (%s,'AJUSTE',%s,'teste','carga de teste')""",
                    (cod, saldo - atual))

# "ZZZ999-SEM-CADASTRO" propositalmente NÃO cadastrado → deve cair em "não cadastrado"

# Três planos em equipamentos diferentes, com periodicidades diferentes
PLANOS = [
    ("CO03-00", "Preventiva Compressor Kaeser ASD 40S", "MEN", 1,
     [("7000590", 20, "qt_men"), ("1990361", 10, "qt_men"), ("6001740", 1, "qt_men")]),
    ("PU01-00", "Preventiva Puncionadeira PGA4", "TRI", 1,
     [("1990361", 20, "qt_tri"), ("7604374", 2, "qt_tri"), ("ZZZ999-SEM-CADASTRO", 1, "qt_tri")]),
    ("GU01-00", "Preventiva Guilhotina Newton 3003", "SEM", 1,
     [("1990361", 5, "qt_sem"), ("7000590", 2, "qt_sem")]),
]

ano = db.hoje().year
for eq_cod, nome, period, sem_ini, materiais in PLANOS:
    eq = db.um("SELECT id FROM equipamentos WHERE codigo=%s", (eq_cod,))
    pid = db.inserir("""INSERT INTO planos_preventiva (equipamento_id, nome, ativo)
                        VALUES (%s,%s,TRUE) RETURNING id""", (eq["id"], nome))
    db.executar("""INSERT INTO checklist_itens (plano_id, ordem, numero, descricao, periodicidade)
                   VALUES (%s,1,'1','Item de verificação',%s)""", (pid, period))
    for cod, qt, col in materiais:
        db.executar(f"""INSERT INTO plano_materiais (plano_id, codigo, descricao, umb, {col})
                        VALUES (%s,%s,%s,'UNI',%s)""",
                    (pid, cod, dict((m[0], m[1]) for m in MATS).get(cod, cod), qt))
    c.post("/preventivas/programar", data={"plano_id": pid, "ano": ano,
                                           "periodicidade": period, "semana_inicial": sem_ini})

n_prog = db.scalar("SELECT COUNT(*) AS n FROM programacao WHERE ano=%s", (ano,))
print(f"   {len(PLANOS)} planos · {n_prog} preventivas programadas em {ano}")

# ══ TESTES ═══════════════════════════════════════════════════
hoje = db.hoje()
print("\n── Horizontes ──")
for dias in (7, 15, 30, 60, 90):
    d_fim = hoje + timedelta(days=dias - 1)
    linhas, det, res = calcular_necessidade(hoje, d_fim)
    print(f"   {dias:>3} dias ({len(semanas_do_periodo(hoje, d_fim))} semanas) → "
          f"{res['preventivas']:>2} preventivas · {res['itens']} itens · "
          f"{res['a_comprar']} a comprar · {res['custo_total']:.2f}")

print("\n── Detalhe do plano de 30 dias ──")
linhas, detalhe, resumo = calcular_necessidade(hoje, hoje + timedelta(days=29))
print(f"   {'CÓDIGO':<10}{'NECESS.':>9}{'SALDO':>9}{'FALTA':>9}  SITUAÇÃO")
for l in linhas:
    print(f"   {l['codigo']:<10}{l['necessario']:>9.0f}{l['saldo']:>9.0f}"
          f"{l['falta']:>9.0f}  {l['situacao']}")

# Validações
por_cod = {l["codigo"]: l for l in linhas}
assert por_cod["6001740"]["situacao"] == "sem_estoque", "filtro sem_estoque falhou"
assert por_cod["1990361"]["situacao"] == "parcial", "parcial falhou"
assert por_cod["7000590"]["situacao"] == "disponivel", "disponível falhou"
assert "ZZZ999-SEM-CADASTRO" not in por_cod, "item trimestral não deveria entrar em 30 dias"
print("\n   ✅ situações classificadas corretamente em 30 dias")
print("   ✅ item trimestral corretamente fora da janela de 30 dias")

# Janela maior traz o item trimestral (não cadastrado)
l90, _, _ = calcular_necessidade(hoje, hoje + timedelta(days=89))
p90 = {l["codigo"]: l for l in l90}
assert p90["ZZZ999-SEM-CADASTRO"]["situacao"] == "nao_cadastrado", "não cadastrado falhou"
print("   ✅ em 90 dias o item trimestral aparece como 'não cadastrado'")

# Ordenação: faltas primeiro
assert linhas[0]["situacao"] in ("sem_estoque", "nao_cadastrado"), "ordenação errada"
print("   ✅ itens críticos aparecem no topo")

# Soma correta por periodicidade
sem = [d for d in detalhe["1990361"] if d["periodicidade"] == "SEM"]
esperado = sum(d["qt"] for d in detalhe["1990361"])
assert abs(por_cod["1990361"]["necessario"] - esperado) < 0.01, "soma incorreta"
print(f"   ✅ soma por periodicidade correta ({len(detalhe['1990361'])} preventivas "
      f"consomem o item 1990361)")

# ── Reservar estoque mínimo ──
print("\n── Reservando o estoque mínimo ──")
l_sem, _, r_sem = calcular_necessidade(hoje, hoje + timedelta(days=29), considerar_min=False)
l_com, _, r_com = calcular_necessidade(hoje, hoje + timedelta(days=29), considerar_min=True)
print(f"   sem reserva: {r_sem['a_comprar']} a comprar · R$ {r_sem['custo_total']:.2f}")
print(f"   com reserva: {r_com['a_comprar']} a comprar · R$ {r_com['custo_total']:.2f}")
assert r_com["custo_total"] >= r_sem["custo_total"], "reserva deveria aumentar a falta"
print("   ✅ a reserva do mínimo aumenta a necessidade de compra")

# ── Rotas ──
print("\n── Rotas ──")
testes = [
    ("/preventivas/plano-materiais", "padrão 30 dias"),
    ("/preventivas/plano-materiais?dias=7", "7 dias"),
    ("/preventivas/plano-materiais?dias=45", "45 dias customizado"),
    ("/preventivas/plano-materiais?dias=365", "1 ano"),
    ("/preventivas/plano-materiais?dias=30&considerar_min=1", "com reserva do mínimo"),
    ("/preventivas/plano-materiais?dias=30&situacao=faltantes", "só faltantes"),
    ("/preventivas/plano-materiais?dias=30&situacao=sem_estoque", "só sem estoque"),
    ("/preventivas/plano-materiais?dias=30&todas=1", "incluindo realizadas"),
    (f"/preventivas/plano-materiais?ini={hoje}&fim={hoje + timedelta(days=90)}", "período exato"),
    ("/preventivas/plano-materiais?dias=abc", "entrada inválida"),
    ("/preventivas/plano-materiais?dias=99999", "acima do limite"),
    ("/preventivas/plano-materiais/exportar?dias=30", "exportar CSV"),
]
for rota, desc in testes:
    r = c.get(rota)
    assert r.status_code == 200, f"{desc} → HTTP {r.status_code}"
    print(f"   ✅ {desc}")

# CSV
r = c.get("/preventivas/plano-materiais/exportar?dias=30")
csv_txt = r.data.decode("utf-8-sig")
assert "PLANO DE MATERIAIS" in csv_txt and "6001740" in csv_txt, "CSV incompleto"
print(f"   ✅ CSV com {len(csv_txt.splitlines())} linhas")

# ── Gerar solicitações a partir da lista ──
print("\n── Gerar solicitações ──")
antes = db.scalar("SELECT COUNT(*) AS n FROM solicitacoes_material")
r = c.post("/preventivas/plano-materiais/solicitar?dias=90",
           data={"codigo": ["6001740", "ZZZ999-SEM-CADASTRO", "1990361"]}, follow_redirects=True)
assert r.status_code == 200
depois = db.scalar("SELECT COUNT(*) AS n FROM solicitacoes_material")
print(f"   {depois - antes} solicitações geradas")
assert depois - antes == 3, "deveria gerar 3 solicitações"

sm = db.um("""SELECT * FROM solicitacoes_material ORDER BY id DESC LIMIT 1""")
print(f"   SM #{sm['numero']} · {sm['descricao'][:34]} · qtd {sm['quantidade']:g} · tipo {sm['tipo']}")
nc = db.um("SELECT * FROM solicitacoes_material "
           "WHERE descricao='ZZZ999-SEM-CADASTRO' OR codigo='ZZZ999-SEM-CADASTRO' "
           "ORDER BY id DESC LIMIT 1")
assert nc and nc["tipo"] == "Cadastro", "item não cadastrado deveria virar tipo 'Cadastro'"
print("   ✅ item sem cadastro gerou solicitação do tipo 'Cadastro'")

# Item disponível não deve gerar solicitação
antes = db.scalar("SELECT COUNT(*) AS n FROM solicitacoes_material")
c.post("/preventivas/plano-materiais/solicitar?dias=30",
       data={"codigo": ["7000590"]}, follow_redirects=True)
assert db.scalar("SELECT COUNT(*) AS n FROM solicitacoes_material") == antes
print("   ✅ item com saldo suficiente não gera solicitação")

print("\n" + "=" * 56)
print("✅ PLANO DE MATERIAIS VALIDADO")
print("=" * 56)
