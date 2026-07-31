"""
Fluxo completo:
solicitante abre → cai na triagem do líder → líder escolhe o manutentor →
manutentor é notificado, executa, pede peça → conclui → solicitante aprova.
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:teste@localhost:5432/manutencao")
os.environ.setdefault("SECRET_KEY", "teste")
os.environ.setdefault("APP_URL", "https://manutencao.decio.com.br")

import mailer
mailer.APP_URL = "https://manutencao.decio.com.br"
mailer.modo_teste(True)

from app import app
from werkzeug.security import generate_password_hash
import db

caixa = mailer.caixa_de_teste()
EQUIPE = [
    ("charles", "Charles Pfleger", "solicitante", "ch1000328@intelbras.com.br"),
    ("jaime", "Jaime Matias", "manutentor", "ja1001070@intelbras.com.br"),
    ("emerson", "Emerson de Jesus", "manutentor", "em1000900@intelbras.com.br"),
    ("lourivaldo", "Lourivaldo Vieira", "lider", "lo1000673@intelbras.com.br"),
    ("maria", "Maria Geucilene", "analista", "ma1001029@intelbras.com.br"),
    ("miguel", "Miguel Bartilotti", "supervisao", "mi1000622@intelbras.com.br"),
]
for usuario, nome, perfil, mail in EQUIPE:
    db.executar("""INSERT INTO usuarios (usuario, senha_hash, nome, perfil, email)
                   VALUES (%s,%s,%s,%s,%s) ON CONFLICT (usuario) DO UPDATE
                   SET perfil=EXCLUDED.perfil, nome=EXCLUDED.nome, email=EXCLUDED.email""",
                (usuario, generate_password_hash("teste123"), nome, perfil, mail))

def entrar(usuario):
    cli = app.test_client()
    cli.post("/login", data={"usuario": usuario, "senha": "teste123"})
    return cli

sol = entrar("charles")
lid = entrar("lourivaldo")
jaime = entrar("jaime")
emerson = entrar("emerson")
ana = entrar("maria")

def para(msg):
    return " ".join(msg["para"]).lower()

est = db.um("SELECT id FROM estabelecimentos WHERE nome='Matriz'")
eq = db.um("SELECT id, codigo FROM equipamentos WHERE codigo='PU02-00'")
BLOQUEIO = b"n\xc3\xa3o tem acesso"

# ══ 1. SOLICITANTE ABRE ═══════════════════════════════════════
print("── 1. Solicitante abre a OS ──")
caixa.clear()
sol.post("/os/nova", data={"estabelecimento_id": est["id"], "tipo": "Industrial",
                           "equipamento_id": eq["id"], "maquina_parada": "on",
                           "descricao_problema": "Auto index não está funcionando"},
         follow_redirects=True)
o = db.um("SELECT * FROM ordens_servico ORDER BY id DESC LIMIT 1")
print(f"   OS #{o['numero']} · status '{o['status']}' · responsável {o['responsavel_id']}")
assert o["status"] == "aberta" and o["responsavel_id"] is None
print("   ✅ nasce sem responsável, aguardando triagem")

m = caixa[-1]
print(f"   e-mail: {m['assunto']}")
assert "lo1000673" in para(m) and "mi1000622" in para(m), "liderança deveria receber"
assert "ja1001070" not in para(m), "manutentor NÃO deve ser avisado ainda"
assert "em1000900" not in para(m), "manutentor NÃO deve ser avisado ainda"
print("   ✅ só líder e supervisão foram avisados — nenhum manutentor")

# ══ 2. A OS NÃO APARECE PARA O MANUTENTOR ═════════════════════
print("\n── 2. Antes da triagem ──")
r = jaime.get("/os/")
assert f"#{o['numero']}".encode() not in r.data, "não deveria aparecer na lista do manutentor"
print("   ✅ não aparece na lista do manutentor")

r = jaime.post(f"/os/{o['id']}/acao/iniciar", follow_redirects=True)
assert db.scalar("SELECT status FROM ordens_servico WHERE id=%s",
                 (o["id"],), default="") == "aberta"
print("   ✅ manutentor não consegue iniciar uma OS não distribuída")

r = jaime.post(f"/os/{o['id']}/assumir", data={"responsavel_id": 1}, follow_redirects=True)
assert BLOQUEIO in r.data, "manutentor não pode se auto-atribuir"
assert db.scalar("SELECT responsavel_id FROM ordens_servico WHERE id=%s",
                 (o["id"],), default=None) is None
print("   ✅ manutentor não pode se auto-atribuir")

# ══ 3. TRIAGEM DO LÍDER ═══════════════════════════════════════
print("\n── 3. Triagem do líder ──")
r = lid.get("/os/triagem")
assert r.status_code == 200 and f"#{o['numero']}".encode() in r.data
print("   ✅ a OS aparece na tela de triagem do líder")
assert BLOQUEIO in sol.get("/os/triagem", follow_redirects=True).data
assert BLOQUEIO in jaime.get("/os/triagem", follow_redirects=True).data
print("   ✅ triagem fechada para solicitante e manutentor")

caixa.clear()
id_jaime = db.scalar("SELECT id FROM usuarios WHERE usuario='jaime'")
lid.post(f"/os/{o['id']}/assumir",
         data={"responsavel_id": id_jaime, "voltar": "triagem"}, follow_redirects=True)
o2 = db.um("SELECT * FROM ordens_servico WHERE id=%s", (o["id"],))
print(f"   status agora: '{o2['status']}' · responsável: {o2['responsavel_id']}")
assert o2["status"] == "atribuida" and o2["responsavel_id"] == id_jaime
print("   ✅ status virou 'atribuida' com o manutentor escolhido")

m = caixa[-1]
print(f"   e-mail: {m['assunto']}")
assert para(m) == "jaime matias <ja1001070@intelbras.com.br>", m["para"]
print("   ✅ só o manutentor escolhido foi notificado")

ap = db.um("""SELECT * FROM os_apontamentos WHERE os_id=%s AND tipo='atribuicao'
              ORDER BY id DESC LIMIT 1""", (o["id"],))
assert ap and "Lourivaldo" in ap["descricao"] and "Jaime" in ap["descricao"]
print(f"   ✅ apontamento: {ap['descricao']}")

assert lid.get("/os/triagem").status_code == 200
assert f"#{o['numero']}".encode() not in lid.get("/os/triagem").data
print("   ✅ saiu da fila de triagem")

# ══ 4. AGORA SIM O MANUTENTOR ═════════════════════════════════
print("\n── 4. Manutentor designado ──")
assert f"#{o['numero']}".encode() in jaime.get("/os/").data
print("   ✅ a OS entrou na lista do Jaime")

assert f"#{o['numero']}".encode() not in emerson.get("/os/").data
print("   ✅ não aparece para o outro manutentor")
r = emerson.post(f"/os/{o['id']}/acao/iniciar", follow_redirects=True)
assert db.scalar("SELECT status FROM ordens_servico WHERE id=%s",
                 (o["id"],), default="") == "atribuida"
print("   ✅ outro manutentor não consegue iniciar a OS alheia")

jaime.post(f"/os/{o['id']}/acao/iniciar", follow_redirects=True)
assert db.scalar("SELECT status FROM ordens_servico WHERE id=%s",
                 (o["id"],), default="") == "em_andamento"
print("   ✅ o manutentor designado inicia normalmente")

# ══ 5. PEDIDO DE PEÇA ═════════════════════════════════════════
print("\n── 5. Pedido de peça ──")
db.executar("""INSERT INTO materiais (codigo, descricao, unidade, tipo, estoque_min)
               VALUES ('7400155','CORREIA SINCRONIZADA PU AT10','UNI','NLAG',1)
               ON CONFLICT (codigo) DO NOTHING""")
db.executar("""INSERT INTO movimentacoes (codigo, tipo, quantidade, usuario)
               VALUES ('7400155','AJUSTE',%s,'teste')""",
            (2 - db.saldo_material("7400155"),))

caixa.clear()
jaime.post(f"/os/{o['id']}/material", data={"codigo": "7400155", "quantidade": "1"},
           follow_redirects=True)
assert db.saldo_material("7400155") == 1.0
assert not caixa, "com saldo, não abre solicitação nem manda e-mail"
print("   ✅ tinha saldo → baixa direta, sem formulário")

caixa.clear()
jaime.post(f"/os/{o['id']}/material",
           data={"codigo": "7400155", "quantidade": "4", "pausar": "1"},
           follow_redirects=True)
sm = db.um("SELECT * FROM solicitacoes_material ORDER BY id DESC LIMIT 1")
print(f"   pediu 4, havia 1 → SM #{sm['numero']} de {float(sm['quantidade']):g}")
assert float(sm["quantidade"]) == 3.0
assert db.scalar("SELECT status FROM ordens_servico WHERE id=%s",
                 (o["id"],), default="") == "aguardando_peca"
assert "ma1001029" in para(caixa[-1])
print("   ✅ faltou → formulário para a analista, OS pausada")

caixa.clear()
ana.post(f"/solicitacoes/{sm['id']}",
         data={"situacao": "Recebido", "comentario": "Disponível no almoxarifado"},
         follow_redirects=True)
assert "ja1001070" in para(caixa[-1])
print("   ✅ chegou o material → Jaime avisado")
jaime.post(f"/os/{o['id']}/acao/retomar", follow_redirects=True)

# ══ 6. CONCLUSÃO E APROVAÇÃO ══════════════════════════════════
print("\n── 6. Conclusão ──")
caixa.clear()
d = db.um("SELECT id FROM defeitos WHERE nome='Mecânico'")
ca = db.um("SELECT id FROM causas LIMIT 1")
jaime.post(f"/os/{o['id']}/concluir",
           data={"defeito_id": d["id"], "causa_id": ca["id"],
                 "acao_realizada": "Substituída a correia e realinhado o index",
                 "liberar_equipamento": "on"}, follow_redirects=True)
assert db.scalar("SELECT status FROM ordens_servico WHERE id=%s",
                 (o["id"],), default="") == "aguardando_aprovacao"
m = caixa[-1]
print(f"   e-mail: {m['assunto']}")
assert para(m) == "charles pfleger <ch1000328@intelbras.com.br>", m["para"]
print("   ✅ e-mail foi só para quem abriu a OS")
assert "Aprovar ou reprovar" in m["html"] and "Substituída a correia" in m["html"]
print("   ✅ traz a ação realizada e o botão de aprovar/reprovar")

caixa.clear()
sol.post(f"/os/{o['id']}/aprovar",
         data={"decisao": "aprovar", "comentario": "Máquina rodando normal"},
         follow_redirects=True)
final = db.um("SELECT * FROM ordens_servico WHERE id=%s", (o["id"],))
assert final["status"] == "concluida" and final["aprovado"] is True
assert "ja1001070" in para(caixa[-1])
print("   ✅ aprovada e finalizada; Jaime avisado")

# ══ 7. EMERGÊNCIA SEGUE DIRETO ════════════════════════════════
print("\n── 7. Intervenção de emergência ──")
jaime.post("/os/intervencao", data={"equipamento_id": eq["id"],
                                    "sintoma": "Vazamento de ar — emergência"},
           follow_redirects=True)
oe = db.um("SELECT * FROM ordens_servico WHERE origem='intervencao' ORDER BY id DESC LIMIT 1")
assert oe["status"] == "em_andamento" and oe["responsavel_id"] == id_jaime
print("   ✅ emergência não passa por triagem — o manutentor abre e já executa")

# ══ 8. REPROVAÇÃO VOLTA PARA O MESMO ══════════════════════════
print("\n── 8. Reprovação ──")
sol.post("/os/nova", data={"estabelecimento_id": est["id"], "tipo": "Industrial",
                           "equipamento_id": eq["id"],
                           "descricao_problema": "Ruído na torreta"},
         follow_redirects=True)
o3 = db.um("SELECT * FROM ordens_servico ORDER BY id DESC LIMIT 1")
lid.post(f"/os/{o3['id']}/assumir", data={"responsavel_id": id_jaime}, follow_redirects=True)
jaime.post(f"/os/{o3['id']}/acao/iniciar", follow_redirects=True)
jaime.post(f"/os/{o3['id']}/concluir",
           data={"defeito_id": d["id"], "causa_id": ca["id"],
                 "acao_realizada": "Reaperto geral"}, follow_redirects=True)
sol.post(f"/os/{o3['id']}/aprovar",
         data={"decisao": "reprovar", "comentario": "Ruído continua"},
         follow_redirects=True)
rep = db.um("SELECT * FROM ordens_servico WHERE id=%s", (o3["id"],))
assert rep["status"] == "reprovada" and rep["responsavel_id"] == id_jaime
print("   ✅ reprovada continua com o mesmo manutentor, sem voltar à triagem")
assert f"#{o3['numero']}".encode() in jaime.get("/os/").data
jaime.post(f"/os/{o3['id']}/reabrir", follow_redirects=True)
assert db.scalar("SELECT status FROM ordens_servico WHERE id=%s",
                 (o3["id"],), default="") == "em_andamento"
print("   ✅ ele retoma direto")

print("\n" + "=" * 60)
print("✅ FLUXO COM TRIAGEM VALIDADO")
print("=" * 60)
