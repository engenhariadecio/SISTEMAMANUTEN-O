"""Testa os disparos de e-mail em cada evento do fluxo."""
import os
import re

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


def limpar():
    caixa.clear()


def ultimo():
    assert caixa, "nenhum e-mail foi enviado"
    return caixa[-1]


def destinatarios(msg):
    return " ".join(msg["para"]).lower()


# ── Equipe com e-mail ──
EQUIPE = [
    ("charles", "Charles Pfleger", "solicitante", "ch1000328@intelbras.com.br"),
    ("jaime3", "Jaime Matias", "manutentor", "ja1001070@intelbras.com.br"),
    ("emerson", "Emerson de Jesus", "manutentor", "em1000900@intelbras.com.br"),
    ("lourivaldo", "Lourivaldo Vieira", "lider", "lo1000673@intelbras.com.br"),
    ("maria3", "Maria Geucilene", "analista", "ma1001029@intelbras.com.br"),
    ("miguel", "Miguel Bartilotti", "supervisao", "mi1000622@intelbras.com.br"),
    ("semmail", "Fulano Sem E-mail", "manutentor", None),
]
for usuario, nome, perfil, mail in EQUIPE:
    db.executar("""INSERT INTO usuarios (usuario, senha_hash, nome, perfil, email)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (usuario) DO UPDATE
                   SET perfil=EXCLUDED.perfil, nome=EXCLUDED.nome, email=EXCLUDED.email""",
                (usuario, generate_password_hash("teste123"), nome, perfil, mail))

sol = app.test_client(); sol.post("/login", data={"usuario": "charles", "senha": "teste123"})
mnt = app.test_client(); mnt.post("/login", data={"usuario": "jaime3", "senha": "teste123"})
lid = app.test_client(); lid.post("/login", data={"usuario": "lourivaldo", "senha": "teste123"})
ana = app.test_client(); ana.post("/login", data={"usuario": "maria3", "senha": "teste123"})

est = db.um("SELECT id FROM estabelecimentos WHERE nome='Matriz'")
eq = db.um("SELECT id, codigo FROM equipamentos WHERE codigo='CO03-00'")

# ══ 1. OS ABERTA → EQUIPE DE MANUTENÇÃO ═══════════════════════
print("── 1. OS aberta ──")
limpar()
sol.post("/os/nova", data={"estabelecimento_id": est["id"], "tipo": "Industrial",
                           "equipamento_id": eq["id"], "maquina_parada": "on",
                           "descricao_problema": "Compressor desarmando por sobrecarga"},
         follow_redirects=True)
o = db.um("SELECT * FROM ordens_servico ORDER BY id DESC LIMIT 1")
m = ultimo()
print(f"   assunto: {m['assunto']}")
print(f"   para: {len(m['para'])} destinatário(s)")
assert f"OS #{o['numero']}" in m["assunto"]
for esperado in ("lo1000673", "mi1000622"):
    assert esperado in destinatarios(m), f"{esperado} deveria receber"
for nao in ("ja1001070", "em1000900", "ch1000328", "ma1001029"):
    assert nao not in destinatarios(m), f"{nao} não deveria receber na abertura"
print("   ✅ foi só para a liderança — a OS aguarda triagem")
assert "Compressor desarmando" in m["html"]
assert "CO03-00" in m["html"]
assert "SIM — produção interrompida" in m["html"]
assert "https://manutencao.decio.com.br/os/" in m["html"]
print("   ✅ corpo traz problema, equipamento, máquina parada e link absoluto")
assert "Compressor desarmando" in m["texto"]
print("   ✅ versão em texto puro incluída")

# ══ 2. OS ATRIBUÍDA → MANUTENTOR ══════════════════════════════
print("\n── 2. OS atribuída ──")
limpar()
jaime = db.um("SELECT id FROM usuarios WHERE usuario='jaime3'")
assert lid.get("/os/triagem").status_code == 200
lid.post(f"/os/{o['id']}/assumir", data={"responsavel_id": jaime["id"]},
         follow_redirects=True)
