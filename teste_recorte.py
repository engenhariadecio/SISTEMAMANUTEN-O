"""
Verifica o recorte de cada perfil:
 • manutentor  → só as OS dele, as preventivas dele, as rondas dele
 • líder       → vê tudo, distribui OS, OM e rondas
 • analista    → dono do depósito NLAG completo
"""
import io
import os

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:teste@localhost:5432/manutencao")
os.environ.setdefault("SECRET_KEY", "teste")

import mailer
mailer.modo_teste(True)

from app import app
from werkzeug.security import generate_password_hash
import db

caixa = mailer.caixa_de_teste()
for usuario, nome, perfil, mail in [
        ("charles", "Charles Pfleger", "solicitante", "ch@intelbras.com.br"),
        ("jaime", "Jaime Matias", "manutentor", "ja@intelbras.com.br"),
        ("emerson", "Emerson de Jesus", "manutentor", "em@intelbras.com.br"),
        ("lourivaldo", "Lourivaldo Vieira", "lider", "lo@intelbras.com.br"),
        ("maria", "Maria Geucilene", "analista", "ma@intelbras.com.br")]:
    db.executar("""INSERT INTO usuarios (usuario, senha_hash, nome, perfil, email)
                   VALUES (%s,%s,%s,%s,%s) ON CONFLICT (usuario) DO UPDATE
                   SET perfil=EXCLUDED.perfil, nome=EXCLUDED.nome, email=EXCLUDED.email""",
                (usuario, generate_password_hash("teste123"), nome, perfil, mail))

def entrar(u):
    c = app.test_client(); c.post("/login", data={"usuario": u, "senha": "teste123"})
    return c

sol, jaime, emerson = entrar("charles"), entrar("jaime"), entrar("emerson")
lid, ana = entrar("lourivaldo"), entrar("maria")
BLOQ = b"n\xc3\xa3o tem acesso"

def bloqueado(c, r):
    return BLOQ in c.get(r, follow_redirects=True).data

id_jaime = db.scalar("SELECT id FROM usuarios WHERE usuario='jaime'")
id_emerson = db.scalar("SELECT id FROM usuarios WHERE usuario='emerson'")

# ══ 1. O QUE O MANUTENTOR NÃO VÊ ══════════════════════════════
print("── Planejamento fechado para o manutentor ──")
FECHADO = ["/preventivas/", "/preventivas/planos", "/preventivas/plano-materiais",
           "/preventivas/reprogramacoes", "/rondas/cadastro", "/os/triagem",
           "/materiais/", "/materiais/entrada", "/materiais/cadastro",
           "/indicadores/", "/relatorios/", "/admin/"]
for rota in FECHADO:
    assert bloqueado(jaime, rota), f"manutentor NÃO deveria acessar {rota}"
print(f"   ✅ bloqueado em {len(FECHADO)} telas de planejamento e gestão")

ABERTO = ["/", "/os/", "/os/intervencao", "/preventivas/oms", "/rondas/"]
for rota in ABERTO:
    r = jaime.get(rota, follow_redirects=True)
    assert r.status_code == 200 and BLOQ not in r.data, f"deveria acessar {rota}"
print("   ✅ acesso a: painel · suas OS · emergência · suas preventivas · suas rondas")

menu = jaime.get("/").data.decode()
assert "Minhas ordens de serviço" in menu and "Minhas preventivas" in menu
assert "Grade 52 semanas" not in menu and "Saldo de estoque" not in menu
assert "Triagem de OS" not in menu and "Plano de materiais" not in menu
print("   ✅ o menu dele mostra só o que ele pode usar")

# ══ 2. LÍDER DISTRIBUI A PREVENTIVA ═══════════════════════════
print("\n── Líder distribui a preventiva ──")
eq = db.um("SELECT id FROM equipamentos WHERE codigo='CO03-00'")
pid = db.inserir("""INSERT INTO planos_preventiva (equipamento_id, nome, ativo)
                    VALUES (%s,'Preventiva Compressor',TRUE) RETURNING id""", (eq["id"],))
db.executar("""INSERT INTO checklist_itens (plano_id, ordem, numero, descricao, periodicidade)
               VALUES (%s,1,'1','Verificar nível de óleo','MEN')""", (pid,))
ano = db.hoje().year
lid.post("/preventivas/programar", data={"plano_id": pid, "ano": ano,
                                         "periodicidade": "MEN", "semana_inicial": 1},
         follow_redirects=True)
prog = db.um("SELECT * FROM programacao WHERE plano_id=%s ORDER BY semana LIMIT 1", (pid,))
lid.post(f"/preventivas/gerar-om/{prog['id']}", follow_redirects=True)
om = db.um("SELECT * FROM ordens_manutencao ORDER BY id DESC LIMIT 1")
print(f"   OM #{om['numero']} gerada")

