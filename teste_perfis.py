"""
Testa a separação de perfis:
 • manutentor  → fila de OS, emergência e pedido de peça (sem acesso ao depósito)
 • analista    → dono do depósito NLAG e das solicitações
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:teste@localhost:5432/manutencao")
os.environ.setdefault("SECRET_KEY", "teste")

from app import app
from werkzeug.security import generate_password_hash
import db

adm = app.test_client()
adm.post("/login", data={"usuario": "admin", "senha": "teste123"})

# ── Usuários dos dois perfis ──
for usuario, nome, perfil in [("jaime", "Jaime Matias", "manutentor"),
                              ("maria", "Maria Geucilene", "analista"),
                              ("charles", "Charles Pfleger", "solicitante")]:
    db.executar("""INSERT INTO usuarios (usuario, senha_hash, nome, perfil)
                   VALUES (%s,%s,%s,%s) ON CONFLICT (usuario) DO UPDATE SET perfil=EXCLUDED.perfil""",
                (usuario, generate_password_hash("teste123"), nome, perfil))

mnt = app.test_client(); mnt.post("/login", data={"usuario": "jaime", "senha": "teste123"})
ana = app.test_client(); ana.post("/login", data={"usuario": "maria", "senha": "teste123"})
sol = app.test_client(); sol.post("/login", data={"usuario": "charles", "senha": "teste123"})

BLOQUEIO = b"n\xc3\xa3o tem acesso"


def bloqueado(cli, rota):
    r = cli.get(rota, follow_redirects=True)
    return BLOQUEIO in r.data


def liberado(cli, rota):
    r = cli.get(rota, follow_redirects=True)
    return r.status_code == 200 and BLOQUEIO not in r.data


# ══ DEPÓSITO NLAG ══════════════════════════════════════════════
print("── Depósito NLAG ──")
DEPOSITO = ["/materiais/", "/materiais/entrada", "/materiais/saida", "/materiais/coletor",
            "/materiais/cadastro", "/materiais/etiquetas", "/materiais/importar",
            "/materiais/historico", "/materiais/alertas", "/materiais/exportar"]
for rota in DEPOSITO:
    assert bloqueado(mnt, rota), f"manutentor NÃO deveria acessar {rota}"
print(f"   ✅ manutentor bloqueado em {len(DEPOSITO)} telas do depósito")

for rota in DEPOSITO:
    assert liberado(ana, rota), f"analista deveria acessar {rota}"
print(f"   ✅ analista tem acesso a todas as {len(DEPOSITO)}")

r = mnt.get("/")
assert "Depósito NLAG".encode() not in r.data, "menu do depósito não deveria aparecer"
assert "Depósito NLAG".encode() in ana.get("/").data
print("   ✅ seção 'Depósito NLAG' some do menu do manutentor")

# Escrita direta também é barrada
r = mnt.post("/materiais/entrada", data={"codigo": "X", "quantidade": "5"},
             follow_redirects=True)
assert BLOQUEIO in r.data, "POST de entrada deveria ser barrado"
print("   ✅ POST direto no depósito também é barrado")

# ══ O QUE O MANUTENTOR PODE ════════════════════════════════════
print("\n── Acessos do manutentor ──")
for rota in ["/", "/os/", "/os/tablet", "/os/nova", "/os/intervencao"]:
    assert liberado(mnt, rota), f"manutentor deveria acessar {rota}"
print("   ✅ painel, fila, tablet, abertura de OS e intervenção de emergência")
for rota in ["/admin/usuarios", "/admin/equipamentos", "/indicadores/", "/os/triagem"]:
    assert bloqueado(mnt, rota), f"manutentor NÃO deveria acessar {rota}"
print("   ✅ bloqueado em administração, indicadores e triagem")

# ══ PEDIDO DE PEÇA — COM SALDO ═════════════════════════════════
print("\n── Pedido de peça com saldo no NLAG ──")
db.executar("""INSERT INTO materiais (codigo, descricao, unidade, tipo, estoque_min,
                 estoque_max, valor_unit)
               VALUES ('7609066','DESENGRIPANTE SPRAY 300ML','UNI','NLAG',5,20,28.50)
               ON CONFLICT (codigo) DO NOTHING""")
db.executar("""INSERT INTO movimentacoes (codigo, tipo, quantidade, usuario)
               VALUES ('7609066','AJUSTE',%s,'teste')""",
            (10 - db.saldo_material("7609066"),))
print(f"   saldo inicial: {db.saldo_material('7609066'):g}")

est = db.um("SELECT id FROM estabelecimentos WHERE nome='Matriz'")
eq = db.um("SELECT id FROM equipamentos WHERE codigo='PU01-00'")
sol.post("/os/nova", data={"estabelecimento_id": est["id"], "tipo": "Industrial",
                           "equipamento_id": eq["id"],
                           "descricao_problema": "Torreta travada — teste de peça"},
         follow_redirects=True)
o = db.um("SELECT * FROM ordens_servico ORDER BY id DESC LIMIT 1")

# A OS passa pela triagem antes de chegar ao manutentor
adm = app.test_client()
adm.post("/login", data={"usuario": "admin", "senha": "teste123"})
id_jaime = db.scalar("SELECT id FROM usuarios WHERE usuario='jaime'")
adm.post(f"/os/{o['id']}/assumir", data={"responsavel_id": id_jaime},
         follow_redirects=True)
mnt.post(f"/os/{o['id']}/acao/iniciar", follow_redirects=True)

sm_antes = db.scalar("SELECT COUNT(*) AS n FROM solicitacoes_material")
r = mnt.post(f"/os/{o['id']}/material",
             data={"codigo": "7609066", "quantidade": "3"}, follow_redirects=True)
assert r.status_code == 200
saldo = db.saldo_material("7609066")
print(f"   pediu 3 → saldo {saldo:g}")
assert saldo == 7.0, f"deveria dar baixa: {saldo}"
assert db.scalar("SELECT COUNT(*) AS n FROM solicitacoes_material") == sm_antes, \
    "não deveria abrir solicitação"
mat_os = db.um("SELECT * FROM os_materiais WHERE os_id=%s ORDER BY id DESC LIMIT 1", (o["id"],))
assert mat_os["baixado"] is True and float(mat_os["quantidade"]) == 3.0
print("   ✅ baixa automática, sem solicitação, consumo registrado na OS")

st = db.scalar("SELECT status FROM ordens_servico WHERE id=%s", (o["id"],), default="")
assert st == "em_andamento", f"OS não deveria pausar: {st}"
print("   ✅ OS continua em andamento")

# ══ PEDIDO DE PEÇA — SALDO PARCIAL ═════════════════════════════
print("\n── Pedido maior que o saldo ──")
r = mnt.post(f"/os/{o['id']}/material",
             data={"codigo": "7609066", "quantidade": "10", "pausar": "1"},
             follow_redirects=True)
saldo = db.saldo_material("7609066")
sm = db.um("SELECT * FROM solicitacoes_material ORDER BY id DESC LIMIT 1")
print(f"   pediu 10, havia 7 → saldo {saldo:g} · SM #{sm['numero']} de {sm['quantidade']:g}")
assert saldo == 0.0, f"deveria zerar: {saldo}"
assert float(sm["quantidade"]) == 3.0, f"deveria solicitar 3: {sm['quantidade']}"
assert sm["os_id"] == o["id"] and sm["tipo"] == "Estoque NLAG"
print("   ✅ baixou 7 e solicitou os 3 que faltaram")

st = db.scalar("SELECT status FROM ordens_servico WHERE id=%s", (o["id"],), default="")
assert st == "aguardando_peca", f"OS deveria pausar: {st}"
print("   ✅ OS pausada aguardando a peça")

notif = db.um("""SELECT n.* FROM notificacoes n JOIN usuarios u ON u.id=n.usuario_id
                 WHERE u.perfil='analista' ORDER BY n.id DESC LIMIT 1""")
assert notif and "SM" in notif["titulo"], "analista deveria ser notificado"
print(f"   ✅ analista notificado: {notif['titulo']}")

alerta = db.um("""SELECT n.* FROM notificacoes n JOIN usuarios u ON u.id=n.usuario_id
                  WHERE u.perfil='analista' AND n.titulo LIKE 'Estoque mínimo%%'
                  ORDER BY n.id DESC LIMIT 1""")
assert alerta, "alerta de estoque mínimo deveria disparar"
print("   ✅ alerta de estoque mínimo disparado ao analista")

# ══ PEÇA SEM CADASTRO ══════════════════════════════════════════
print("\n── Peça sem cadastro ──")
r = mnt.post(f"/os/{o['id']}/material",
             data={"codigo": "", "descricao": "Rolamento 6202 blindado",
                   "quantidade": "2"}, follow_redirects=True)
sm = db.um("SELECT * FROM solicitacoes_material ORDER BY id DESC LIMIT 1")
print(f"   SM #{sm['numero']} · {sm['descricao']} · tipo {sm['tipo']}")
assert sm["tipo"] == "Cadastro", f"deveria ser Cadastro: {sm['tipo']}"
assert sm["codigo"] is None
print("   ✅ virou solicitação de cadastro para o analista")

# ══ MATERIAL HIBE/ERSA ═════════════════════════════════════════
print("\n── Material HIBE/ERSA (saldo do SAP) ──")
db.executar("""INSERT INTO materiais (codigo, descricao, unidade, tipo, saldo_sap)
               VALUES ('7400205','CORREIA SINCRONIZADA ATP10 1010','UNI','HIBE',4)
               ON CONFLICT (codigo) DO UPDATE SET tipo='HIBE', saldo_sap=4""")
mnt.post(f"/os/{o['id']}/material",
         data={"codigo": "7400205", "quantidade": "1"}, follow_redirects=True)
sm = db.um("SELECT * FROM solicitacoes_material ORDER BY id DESC LIMIT 1")
assert sm["tipo"] == "HIBE/ERSA", f"tipo errado: {sm['tipo']}"
assert "SAP" in (sm["observacoes"] or "")
print(f"   ✅ SM #{sm['numero']} tipo HIBE/ERSA, com o saldo do SAP na observação")

# ══ O ANALISTA ATENDE ══════════════════════════════════════════
print("\n── Analista atende a solicitação ──")
alvo = db.um("SELECT * FROM solicitacoes_material WHERE tipo='Estoque NLAG' "
             "ORDER BY id DESC LIMIT 1")
assert liberado(ana, f"/solicitacoes/{alvo['id']}")
r = ana.post(f"/solicitacoes/{alvo['id']}",
             data={"situacao": "Recebido", "comentario": "Material disponível no almoxarifado"},
             follow_redirects=True)
assert r.status_code == 200
assert db.scalar("SELECT situacao FROM solicitacoes_material WHERE id=%s",
                 (alvo["id"],), default="") == "Recebido"
print("   ✅ analista atualizou a situação")

# Manutentor não pode tratar solicitação
r = mnt.post(f"/solicitacoes/{alvo['id']}", data={"situacao": "Concluído"},
             follow_redirects=True)
assert db.scalar("SELECT situacao FROM solicitacoes_material WHERE id=%s",
                 (alvo["id"],), default="") == "Recebido", "manutentor alterou indevidamente"
print("   ✅ manutentor não consegue mudar a situação da solicitação")

# Analista repõe o estoque e o manutentor retoma
ana.post("/materiais/entrada", data={"codigo": "7609066", "quantidade": "20",
                                     "observacao": "Reposição"}, follow_redirects=True)
print(f"   analista repôs → saldo {db.saldo_material('7609066'):g}")
mnt.post(f"/os/{o['id']}/acao/retomar", follow_redirects=True)
assert db.scalar("SELECT status FROM ordens_servico WHERE id=%s",
                 (o["id"],), default="") == "em_andamento"
print("   ✅ manutentor retomou a OS")

# ══ EMERGÊNCIA ═════════════════════════════════════════════════
print("\n── OS de emergência pelo manutentor ──")
r = mnt.post("/os/intervencao", data={"equipamento_id": eq["id"],
                                      "sintoma": "Vazamento de ar — emergência"},
             follow_redirects=True)
assert r.status_code == 200
oe = db.um("SELECT * FROM ordens_servico WHERE origem='intervencao' ORDER BY id DESC LIMIT 1")
assert oe["status"] == "em_andamento" and oe["responsavel_id"]
print(f"   ✅ OS #{oe['numero']} aberta pelo próprio manutentor, cronômetro rodando")

# ── Consulta de saldo continua disponível ao pedir peça ──
r = mnt.get("/materiais/api/7609066")
assert r.status_code == 200 and r.get_json()["ok"]
print("   ✅ manutentor consulta a disponibilidade da peça, sem abrir o depósito")

print("\n" + "=" * 58)
print("✅ PERFIS E FLUXO DE PEÇA VALIDADOS")
print("=" * 58)
