"""Reproduz o problema: manutentor não consegue iniciar nem concluir a OS."""
import io
import os

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:teste@localhost:5432/manutencao")
os.environ.setdefault("SECRET_KEY", "teste")

import mailer
mailer.modo_teste(True)

from app import app
from werkzeug.security import generate_password_hash
import db

for usuario, nome, perfil in [("charles", "Charles Pfleger", "solicitante"),
                              ("jaime", "Jaime Matias", "manutentor"),
                              ("lourivaldo", "Lourivaldo Vieira", "lider")]:
    db.executar("""INSERT INTO usuarios (usuario, senha_hash, nome, perfil, email)
                   VALUES (%s,%s,%s,%s,%s) ON CONFLICT (usuario) DO UPDATE
                   SET perfil=EXCLUDED.perfil, nome=EXCLUDED.nome""",
                (usuario, generate_password_hash("teste123"), nome, perfil,
                 f"{usuario}@intelbras.com.br"))

def entrar(u):
    c = app.test_client(); c.post("/login", data={"usuario": u, "senha": "teste123"})
    return c

sol, jaime, lid = entrar("charles"), entrar("jaime"), entrar("lourivaldo")
id_jaime = db.scalar("SELECT id FROM usuarios WHERE usuario='jaime'")
est = db.um("SELECT id FROM estabelecimentos WHERE nome='Matriz'")
eq = db.um("SELECT id FROM equipamentos LIMIT 1")

print("── Fluxo do manutentor, do início ao fim ──")
sol.post("/os/nova", data={"estabelecimento_id": est["id"], "tipo": "Industrial",
                           "equipamento_id": eq["id"],
                           "descricao_problema": "Máquina com ruído estranho"},
         follow_redirects=True)
o = db.um("SELECT * FROM ordens_servico ORDER BY id DESC LIMIT 1")
lid.post(f"/os/{o['id']}/assumir", data={"responsavel_id": id_jaime}, follow_redirects=True)
st = db.scalar("SELECT status FROM ordens_servico WHERE id=%s", (o["id"],), default="")
print(f"   após a triagem, status = '{st}'")

# ── O botão de iniciar precisa estar na tela ──
tela = jaime.get(f"/os/{o['id']}").data.decode()
assert "Iniciar atendimento" in tela, \
    "BUG: o manutentor abre a OS atribuída e não encontra o botão de iniciar"
print("   ✅ o botão 'Iniciar atendimento' aparece para ele")

jaime.post(f"/os/{o['id']}/acao/iniciar", follow_redirects=True)
assert db.scalar("SELECT status FROM ordens_servico WHERE id=%s",
                 (o["id"],), default="") == "em_andamento"
print("   ✅ cronômetro iniciado")

# ── Pausas com motivo ──
print("\n── Pausas ──")
for motivo, rotulo in [("cafe", "Café"), ("almoco", "Almoço"),
                       ("laboral", "Ginástica laboral"), ("reuniao", "Reunião")]:
    r = jaime.post(f"/os/{o['id']}/acao/pausar", data={"motivo": motivo},
                   follow_redirects=True)
    assert r.status_code == 200
    st = db.scalar("SELECT status FROM ordens_servico WHERE id=%s", (o["id"],), default="")
    assert st == "pausada", f"deveria pausar, status={st}"
    t = db.um("""SELECT tipo FROM os_tempos WHERE os_id=%s ORDER BY id DESC LIMIT 1""",
              (o["id"],))
    assert t["tipo"] == motivo, f"o intervalo deveria ser do tipo {motivo}, veio {t['tipo']}"
    ap = db.um("""SELECT descricao FROM os_apontamentos WHERE os_id=%s
                  ORDER BY id DESC LIMIT 1""", (o["id"],))
    print(f"   {rotulo:<20} → {ap['descricao']}")
    jaime.post(f"/os/{o['id']}/acao/retomar", follow_redirects=True)
print("   ✅ cada motivo de pausa é registrado separadamente")

# Pausa com motivo livre
jaime.post(f"/os/{o['id']}/acao/pausar",
           data={"motivo": "outro", "observacao": "Aguardando liberação da produção"},
           follow_redirects=True)
ap = db.um("SELECT descricao FROM os_apontamentos WHERE os_id=%s ORDER BY id DESC LIMIT 1",
           (o["id"],))
assert "liberação da produção" in ap["descricao"]
print(f"   ✅ motivo livre: {ap['descricao']}")
jaime.post(f"/os/{o['id']}/acao/retomar", follow_redirects=True)

# ── Solicitar material e retomar ──
print("\n── Material no meio do serviço ──")
db.executar("""INSERT INTO materiais (codigo, descricao, unidade, tipo)
               VALUES ('TSTC01','ROLAMENTO DE TESTE','UNI','NLAG')
               ON CONFLICT (codigo) DO NOTHING""")
jaime.post(f"/os/{o['id']}/material",
           data={"codigo": "TSTC01", "descricao": "ROLAMENTO DE TESTE",
                 "quantidade": "1", "pausar": "1"}, follow_redirects=True)
assert db.scalar("SELECT status FROM ordens_servico WHERE id=%s",
                 (o["id"],), default="") == "aguardando_peca"
print("   ✅ sem saldo → solicitação aberta e OS pausada")

# Enquanto o analista não liberar, a OS fica travada
jaime.post(f"/os/{o['id']}/acao/retomar", follow_redirects=True)
assert db.scalar("SELECT status FROM ordens_servico WHERE id=%s",
                 (o["id"],), default="") == "aguardando_peca"
print("   ✅ não retoma antes da liberação do analista")

