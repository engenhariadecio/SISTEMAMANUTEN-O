"""Teste dos níveis de criticidade configuráveis e da matriz de classificação."""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:teste@localhost:5432/manutencao")
os.environ.setdefault("SECRET_KEY", "teste")

from app import app
import db

c = app.test_client()
c.post("/login", data={"usuario": "admin", "senha": "teste123"})

print("── Níveis cadastrados ──")
for n in db.niveis_criticidade():
    resp = f"{float(n['sla_resposta_h']):g}h" if n["sla_resposta_h"] is not None else "—"
    concl = f"{float(n['sla_conclusao_h']):g}h" if n["sla_conclusao_h"] is not None else "—"
    print(f"   {n['codigo']} · ordem {n['ordem']} · {n['nome']:<12} "
          f"resposta {resp:>6} · conclusão {concl:>6} · {n['cor']}")
assert len(db.niveis_criticidade()) == 5, "deveriam existir 5 níveis"
print("   ✅ 5 níveis (A–E) criados no seed")

# ══ ESCALA ADAPTATIVA ══════════════════════════════════════════
print("\n── Classificação por pontuação (5 níveis) ──")
for p in (100, 85, 79, 61, 55, 39, 21, 10, 0):
    print(f"   {p:>3} pontos → {db.classificar(p)}")
assert db.classificar(100) == "A" and db.classificar(0) == "E"
assert db.classificar(85) == "A" and db.classificar(70) == "B"
assert db.classificar(50) == "C" and db.classificar(30) == "D"
print("   ✅ faixas de 20 pontos, do A ao E")

# ══ MATRIZ ═════════════════════════════════════════════════════
print("\n── Pontuação da matriz ──")
casos = [
    ("Compressor (para a fábrica, sem reserva)",
     {"mtz_seguranca": 3, "mtz_producao": 4, "mtz_qualidade": 2,
      "mtz_frequencia": 2, "mtz_reparo": 3, "mtz_redundancia": 4}),
    ("Dobradeira (para uma linha, tem outra)",
     {"mtz_seguranca": 2, "mtz_producao": 3, "mtz_qualidade": 2,
      "mtz_frequencia": 2, "mtz_reparo": 2, "mtz_redundancia": 1}),
    ("Bebedouro (apoio)",
     {"mtz_seguranca": 0, "mtz_producao": 0, "mtz_qualidade": 0,
      "mtz_frequencia": 1, "mtz_reparo": 0, "mtz_redundancia": 0}),
]
for nome, notas in casos:
    p = db.pontuar_matriz(notas)
    print(f"   {nome:<44} {p:>6.1f} → {db.classificar(p)}")

# tudo zero e tudo no máximo
assert db.pontuar_matriz({}) == 0.0
tudo4 = {campo: 4 for campo, *_ in db.CRITERIOS_MATRIZ}
assert db.pontuar_matriz(tudo4) == 100.0
print("   ✅ escala de 0 a 100 fechada corretamente")

# ══ ESCALONAMENTO (máquina parada) ═════════════════════════════
print("\n── Escalonamento por máquina parada ──")
for origem in ("A", "B", "C", "D", "E"):
    print(f"   {origem} → {db.escalar_criticidade(origem)}")
assert db.escalar_criticidade("C") == "B"
assert db.escalar_criticidade("A") == "A", "A não pode subir mais"
assert db.escalar_criticidade("E") == "D"
print("   ✅ sobe um nível, sem passar de A")

# ══ TELA DA MATRIZ ═════════════════════════════════════════════
print("\n── Classificar um equipamento pela tela ──")
eq = db.um("SELECT * FROM equipamentos WHERE codigo='CO03-00'")
print(f"   {eq['codigo']} — criticidade antes: {eq['criticidade']}")
assert c.get(f"/admin/equipamentos/{eq['id']}/matriz").status_code == 200

r = c.post(f"/admin/equipamentos/{eq['id']}/matriz", data={
    "mtz_seguranca": 3, "mtz_producao": 4, "mtz_qualidade": 2,
    "mtz_frequencia": 2, "mtz_reparo": 3, "mtz_redundancia": 4,
    "aplicar": "1", "justificativa": "Único compressor da matriz, sem reserva."},
    follow_redirects=True)
assert r.status_code == 200
eq2 = db.um("SELECT * FROM equipamentos WHERE id=%s", (eq["id"],))
print(f"   pontuação {eq2['mtz_pontuacao']} → criticidade {eq2['criticidade']}")
assert eq2["mtz_pontuacao"] is not None and eq2["mtz_avaliado_em"] is not None
assert eq2["criticidade"] == db.classificar(float(eq2["mtz_pontuacao"]))
print("   ✅ matriz gravada, pontuação e nível aplicados")

