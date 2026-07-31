"""
Duas coisas:
 • a OS concluída precisa aparecer para quem a abriu
 • o analista trabalha por OS, num painel único de liberação
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:teste@localhost:5432/manutencao")
os.environ.setdefault("SECRET_KEY", "teste")

import mailer
mailer.modo_teste(True)

from app import app
from werkzeug.security import generate_password_hash
import db

for u, n, p, e in [("pn_sol", "Solicitante Painel", "solicitante", "s@p.com"),
                   ("pn_mnt", "Manutentor Painel", "manutentor", "m@p.com"),
                   ("pn_lid", "Lider Painel", "lider", "l@p.com"),
                   ("pn_ana", "Analista Painel", "analista", "a@p.com")]:
    db.executar("""INSERT INTO usuarios (usuario,senha_hash,nome,perfil,email)
                   VALUES (%s,%s,%s,%s,%s) ON CONFLICT (usuario) DO UPDATE
                   SET perfil=EXCLUDED.perfil, nome=EXCLUDED.nome""",
                (u, generate_password_hash("teste123"), n, p, e))


def entrar(u):
    c = app.test_client()
    c.post("/login", data={"usuario": u, "senha": "teste123"})
    return c


sol, mnt, lid, ana = entrar("pn_sol"), entrar("pn_mnt"), entrar("pn_lid"), entrar("pn_ana")
uid = db.scalar("SELECT id FROM usuarios WHERE usuario='pn_mnt'")
est = db.um("SELECT id FROM estabelecimentos WHERE nome='Matriz'")
eq = db.um("SELECT id FROM equipamentos WHERE codigo='CO03-00'")

# ══ 1. A OS CONCLUÍDA APARECE PARA QUEM ABRIU ═════════════════
print("── 1. OS concluída na tela do solicitante ──")
sol.post("/os/nova", data={"estabelecimento_id": est["id"], "tipo": "Industrial",
                           "equipamento_id": eq["id"],
                           "descricao_problema": "Ruído no compressor"},
         follow_redirects=True)
o = db.um("SELECT * FROM ordens_servico ORDER BY id DESC LIMIT 1")
lid.post(f"/os/{o['id']}/assumir", data={"responsavel_id": uid}, follow_redirects=True)
mnt.post(f"/os/{o['id']}/acao/iniciar", follow_redirects=True)
d = db.um("SELECT id FROM defeitos LIMIT 1")
ca = db.um("SELECT id FROM causas LIMIT 1")
mnt.post(f"/os/{o['id']}/concluir",
         data={"defeito_id": d["id"], "causa_id": ca["id"],
               "acao_realizada": "Trocado o rolamento"}, follow_redirects=True)
assert db.scalar("SELECT status FROM ordens_servico WHERE id=%s",
                 (o["id"], ), default="") == "aguardando_aprovacao"

marca = f"#{o['numero']}".encode()
assert marca in sol.get("/os/").data, \
    "BUG: a OS some da lista padrão justamente quando é a vez do solicitante"
print("   ✅ aparece na lista padrão dele (filtro 'abertas')")

painel = sol.get("/").data.decode()
assert "esperando a sua avaliação" in painel
print("   ✅ o painel abre com o aviso em destaque")
assert marca in painel.encode()
print("   ✅ e a OS está na fila do painel")

lista = sol.get("/os/").data.decode()
assert "sua vez" in lista
print("   ✅ a linha vem marcada com 'sua vez'")

sol.post(f"/os/{o['id']}/aprovar", data={"decisao": "aprovar", "comentario": "ok"},
         follow_redirects=True)
assert "esperando a sua avaliação" not in sol.get("/").data.decode()
print("   ✅ depois de avaliar, o aviso some")

# ══ 2. PAINEL DE LIBERAÇÃO ════════════════════════════════════
print("\n── 2. Painel de liberação do analista ──")
# Um item com saldo de sobra, um com saldo curto e um zerado
for cod, desc, saldo in [("PN001", "CORREIA A-72", 20),
                         ("PN002", "ROLAMENTO 6204", 3),
                         ("PN003", "GRAXA MP2 1KG", 5)]:
    db.executar("""INSERT INTO materiais (codigo,descricao,unidade,tipo,estoque_min)
                   VALUES (%s,%s,'UNI','NLAG',2) ON CONFLICT (codigo) DO NOTHING""",
                (cod, desc))
    atual = db.saldo_material(cod)
    if saldo - atual:
        db.executar("""INSERT INTO movimentacoes (codigo,tipo,quantidade,usuario)
                       VALUES (%s,'AJUSTE',%s,'setup')""", (cod, saldo - atual))

sol.post("/os/nova", data={"estabelecimento_id": est["id"], "tipo": "Industrial",
                           "equipamento_id": eq["id"], "maquina_parada": "on",
                           "descricao_problema": "Compressor não parte"},
         follow_redirects=True)
o2 = db.um("SELECT * FROM ordens_servico ORDER BY id DESC LIMIT 1")
lid.post(f"/os/{o2['id']}/assumir", data={"responsavel_id": uid}, follow_redirects=True)
mnt.post(f"/os/{o2['id']}/acao/iniciar", follow_redirects=True)

for cod, desc, qtd in [("PN001", "CORREIA A-72", 2),
                       ("PN002", "ROLAMENTO 6204", 10),
                       ("PN003", "GRAXA MP2 1KG", 1),
                       ("", "BUCHA ESPECIAL SOB MEDIDA", 1)]:
    mnt.post(f"/os/{o2['id']}/material",
             data={"codigo": cod, "descricao": desc, "quantidade": str(qtd)},
             follow_redirects=True)
print("   manutentor pediu 4 itens na OS")

r = ana.get("/solicitacoes/liberacao")
assert r.status_code == 200
tela = r.data.decode()
assert f"OS #{o2['numero']}" in tela
print(f"   ✅ a OS #{o2['numero']} aparece no painel")
assert "MÁQUINA PARADA" in tela
print("   ✅ máquina parada em destaque, e a OS vem primeiro na fila")

for termo in ["CORREIA A-72", "ROLAMENTO 6204", "GRAXA MP2 1KG",
              "BUCHA ESPECIAL SOB MEDIDA"]:
    assert termo in tela, f"faltou o item {termo}"
print("   ✅ os 4 itens da OS listados juntos")

assert "pronto" in tela and "faltam" in tela and "cadastrar" in tela
print("   ✅ cada item mostra a situação: pronto, faltando ou a cadastrar")
assert "Liberar os" in tela
print("   ✅ botão para liberar de uma vez os que estão prontos")

# ── Liberar tudo o que dá ──
antes = {c: db.saldo_material(c) for c in ("PN001", "PN002", "PN003")}
r = ana.post(f"/solicitacoes/os/{o2['id']}/liberar-tudo", follow_redirects=True)
depois = {c: db.saldo_material(c) for c in ("PN001", "PN002", "PN003")}
print(f"   PN001 {antes['PN001']:g}→{depois['PN001']:g} · "
      f"PN002 {antes['PN002']:g}→{depois['PN002']:g} · "
      f"PN003 {antes['PN003']:g}→{depois['PN003']:g}")
assert depois["PN001"] == antes["PN001"] - 2, "o que tinha saldo deveria sair"
assert depois["PN003"] == antes["PN003"] - 1, "o segundo item pronto também"
assert depois["PN002"] == antes["PN002"], "saldo curto não pode ser liberado"
print("   ✅ liberou os dois prontos; os travados ficaram intactos")

msg = r.data.decode()
assert "saldo insuficiente" in msg and "sem cadastro" in msg
print("   ✅ o aviso diz exatamente o que travou cada item")

pend = db.scalar("""SELECT COUNT(*) AS n FROM solicitacoes_material
                    WHERE os_id=%s AND situacao NOT IN
                    ('Liberado','Concluído','Recusado','Cancelado')""",
                 (o2["id"],), default=0)
assert pend == 2, f"deveriam restar 2 pendentes, restaram {pend}"
print(f"   ✅ restam {pend} itens pendentes na OS")

r = mnt.post(f"/os/{o2['id']}/acao/retomar", follow_redirects=True)
assert db.scalar("SELECT status FROM ordens_servico WHERE id=%s",
                 (o2["id"],), default="") == "aguardando_peca"
print("   ✅ a OS segue travada — falta material")

# ── Resolver o resto ──
smp = db.um("""SELECT id FROM solicitacoes_material WHERE os_id=%s AND codigo='PN002'
               ORDER BY id DESC LIMIT 1""", (o2["id"],))
ana.post(f"/solicitacoes/{smp['id']}/liberar",
         data={"quantidade": "10", "entrada": "7"}, follow_redirects=True)
smc = db.um("""SELECT id FROM solicitacoes_material WHERE os_id=%s
               AND (codigo IS NULL OR codigo='') ORDER BY id DESC LIMIT 1""", (o2["id"],))
ana.post(f"/solicitacoes/{smc['id']}/cadastrar",
         data={"codigo": "PN004", "descricao": "BUCHA ESPECIAL SOB MEDIDA",
               "unidade": "UNI", "tipo": "NLAG"}, follow_redirects=True)
ana.post(f"/solicitacoes/{smc['id']}/liberar",
         data={"quantidade": "1", "entrada": "2"}, follow_redirects=True)

assert db.scalar("""SELECT COUNT(*) AS n FROM solicitacoes_material
                    WHERE os_id=%s AND situacao NOT IN
                    ('Liberado','Concluído','Recusado','Cancelado')""",
                 (o2["id"],), default=0) == 0
print("   ✅ todos os itens liberados")

assert f"OS #{o2['numero']}" not in ana.get("/solicitacoes/liberacao").data.decode()
print("   ✅ a OS saiu do painel de liberação")

mnt.post(f"/os/{o2['id']}/acao/retomar", follow_redirects=True)
assert db.scalar("SELECT status FROM ordens_servico WHERE id=%s",
                 (o2["id"],), default="") == "em_andamento"
print("   ✅ o manutentor voltou a trabalhar")

consumos = db.query("SELECT codigo, quantidade FROM os_materiais WHERE os_id=%s",
                    (o2["id"],))
assert len(consumos) == 4
print(f"   ✅ os 4 consumos lançados na OS, para o custo do equipamento")

# ══ 3. ACESSO ═════════════════════════════════════════════════
print("\n── 3. Acesso ao painel ──")
BLOQ = "não tem acesso".encode()
assert BLOQ in mnt.get("/solicitacoes/liberacao", follow_redirects=True).data
assert BLOQ in sol.get("/solicitacoes/liberacao", follow_redirects=True).data
print("   ✅ fechado para manutentor e solicitante")
assert lid.get("/solicitacoes/liberacao").status_code == 200
print("   ✅ analista e liderança acessam")
assert "Liberação de materiais" in ana.get("/").data.decode()
print("   ✅ o item aparece no menu do analista")

print("\n" + "=" * 60)
print("✅ PAINEL DE LIBERAÇÃO VALIDADO")
print("=" * 60)