db.executar("""INSERT INTO usuarios (usuario,senha_hash,nome,perfil,email)
               VALUES ('cr_ana',%s,'Analista Crono','analista','anacr@intelbras.com.br')
               ON CONFLICT (usuario) DO UPDATE SET perfil='analista', nome=EXCLUDED.nome""",
            (generate_password_hash("teste123"),))
ana = app.test_client(); ana.post("/login", data={"usuario": "cr_ana", "senha": "teste123"})
sm = db.um("SELECT * FROM solicitacoes_material WHERE os_id=%s ORDER BY id DESC LIMIT 1",
           (o["id"],))
ana.post(f"/solicitacoes/{sm['id']}/liberar",
         data={"quantidade": "1", "entrada": "3"}, follow_redirects=True)
print("   ✅ analista liberou a peça")

tela = jaime.get(f"/os/{o['id']}").data.decode()
assert "Retomar" in tela, "deveria haver botão de retomar"
jaime.post(f"/os/{o['id']}/acao/retomar", follow_redirects=True)
assert db.scalar("SELECT status FROM ordens_servico WHERE id=%s",
                 (o["id"],), default="") == "em_andamento"
print("   ✅ retomou depois da peça liberada")

# ── Concluir com relatório, fotos e vídeo ──
print("\n── Conclusão ──")
tela = jaime.get(f"/os/{o['id']}").data.decode()
assert "Concluir" in tela, "BUG: não há botão de concluir"
print("   ✅ o botão 'Concluir' está disponível")

d = db.um("SELECT id FROM defeitos LIMIT 1")
ca = db.um("SELECT id FROM causas LIMIT 1")
mailer.caixa_de_teste().clear()
r = jaime.post(f"/os/{o['id']}/concluir", data={
    "defeito_id": d["id"], "causa_id": ca["id"],
    "acao_realizada": "Substituído o rolamento do eixo principal e realinhado o conjunto. "
                      "Testado por 20 minutos sem ruído.",
    "evidencias": [(io.BytesIO(b"foto-antes"), "antes.jpg"),
                   (io.BytesIO(b"foto-depois"), "depois.jpg"),
                   (io.BytesIO(b"video-teste"), "teste.mp4"),
                   (io.BytesIO(b"%PDF-laudo"), "relatorio.pdf")]},
    content_type="multipart/form-data", follow_redirects=True)
assert r.status_code == 200

final = db.um("SELECT * FROM ordens_servico WHERE id=%s", (o["id"],))
print(f"   status = '{final['status']}' · tempo = {final['tempo_trabalho_seg']}s")
assert final["status"] == "aguardando_aprovacao", "deveria ir para aprovação"
print("   ✅ concluída e enviada para o solicitante aprovar")

anexos = sorted(a["nome"] for a in db.query(
    "SELECT nome FROM os_anexos WHERE os_id=%s", (o["id"],)) or [])
print(f"   evidências: {', '.join(anexos)}")
assert anexos == ["antes.jpg", "depois.jpg", "relatorio.pdf", "teste.mp4"]
print("   ✅ fotos, vídeo e relatório anexados")

msg = mailer.caixa_de_teste()[-1]
assert "charles@intelbras.com.br" in " ".join(msg["para"])
print(f"   ✅ e-mail ao criador da OS: {msg['assunto']}")

# ── Distribuição do tempo por motivo ──
print("\n── Tempo apurado ──")
tempos = db.query("""SELECT tipo, SUM(COALESCE(duracao_seg,0)) AS seg, COUNT(*) AS n
                     FROM os_tempos WHERE os_id=%s GROUP BY tipo ORDER BY tipo""",
                  (o["id"],))
for t in tempos:
    print(f"   {t['tipo']:<18} {t['n']} intervalo(s)")
tipos = {t["tipo"] for t in tempos}
assert {"trabalho", "cafe", "almoco", "laboral", "reuniao", "aguardando_peca"} <= tipos
print("   ✅ cada tipo de parada fica separado do tempo de trabalho")

# ── O criador decide ──
print("\n── O criador da OS decide ──")
tela = sol.get(f"/os/{o['id']}").data.decode()
assert "Aprovar e finalizar" in tela and "Reprovar" in tela
assert "antes.jpg" in tela and "relatorio.pdf" in tela
print("   ✅ ele vê as evidências e os dois botões")

sol.post(f"/os/{o['id']}/aprovar",
         data={"decisao": "reprovar", "comentario": "O ruído voltou na primeira hora"},
         follow_redirects=True)
assert db.scalar("SELECT status FROM ordens_servico WHERE id=%s",
                 (o["id"],), default="") == "reprovada"
print("   ✅ reprovou — volta para o Jaime")

tela = jaime.get(f"/os/{o['id']}").data.decode()
assert "Retomar tratamento" in tela or "Iniciar atendimento" in tela
jaime.post(f"/os/{o['id']}/reabrir", follow_redirects=True)
jaime.post(f"/os/{o['id']}/concluir", data={
    "defeito_id": d["id"], "causa_id": ca["id"],
    "acao_realizada": "Trocado também o mancal, que estava com folga"},
    follow_redirects=True)
sol.post(f"/os/{o['id']}/aprovar", data={"decisao": "aprovar", "comentario": "Resolvido"},
         follow_redirects=True)
fim = db.um("SELECT * FROM ordens_servico WHERE id=%s", (o["id"],))
assert fim["status"] == "concluida" and fim["aprovado"] is True
print("   ✅ segunda tentativa aprovada — OS encerrada")

print("\n" + "=" * 58)
print("✅ CICLO DO MANUTENTOR VALIDADO")
print("=" * 58)
