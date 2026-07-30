"""O relógio da tela não pode voltar a zero quando o manutentor retoma."""
import os
import time

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:teste@localhost:5432/manutencao")
os.environ.setdefault("SECRET_KEY", "teste")

import mailer
mailer.modo_teste(True)

from app import app
from werkzeug.security import generate_password_hash
import db

for u, n, p in [("rel_sol", "Solicitante do Relógio", "solicitante"),
                ("rel_mnt", "Manutentor do Relógio", "manutentor"),
                ("rel_lid", "Líder do Relógio", "lider")]:
    db.executar("""INSERT INTO usuarios (usuario,senha_hash,nome,perfil) VALUES (%s,%s,%s,%s)
                   ON CONFLICT (usuario) DO UPDATE SET perfil=EXCLUDED.perfil""",
                (u, generate_password_hash("teste123"), n, p))


def entrar(u):
    c = app.test_client()
    c.post("/login", data={"usuario": u, "senha": "teste123"})
    return c


sol, jaime, lid = entrar("rel_sol"), entrar("rel_mnt"), entrar("rel_lid")
uid = db.scalar("SELECT id FROM usuarios WHERE usuario='rel_mnt'")
est = db.um("SELECT id FROM estabelecimentos WHERE nome='Matriz'")
eq = db.um("SELECT id FROM equipamentos LIMIT 1")

sol.post("/os/nova", data={"estabelecimento_id": est["id"], "tipo": "Industrial",
                           "equipamento_id": eq["id"],
                           "descricao_problema": "teste do relógio"}, follow_redirects=True)
o = db.um("SELECT * FROM ordens_servico ORDER BY id DESC LIMIT 1")
lid.post(f"/os/{o['id']}/assumir", data={"responsavel_id": uid}, follow_redirects=True)


def crono():
    return jaime.get(f"/api/os/{o['id']}/cronometro").get_json()


def linha(texto, c):
    contando = "sim" if c.get("trabalhando") else "não"
    print(f"   {texto:<40}{c['acumulado']:>5}s   contando: {contando}")


print("── O relógio ao longo do serviço ──")
jaime.post(f"/os/{o['id']}/acao/iniciar", follow_redirects=True)
time.sleep(2.2)
c = crono(); linha("iniciou, passaram ~2s", c)
t1 = c["acumulado"]
assert t1 >= 2 and c["trabalhando"] is True

jaime.post(f"/os/{o['id']}/acao/pausar", data={"motivo": "cafe"}, follow_redirects=True)
c = crono(); linha("pausou para o café", c)
assert c["acumulado"] == t1, f"ao pausar o acumulado mudou: {t1} → {c['acumulado']}"
assert c["trabalhando"] is False

time.sleep(2.2)
c = crono(); linha("2s de café — não pode subir", c)
assert c["acumulado"] == t1, "BUG: o relógio contou durante a pausa"

jaime.post(f"/os/{o['id']}/acao/retomar", follow_redirects=True)
c = crono(); linha("RETOMOU — segue de onde parou", c)
assert c["acumulado"] >= t1, f"BUG: zerou ao retomar ({t1} → {c['acumulado']})"
assert c["trabalhando"] is True

time.sleep(2.2)
c = crono(); linha("mais 2s de serviço", c)
assert c["acumulado"] >= t1 + 2

jaime.post(f"/os/{o['id']}/acao/pausar", data={"motivo": "almoco"}, follow_redirects=True)
jaime.post(f"/os/{o['id']}/acao/retomar", follow_redirects=True)
time.sleep(1.2)
c = crono(); linha("depois do almoço e retomada", c)
assert c["acumulado"] >= t1 + 3, "as fatias de trabalho deveriam somar"

d = db.um("SELECT id FROM defeitos LIMIT 1")
ca = db.um("SELECT id FROM causas LIMIT 1")
jaime.post(f"/os/{o['id']}/concluir",
           data={"defeito_id": d["id"], "causa_id": ca["id"], "acao_realizada": "ok"},
           follow_redirects=True)