caixa.clear()
lid.post(f"/preventivas/om/{om['id']}/atribuir",
         data={"manutentor1_id": id_jaime}, follow_redirects=True)
assert db.scalar("SELECT manutentor1_id FROM ordens_manutencao WHERE id=%s",
                 (om["id"],), default=None) == id_jaime
print("   ✅ destinada ao Jaime")
assert caixa and "ja@intelbras.com.br" in " ".join(caixa[-1]["para"])
print(f"   ✅ e-mail enviado: {caixa[-1]['assunto']}")

assert f"#{om['numero']}".encode() in jaime.get("/preventivas/oms").data
assert f"#{om['numero']}".encode() not in emerson.get("/preventivas/oms").data
print("   ✅ aparece só para o Jaime")

r = emerson.post(f"/preventivas/om/{om['id']}", data={"acao": "iniciar"},
                 follow_redirects=True)
assert db.scalar("SELECT status FROM ordens_manutencao WHERE id=%s",
                 (om["id"],), default="") == "aberta"
print("   ✅ o outro manutentor não consegue executá-la")

jaime.post(f"/preventivas/om/{om['id']}", data={"acao": "iniciar"}, follow_redirects=True)
assert db.scalar("SELECT status FROM ordens_manutencao WHERE id=%s",
                 (om["id"],), default="") == "em_andamento"
print("   ✅ o designado executa normalmente")

# ══ 3. LÍDER CRIA E DESTINA A RONDA ═══════════════════════════
print("\n── Líder cria e destina a ronda ──")
assert lid.get("/rondas/cadastro").status_code == 200
caixa.clear()
lid.post("/rondas/cadastro", data={
    "acao": "nova_ronda", "nome": "Ronda Utilidades — Turno 1", "turno": "1º Turno",
    "responsavel_id": id_jaime,
    "observacao": "Conferir purgadores antes das 8h"}, follow_redirects=True)
ronda = db.um("SELECT * FROM rondas ORDER BY id DESC LIMIT 1")
print(f"   ronda '{ronda['nome']}' criada")
assert ronda["responsavel_id"] == id_jaime
assert caixa and "ja@intelbras.com.br" in " ".join(caixa[-1]["para"])
print(f"   ✅ Jaime avisado: {caixa[-1]['assunto']}")
assert "purgadores" in caixa[-1]["html"]
print("   ✅ a orientação do líder foi junto no aviso")

lid.post("/rondas/cadastro", data={
    "acao": "novo_ponto", "ronda_id": ronda["id"],
    "descricao": "Purgadores da rede de ar"}, follow_redirects=True)

assert ronda["nome"].encode() in jaime.get("/rondas/").data
assert ronda["nome"].encode() not in emerson.get("/rondas/").data
print("   ✅ a ronda aparece só para o Jaime")

r = emerson.post(f"/rondas/{ronda['id']}/iniciar", follow_redirects=True)
assert b"destinada a outro" in r.data
print("   ✅ o outro manutentor não consegue iniciá-la")

jaime.post(f"/rondas/{ronda['id']}/iniciar", follow_redirects=True)
ex = db.um("SELECT * FROM ronda_execucoes ORDER BY id DESC LIMIT 1")
assert ex and ex["usuario_id"] == id_jaime
print("   ✅ o designado inicia normalmente")

# Redestinar
caixa.clear()
lid.post("/rondas/cadastro", data={"acao": "destinar", "ronda_id": ronda["id"],
                                   "responsavel_id": id_emerson}, follow_redirects=True)
assert db.scalar("SELECT responsavel_id FROM rondas WHERE id=%s",
                 (ronda["id"],), default=None) == id_emerson
# O card ativo passa para o Emerson; o histórico do Jaime preserva o que ele executou
alvo = f"/rondas/{ronda['id']}/iniciar".encode()
assert alvo in emerson.get("/rondas/").data, "Emerson deveria poder iniciar"
assert alvo not in jaime.get("/rondas/").data, "Jaime não deveria mais poder iniciar"
print("   ✅ redestinar troca a visibilidade na hora")
assert ronda["nome"].encode() in jaime.get("/rondas/").data
print("   ✅ o histórico do Jaime preserva a execução que ele já fez")

# ══ 4. DEPÓSITO NLAG COMPLETO COM A ANALISTA ══════════════════
print("\n── Depósito NLAG na mão da analista ──")
NLAG = ["/materiais/", "/materiais/cadastro", "/materiais/entrada", "/materiais/saida",
        "/materiais/historico", "/materiais/coletor", "/materiais/etiquetas",
        "/materiais/importar", "/materiais/exportar", "/materiais/alertas"]
