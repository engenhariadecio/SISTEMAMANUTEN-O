"""
Atendimento do analista:
 • peça com cadastro  → um clique em "Liberar"
 • peça sem cadastro  → ficha de cadastro e só então liberar
 • a OS só destrava quando todos os materiais forem liberados
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:teste@localhost:5432/manutencao")
os.environ.setdefault("SECRET_KEY", "teste")

import mailer
mailer.modo_teste(True)

from app import app
from werkzeug.security import generate_password_hash
import db

caixa = mailer.caixa_de_teste()

for u, n, p, e in [("lib_sol", "Solicitante Lib", "solicitante", "sol@intelbras.com.br"),
                   ("lib_mnt", "Manutentor Lib", "manutentor", "mnt@intelbras.com.br"),
                   ("lib_lid", "Líder Lib", "lider", "lid@intelbras.com.br"),
                   ("lib_ana", "Analista Lib", "analista", "ana@intelbras.com.br")]:
    db.executar("""INSERT INTO usuarios (usuario,senha_hash,nome,perfil,email)
                   VALUES (%s,%s,%s,%s,%s) ON CONFLICT (usuario) DO UPDATE
                   SET perfil=EXCLUDED.perfil, nome=EXCLUDED.nome, email=EXCLUDED.email""",
                (u, generate_password_hash("teste123"), n, p, e))


def entrar(u):
    c = app.test_client()
    c.post("/login", data={"usuario": u, "senha": "teste123"})
    return c


sol, mnt, lid, ana = entrar("lib_sol"), entrar("lib_mnt"), entrar("lib_lid"), entrar("lib_ana")
uid_mnt = db.scalar("SELECT id FROM usuarios WHERE usuario='lib_mnt'")
est = db.um("SELECT id FROM estabelecimentos WHERE nome='Matriz'")
eq = db.um("SELECT id FROM equipamentos LIMIT 1")

# Peça com cadastro e saldo zerado; a outra nem existe
db.executar("""INSERT INTO materiais (codigo, descricao, unidade, tipo, estoque_min)
               VALUES ('LIB001','ROLAMENTO 6205 ZZ','UNI','NLAG',2)
               ON CONFLICT (codigo) DO NOTHING""")
db.executar("""INSERT INTO movimentacoes (codigo,tipo,quantidade,usuario)
               VALUES ('LIB001','AJUSTE',%s,'teste')""", (-db.saldo_material("LIB001"),))

sol.post("/os/nova", data={"estabelecimento_id": est["id"], "tipo": "Industrial",
                           "equipamento_id": eq["id"],
                           "descricao_problema": "Mancal com folga"}, follow_redirects=True)
o = db.um("SELECT * FROM ordens_servico ORDER BY id DESC LIMIT 1")
lid.post(f"/os/{o['id']}/assumir", data={"responsavel_id": uid_mnt}, follow_redirects=True)

# ══ 1. MANUTENTOR REQUISITA ═══════════════════════════════════
print("── 1. Manutentor requisita ──")
print("   (nenhum pedido dá baixa sozinho — tudo passa pelo analista)")
mnt.post(f"/os/{o['id']}/material",
         data={"codigo": "LIB001", "descricao": "ROLAMENTO 6205 ZZ", "quantidade": "2"},
         follow_redirects=True)
sm1 = db.um("SELECT * FROM solicitacoes_material ORDER BY id DESC LIMIT 1")
print(f"   peça cadastrada, sem saldo → SM #{sm1['numero']}")

# Peça que não existe no catálogo
mnt.post(f"/os/{o['id']}/material",
         data={"codigo": "", "descricao": "RETENTOR ESPECIAL 45X62X8", "quantidade": "1"},
         follow_redirects=True)
sm2 = db.um("SELECT * FROM solicitacoes_material ORDER BY id DESC LIMIT 1")
print(f"   peça fora do catálogo → SM #{sm2['numero']} (tipo {sm2['tipo']})")
assert sm2["tipo"] == "Cadastro"

ap = db.query("""SELECT descricao FROM os_apontamentos WHERE os_id=%s AND tipo='material'
                 ORDER BY id""", (o["id"],))
for a in ap:
    print(f"   histórico: {a['descricao'][:66]}")
assert all(a["descricao"].startswith("Material requisitado") for a in ap)
print("   ✅ histórico registra 'Material requisitado'")

# ══ 2. A OS FICA BLOQUEADA ════════════════════════════════════
print("\n── 2. OS travada enquanto falta material ──")
r = mnt.post(f"/os/{o['id']}/acao/iniciar", follow_redirects=True)
st = db.scalar("SELECT status FROM ordens_servico WHERE id=%s", (o["id"],), default="")
print(f"   tentou iniciar → status continua '{st}'")
assert st != "em_andamento", "não deveria iniciar com material pendente"
assert "aguardando liberação".encode() in r.data
print("   ✅ bloqueado, com aviso na tela")

tela = mnt.get(f"/os/{o['id']}").data.decode()
assert "2 material(is) aguardando o analista" in tela
print("   ✅ a OS mostra quantos itens faltam")

# ══ 3. PEÇA COM CADASTRO: UM CLIQUE ═══════════════════════════
print("\n── 3. Analista libera a peça cadastrada ──")
tela = ana.get(f"/solicitacoes/{sm1['id']}").data.decode()
assert "Liberar material para" in tela and "ROLAMENTO 6205 ZZ" in tela
print("   ✅ a tela mostra o botão de liberar, com foto e saldo")

caixa.clear()
r = ana.post(f"/solicitacoes/{sm1['id']}/liberar", data={"quantidade": "2"},
             follow_redirects=True)
assert "insuficiente".encode() in r.data
print("   ✅ sem saldo, a liberação é recusada com aviso")

r = ana.post(f"/solicitacoes/{sm1['id']}/liberar",
             data={"quantidade": "2", "entrada": "5"}, follow_redirects=True)
s1 = db.um("SELECT * FROM solicitacoes_material WHERE id=%s", (sm1["id"],))
print(f"   entrada de 5 + liberação de 2 → situação '{s1['situacao']}', "
      f"saldo {db.saldo_material('LIB001'):g}")
assert s1["situacao"] == "Liberado"
assert db.saldo_material("LIB001") == 3.0
print("   ✅ um clique: entrada, baixa e liberação")

h = db.um("""SELECT comentario FROM solicitacao_historico WHERE solicitacao_id=%s
             ORDER BY id DESC LIMIT 1""", (sm1["id"],))
print(f"   histórico da SM: {h['comentario'][:64]}")
assert h["comentario"].startswith("Material liberado pelo analista")

ap = db.um("""SELECT descricao FROM os_apontamentos WHERE os_id=%s
              ORDER BY id DESC LIMIT 1""", (o["id"],))
print(f"   histórico da OS: {ap['descricao'][:64]}")
assert ap["descricao"].startswith("Material liberado pelo analista")
print("   ✅ 'Material liberado pelo analista' nos dois históricos")

assert caixa and "mnt@intelbras.com.br" in " ".join(caixa[-1]["para"])
print(f"   ✅ manutentor avisado: {caixa[-1]['assunto']}")

consumo = db.um("""SELECT * FROM os_materiais WHERE os_id=%s AND codigo='LIB001'""",
                (o["id"],))
assert consumo and consumo["baixado"] is True
print("   ✅ consumo lançado na OS")

# ══ 4. AINDA FALTA UMA — SEGUE TRAVADA ════════════════════════
print("\n── 4. Ainda falta a peça sem cadastro ──")
r = mnt.post(f"/os/{o['id']}/acao/iniciar", follow_redirects=True)
assert db.scalar("SELECT status FROM ordens_servico WHERE id=%s",
                 (o["id"],), default="") != "em_andamento"
print("   ✅ continua travada — falta 1 material")

# ══ 5. PEÇA SEM CADASTRO: FICHA PRIMEIRO ══════════════════════
print("\n── 5. Analista cadastra a peça que não existia ──")
tela = ana.get(f"/solicitacoes/{sm2['id']}").data.decode()
assert "ainda não tem cadastro" in tela and "Cadastrar material" in tela
assert "Liberar material para" not in tela
print("   ✅ sem cadastro, aparece a ficha e NÃO o botão de liberar")

r = ana.post(f"/solicitacoes/{sm2['id']}/liberar", data={"quantidade": "1"},
             follow_redirects=True)
assert "não tem cadastro".encode() in r.data
print("   ✅ tentar liberar sem cadastro é recusado")

ana.post(f"/solicitacoes/{sm2['id']}/cadastrar", data={
    "codigo": "LIB002", "descricao": "RETENTOR ESPECIAL 45X62X8", "unidade": "UNI",
    "tipo": "NLAG", "estoque_min": "1", "estoque_max": "4",
    "localizacao": "Gaveta B7", "valor_unit": "38.90"}, follow_redirects=True)
novo = db.um("SELECT * FROM materiais WHERE codigo='LIB002'")
assert novo and novo["localizacao"] == "Gaveta B7"
s2 = db.um("SELECT * FROM solicitacoes_material WHERE id=%s", (sm2["id"],))
print(f"   cadastrado LIB002 → SM agora '{s2['situacao']}', código final {s2['codigo_final']}")
assert s2["situacao"] == "Cadastrado" and s2["codigo_final"] == "LIB002"
print("   ✅ ficha preenchida vira material e alimenta a solicitação")

tela = ana.get(f"/solicitacoes/{sm2['id']}").data.decode()
assert "Liberar material para" in tela
print("   ✅ agora o botão de liberar aparece")

caixa.clear()
ana.post(f"/solicitacoes/{sm2['id']}/liberar",
         data={"quantidade": "1", "entrada": "2"}, follow_redirects=True)
assert db.scalar("SELECT situacao FROM solicitacoes_material WHERE id=%s",
                 (sm2["id"],), default="") == "Liberado"
print(f"   ✅ liberado — saldo de LIB002: {db.saldo_material('LIB002'):g}")

# ══ 6. TUDO LIBERADO: A OS DESTRAVA ═══════════════════════════
print("\n── 6. Com tudo liberado, a OS destrava ──")
tela = mnt.get(f"/os/{o['id']}").data.decode()
assert "aguardando o analista" not in tela
print("   ✅ o aviso de pendência sumiu")

mnt.post(f"/os/{o['id']}/acao/iniciar", follow_redirects=True)
st = db.scalar("SELECT status FROM ordens_servico WHERE id=%s", (o["id"],), default="")
print(f"   status agora: '{st}'")
assert st == "em_andamento"
print("   ✅ o manutentor conseguiu iniciar")

# ══ 7. PEDIDO NOVO NO MEIO DO SERVIÇO ═════════════════════════
print("\n── 7. Novo pedido durante a execução ──")
mnt.post(f"/os/{o['id']}/material",
         data={"codigo": "LIB001", "descricao": "ROLAMENTO 6205 ZZ",
               "quantidade": "9", "pausar": "1"}, follow_redirects=True)
assert db.scalar("SELECT status FROM ordens_servico WHERE id=%s",
                 (o["id"],), default="") == "aguardando_peca"
r = mnt.post(f"/os/{o['id']}/acao/retomar", follow_redirects=True)
assert db.scalar("SELECT status FROM ordens_servico WHERE id=%s",
                 (o["id"],), default="") == "aguardando_peca"
print("   ✅ não retoma enquanto o novo pedido não for liberado")

sm3 = db.um("SELECT * FROM solicitacoes_material ORDER BY id DESC LIMIT 1")
ana.post(f"/solicitacoes/{sm3['id']}/liberar",
         data={"quantidade": "6", "entrada": "10"}, follow_redirects=True)
mnt.post(f"/os/{o['id']}/acao/retomar", follow_redirects=True)
assert db.scalar("SELECT status FROM ordens_servico WHERE id=%s",
                 (o["id"],), default="") == "em_andamento"
print("   ✅ liberado, ele retoma na hora")

# ══ 8. O ANALISTA NÃO SOLICITA ════════════════════════════════
print("\n── 8. Papéis ──")
BLOQ = "não tem acesso".encode()
assert BLOQ in ana.get("/solicitacoes/nova", follow_redirects=True).data
print("   ✅ a analista atende, não solicita")
assert BLOQ in mnt.get("/solicitacoes/nova", follow_redirects=True).data
print("   ✅ o manutentor pede só de dentro da OS")

# Dentro da OS o painel continua funcionando
tela = mnt.get(f"/os/{o['id']}").data.decode()
assert 'id="mCatalogo"' in tela and "Pedir peça" in tela
assert "Solicitação detalhada" not in tela
print("   ✅ o catálogo dentro da OS é o único caminho para ele")

assert lid.get("/solicitacoes/nova", follow_redirects=True).status_code == 200
print("   ✅ a liderança usa o formulário avulso para repor estoque")
assert BLOQ in mnt.post(f"/solicitacoes/{sm1['id']}/liberar",
                        data={"quantidade": "1"}, follow_redirects=True).data
print("   ✅ o manutentor não libera para si mesmo")

print("\n" + "=" * 60)
print("✅ LIBERAÇÃO DE MATERIAL VALIDADA")
print("=" * 60)