final = db.um("SELECT tempo_trabalho_seg FROM ordens_servico WHERE id=%s", (o["id"],))
print(f"\n   tempo de serviço gravado: {final['tempo_trabalho_seg']}s")
for x in db.query("""SELECT tipo, SUM(COALESCE(duracao_seg,0)) AS s FROM os_tempos
                     WHERE os_id=%s GROUP BY tipo ORDER BY tipo""", (o["id"],)):
    print(f"     {x['tipo']:<12} {x['s']}s")
assert final["tempo_trabalho_seg"] >= 5, "deveria somar as três fatias de trabalho"
print("\n   ✅ soma as fatias de trabalho e ignora as pausas")

# ══ BOTÕES ════════════════════════════════════════════════════
print("\n── Botões na área do manutentor ──")
sol.post("/os/nova", data={"estabelecimento_id": est["id"], "tipo": "Industrial",
                           "equipamento_id": eq["id"],
                           "descricao_problema": "teste dos botões"}, follow_redirects=True)
o2 = db.um("SELECT * FROM ordens_servico ORDER BY id DESC LIMIT 1")
lid.post(f"/os/{o2['id']}/assumir", data={"responsavel_id": uid}, follow_redirects=True)
jaime.post(f"/os/{o2['id']}/acao/iniciar", follow_redirects=True)

tela = jaime.get(f"/os/{o2['id']}").data.decode()
bloco = tela[tela.index("acoes-os"):tela.index("acoes-os") + 1800]
for rotulo, esperado in [("Pausar", True), ("Concluir", True),
                         ("Almoço", False), ("Aguard. peça", False)]:
    tem = f">{rotulo}" in bloco or f"{rotulo}</button>" in bloco or f" {rotulo}<" in bloco
    marca = "✅" if tem == esperado else "❌"
    print(f"   {marca} botão '{rotulo}': {'presente' if tem else 'ausente'}")
    assert tem == esperado, f"'{rotulo}' deveria estar {'presente' if esperado else 'ausente'}"
print("   ✅ enquanto executa há apenas Pausar e Concluir")

motivos = ["Café", "Almoço", "Ginástica laboral", "Aguardando peça",
           "Reunião", "Fim de turno", "Outro motivo"]
faltando = [m for m in motivos if m not in tela]
assert not faltando, f"motivos ausentes no modal: {faltando}"
print(f"   ✅ os {len(motivos)} motivos estão no seletor de pausa")

jaime.post(f"/os/{o2['id']}/acao/pausar", data={"motivo": "aguardando_peca"},
           follow_redirects=True)
st = db.scalar("SELECT status FROM ordens_servico WHERE id=%s", (o2["id"],), default="")
assert st == "aguardando_peca", f"status errado: {st}"
print("   ✅ 'aguardando peça' escolhido no seletor muda o status corretamente")

tela = jaime.get(f"/os/{o2['id']}").data.decode()
assert "Retomar de" in tela
print("   ✅ ao retomar, o botão mostra de quanto tempo ele continua")

# ══ PALETA ════════════════════════════════════════════════════
print("\n── Paleta dos botões ──")
css = open("static/css/app.css").read()
for classe in [".btn-primary", ".btn-info", ".btn-success", ".btn-warning",
               ".btn-secondary", ".btn-outline-primary", ".btn-outline-warning",
               ".btn-outline-secondary", ".btn-outline-decio"]:
    assert classe in css, f"{classe} sem definição na paleta"
print("   ✅ todas as variantes do Bootstrap redefinidas nas cores da logo")
assert "var(--verde)" in css and "var(--azul)" in css
assert ".btn-danger" in css
print("   ✅ vermelho preservado só para ações destrutivas")

print("\n" + "=" * 58)
print("✅ CRONÔMETRO E BOTÕES VALIDADOS")
print("=" * 58)