m = ultimo()
print(f"   assunto: {m['assunto']}")
assert m["para"] == [f"Jaime Matias <ja1001070@intelbras.com.br>"], m["para"]
print("   ✅ só o manutentor designado recebeu")
assert "atribuída a você" in m["assunto"]

# ══ 3. OS CONCLUÍDA → SOLICITANTE ═════════════════════════════
print("\n── 3. OS concluída ──")
limpar()
mnt.post(f"/os/{o['id']}/acao/iniciar", follow_redirects=True)
d = db.um("SELECT id FROM defeitos WHERE nome='Elétrico'")
ca = db.um("SELECT id FROM causas LIMIT 1")
mnt.post(f"/os/{o['id']}/concluir",
         data={"defeito_id": d["id"], "causa_id": ca["id"],
               "acao_realizada": "Substituído contator e reapertadas as conexões",
               "liberar_equipamento": "on"}, follow_redirects=True)
m = ultimo()
print(f"   assunto: {m['assunto']}")
assert "ch1000328" in destinatarios(m), "solicitante deveria receber"
assert len(m["para"]) == 1, f"só o solicitante: {m['para']}"
print("   ✅ foi só para quem abriu a OS")
assert "aprovação" in m["assunto"].lower()
for esperado in ("Substituído contator", "Elétrico", "Jaime Matias",
                 "Aprovar ou reprovar"):
    assert esperado in m["html"], f"faltou no corpo: {esperado}"
print("   ✅ traz ação realizada, defeito, executante e botão de aprovação")

# ══ 4. REPROVADA → MANUTENTOR + LIDERANÇA ═════════════════════
print("\n── 4. OS reprovada ──")
limpar()
sol.post(f"/os/{o['id']}/aprovar",
         data={"decisao": "reprovar", "comentario": "Voltou a desarmar hoje cedo"},
         follow_redirects=True)
m = ultimo()
print(f"   assunto: {m['assunto']}")
assert "ja1001070" in destinatarios(m) and "lo1000673" in destinatarios(m)
assert "mi1000622" in destinatarios(m)
print("   ✅ manutentor, líder e supervisão avisados")
assert "Voltou a desarmar" in m["html"]

# ══ 5. APROVADA → MANUTENTOR ══════════════════════════════════
print("\n── 5. OS aprovada ──")
limpar()
mnt.post(f"/os/{o['id']}/reabrir", follow_redirects=True)
mnt.post(f"/os/{o['id']}/concluir",
         data={"defeito_id": d["id"], "causa_id": ca["id"],
               "acao_realizada": "Trocado o disjuntor e refeito o dimensionamento"},
         follow_redirects=True)
limpar()
sol.post(f"/os/{o['id']}/aprovar",
         data={"decisao": "aprovar", "comentario": "Rodando normal desde ontem"},
         follow_redirects=True)
m = ultimo()
print(f"   assunto: {m['assunto']}")
assert "ja1001070" in destinatarios(m) and len(m["para"]) == 1
print("   ✅ manutentor avisado da aprovação")

# ══ 6. PEÇA SOLICITADA → ANALISTA ═════════════════════════════
print("\n── 6. Peça solicitada ──")
db.executar("""INSERT INTO materiais (codigo, descricao, unidade, tipo, estoque_min)
               VALUES ('7000715','FILTRO SEPARADOR AR/OLEO FSBS4','UNI','NLAG',2)
               ON CONFLICT (codigo) DO NOTHING""")
sol.post("/os/nova", data={"estabelecimento_id": est["id"], "tipo": "Industrial",
                           "equipamento_id": eq["id"],
                           "descricao_problema": "Troca do filtro separador"},
         follow_redirects=True)
o2 = db.um("SELECT * FROM ordens_servico ORDER BY id DESC LIMIT 1")
lid.post(f"/os/{o2['id']}/assumir", data={"responsavel_id": jaime["id"]},
         follow_redirects=True)
mnt.post(f"/os/{o2['id']}/acao/iniciar", follow_redirects=True)
limpar()
mnt.post(f"/os/{o2['id']}/material",
         data={"codigo": "7000715", "quantidade": "2", "pausar": "1"},
         follow_redirects=True)