for rota in NLAG:
    r = ana.get(rota, follow_redirects=True)
    assert r.status_code == 200 and BLOQ not in r.data, f"analista deveria acessar {rota}"
print(f"   ✅ as {len(NLAG)} telas do NLAG original disponíveis para a analista")

ana.post("/materiais/cadastro", data={
    "acao": "novo", "codigo": "TST999", "descricao": "PECA DE TESTE",
    "unidade": "UNI", "tipo": "NLAG", "estoque_min": "2", "estoque_max": "10"},
    follow_redirects=True)
assert db.um("SELECT * FROM materiais WHERE codigo='TST999'")
print("   ✅ analista cadastra material")
ana.post("/materiais/entrada", data={"codigo": "TST999", "quantidade": "8"},
         follow_redirects=True)
assert db.saldo_material("TST999") == 8.0
print("   ✅ analista dá entrada")
r = ana.get("/materiais/etiqueta/imprimir?codigo=TST999")
assert r.status_code == 200 and b"TST999" in r.data
print("   ✅ etiqueta com código de barras")

# ══ 5. MANUTENTOR BAIXA PELA OS ═══════════════════════════════
print("\n── Manutentor baixa o saldo pela OS ──")
est = db.um("SELECT id FROM estabelecimentos WHERE nome='Matriz'")
sol.post("/os/nova", data={"estabelecimento_id": est["id"], "tipo": "Industrial",
                           "equipamento_id": eq["id"],
                           "descricao_problema": "Vazamento no compressor"},
         follow_redirects=True)
o = db.um("SELECT * FROM ordens_servico ORDER BY id DESC LIMIT 1")
lid.post(f"/os/{o['id']}/assumir", data={"responsavel_id": id_jaime}, follow_redirects=True)
jaime.post(f"/os/{o['id']}/acao/iniciar", follow_redirects=True)
jaime.post(f"/os/{o['id']}/material", data={"codigo": "TST999", "quantidade": "3"},
           follow_redirects=True)
assert db.saldo_material("TST999") == 5.0
print(f"   ✅ saldo caiu de 8 para {db.saldo_material('TST999'):g} sem sair da OS")

# ══ 6. CONCLUSÃO COM EVIDÊNCIAS ═══════════════════════════════
print("\n── Conclusão com fotos e relatório ──")
d = db.um("SELECT id FROM defeitos LIMIT 1")
ca = db.um("SELECT id FROM causas LIMIT 1")
caixa.clear()
jaime.post(f"/os/{o['id']}/concluir", data={
    "defeito_id": d["id"], "causa_id": ca["id"],
    "acao_realizada": "Substituída a vedação e testada a estanqueidade",
    "evidencias": [(io.BytesIO(b"conteudo-foto-antes"), "antes.jpg"),
                   (io.BytesIO(b"conteudo-foto-depois"), "depois.jpg"),
                   (io.BytesIO(b"%PDF-relatorio"), "laudo.pdf")]},
    content_type="multipart/form-data", follow_redirects=True)
anexos = db.query("SELECT nome FROM os_anexos WHERE os_id=%s", (o["id"],))
nomes = sorted(a["nome"] for a in anexos or [])
print(f"   anexados: {', '.join(nomes)}")
assert nomes == ["antes.jpg", "depois.jpg", "laudo.pdf"]
print("   ✅ fotos e relatório gravados na OS")
assert "3 arquivo(s)" in caixa[-1]["html"]
print("   ✅ o e-mail ao solicitante informa quantas evidências há")

r = sol.get(f"/os/{o['id']}")
assert b"antes.jpg" in r.data and b"laudo.pdf" in r.data
print("   ✅ o solicitante vê as evidências ao aprovar")

# ══ 7. LÍDER VÊ TUDO ══════════════════════════════════════════
print("\n── Visão do líder ──")
TUDO = ["/os/", "/os/triagem", "/preventivas/", "/preventivas/oms",
        "/preventivas/planos", "/preventivas/plano-materiais", "/rondas/",
        "/rondas/cadastro", "/materiais/", "/indicadores/", "/relatorios/"]
for rota in TUDO:
    r = lid.get(rota, follow_redirects=True)
    assert r.status_code == 200 and BLOQ not in r.data, f"líder deveria acessar {rota}"
print(f"   ✅ acesso às {len(TUDO)} áreas de gestão")
assert f"#{o['numero']}".encode() in lid.get("/os/?status=todas").data
print("   ✅ enxerga as OS de todos os manutentores")

print("\n" + "=" * 60)
print("✅ RECORTE DE PERFIS VALIDADO")
print("=" * 60)