# Override manual
r = c.post(f"/admin/equipamentos/{eq['id']}/matriz", data={
    "mtz_seguranca": 0, "mtz_producao": 0, "mtz_qualidade": 0,
    "mtz_frequencia": 0, "mtz_reparo": 0, "mtz_redundancia": 0,
    "criticidade_manual": "A", "justificativa": "Decisão da supervisão."},
    follow_redirects=True)
eq3 = db.um("SELECT criticidade, mtz_pontuacao FROM equipamentos WHERE id=%s", (eq["id"],))
assert eq3["criticidade"] == "A" and float(eq3["mtz_pontuacao"]) == 0.0
print("   ✅ override manual respeitado (matriz sugeria E, gravou A)")
log = db.um("SELECT * FROM log_auditoria WHERE acao='classificar_criticidade' "
            "ORDER BY id DESC LIMIT 1")
assert log, "classificação deveria ir para a auditoria"
print(f"   ✅ auditoria: {log['detalhe']}")

# ══ CRIAR / DESATIVAR NÍVEL ════════════════════════════════════
print("\n── Nível extra e escala adaptativa ──")
c.post("/admin/criticidades", data={
    "acao": "novo", "novo_codigo": "F", "novo_nome": "Descartável",
    "nova_cor": "#999999", "nova_resp": "", "nova_concl": ""}, follow_redirects=True)
assert len(db.niveis_criticidade()) == 6
print(f"   6 níveis ativos → faixa de {100/6:.1f} pontos cada")
assert db.classificar(100) == "A" and db.classificar(5) == "F"
print(f"   100 pontos → {db.classificar(100)} · 5 pontos → {db.classificar(5)}")
print("   ✅ escala se redistribuiu sozinha")

# Desativar o F e voltar para 5
c.post("/admin/criticidades", data={
    "acao": "salvar", "codigo": "F", "nome_F": "Descartável", "ordem_F": "6",
    "cor_F": "#999999", "desc_F": ""}, follow_redirects=True)
assert len(db.niveis_criticidade()) == 5, "F deveria ter sido desativado"
print("   ✅ nível desativado volta a escala para 5 faixas")

# ══ ORDENAÇÃO DA FILA ══════════════════════════════════════════
print("\n── Fila ordenada pela tabela de criticidade ──")
est = db.um("SELECT id FROM estabelecimentos WHERE nome='Matriz'")
for cod, crit in [("BE01-00", "E"), ("GU01-00", "A"), ("DO01-00", "C")]:
    e = db.um("SELECT id FROM equipamentos WHERE codigo=%s", (cod,))
    db.executar("UPDATE equipamentos SET criticidade=%s WHERE id=%s", (crit, e["id"]))
    c.post("/os/nova", data={"estabelecimento_id": est["id"], "tipo": "Industrial",
                             "equipamento_id": e["id"],
                             "descricao_problema": f"Teste de fila {cod}"},
           follow_redirects=True)

fila = db.query(f"""SELECT o.numero, o.criticidade, e.codigo FROM ordens_servico o
                    JOIN equipamentos e ON e.id=o.equipamento_id
                    WHERE o.descricao_problema LIKE 'Teste de fila%%'
                    ORDER BY {db.ordem_crit('o.criticidade')}, o.data_abertura""")
ordem = [f["criticidade"] for f in fila]
for f in fila:
    print(f"   #{f['numero']} · {f['codigo']} · criticidade {f['criticidade']}")
assert ordem == ["A", "C", "E"], f"ordem errada: {ordem}"
print("   ✅ A antes de C antes de E")

# Máquina parada escalona
e = db.um("SELECT id FROM equipamentos WHERE codigo='BE01-00'")
c.post("/os/nova", data={"estabelecimento_id": est["id"], "tipo": "Industrial",
                         "equipamento_id": e["id"], "maquina_parada": "on",
                         "descricao_problema": "Parada escalonando"}, follow_redirects=True)
o = db.um("SELECT criticidade FROM ordens_servico WHERE descricao_problema='Parada escalonando'")
assert o["criticidade"] == "D", f"E parado deveria virar D, veio {o['criticidade']}"
print("   ✅ equipamento E com máquina parada abriu OS como D")

# ══ ROTAS ══════════════════════════════════════════════════════
print("\n── Rotas ──")
for rota in ["/admin/criticidades", f"/admin/equipamentos/{eq['id']}/matriz",
             "/admin/equipamentos", "/os/", "/", "/indicadores/parque",
             "/preventivas/", "/os/?criticidade=A"]:
    r = c.get(rota)
    assert r.status_code == 200, f"{rota} → {r.status_code}"
    print(f"   ✅ {rota}")

r = c.post("/admin/criticidades", data={"acao": "reclassificar"}, follow_redirects=True)
assert r.status_code == 200
print("   ✅ reclassificação em massa")

print("\n" + "=" * 56)
print("✅ CRITICIDADE CONFIGURÁVEL VALIDADA")
print("=" * 56)