m = ultimo()
print(f"   assunto: {m['assunto']}")
assert "ma1001029" in destinatarios(m), "analista deveria receber"
assert "lo1000673" in destinatarios(m), "líder deveria receber"
print("   ✅ analista e líder avisados da falta de peça")
assert "FILTRO SEPARADOR" in m["html"]

# ══ 7. MATERIAL RECEBIDO → MANUTENTOR ═════════════════════════
print("\n── 7. Material recebido ──")
sm = db.um("SELECT * FROM solicitacoes_material ORDER BY id DESC LIMIT 1")
limpar()
ana.post(f"/solicitacoes/{sm['id']}",
         data={"situacao": "Recebido", "comentario": "Retirar no almoxarifado"},
         follow_redirects=True)
m = ultimo()
print(f"   assunto: {m['assunto']}")
assert "ja1001070" in destinatarios(m), "manutentor da OS deveria receber"
print("   ✅ manutentor avisado da chegada")
assert "Retomar a OS" in m["html"]

# ══ DESLIGAR UM EVENTO ════════════════════════════════════════
print("\n── Desligar um evento ──")
adm = app.test_client()
adm.post("/login", data={"usuario": "admin", "senha": "teste123"})
assert adm.get("/admin/email").status_code == 200
print("   ✅ tela de configuração abre")

adm.post("/admin/email", data={"acao": "eventos", "ev_os_concluida": "1"},
         follow_redirects=True)
assert mailer.evento_ativo("os_concluida") is True
assert mailer.evento_ativo("os_aberta") is False, "os_aberta deveria ter sido desligado"
print("   ✅ preferências salvas (só os marcados ficam ligados)")

limpar()
sol.post("/os/nova", data={"estabelecimento_id": est["id"], "tipo": "Industrial",
                           "equipamento_id": eq["id"],
                           "descricao_problema": "Teste com evento desligado"},
         follow_redirects=True)
assert not caixa, "não deveria enviar e-mail com o evento desligado"
print("   ✅ evento desligado não dispara e-mail")

# Religar
adm.post("/admin/email", data={"acao": "eventos", **{f"ev_{k}": "1" for k in mailer.EVENTOS}},
         follow_redirects=True)
assert mailer.evento_ativo("os_aberta") is True
print("   ✅ religado")

# ══ ROBUSTEZ ══════════════════════════════════════════════════
print("\n── Robustez ──")
assert mailer.enviar_agora([], "x", "<p>y</p>")[0] is False
print("   ✅ sem destinatário não tenta enviar")

usuarios_sem = mailer.emails_dos_usuarios(
    [db.scalar("SELECT id FROM usuarios WHERE usuario='semmail'", default=0)])
assert usuarios_sem == [], "usuário sem e-mail não deveria entrar na lista"
print("   ✅ usuário sem e-mail é ignorado, sem quebrar o envio")

for invalido in ("", "abc", "a@b", None):
    assert not mailer.email_valido(invalido), f"deveria recusar: {invalido}"
assert mailer.email_valido("ch1000328@intelbras.com.br")
print("   ✅ validação de endereço")

# Falha de SMTP não derruba a requisição
def quebrado(*a, **k):
    raise ConnectionRefusedError("servidor fora do ar")

original = mailer._transportar
mailer._transportar = quebrado
try:
    r = sol.post("/os/nova", data={"estabelecimento_id": est["id"], "tipo": "Industrial",
                                   "equipamento_id": eq["id"],
                                   "descricao_problema": "Teste com SMTP fora do ar"},
                 follow_redirects=True)
    assert r.status_code == 200, "a OS deveria ser aberta mesmo com o e-mail falhando"
    assert db.um("SELECT id FROM ordens_servico WHERE descricao_problema="
                 "'Teste com SMTP fora do ar'"), "OS não foi gravada"
finally:
    mailer._transportar = original
print("   ✅ SMTP fora do ar não impede a abertura da OS")
assert mailer.status()["ultimo_erro"], "o erro deveria ficar registrado"
print(f"   ✅ erro registrado para exibir na tela: {mailer.status()['ultimo_erro'][:44]}")

print("\n" + "=" * 58)
print(f"✅ E-MAIL VALIDADO — 7 eventos disparando")
print("=" * 58)
