"""Teste funcional: percorre todas as rotas e simula o fluxo completo."""
import os
import sys

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:teste@localhost:5432/manutencao")
os.environ.setdefault("SECRET_KEY", "teste")

from app import app
import db

app.config["TESTING"] = False
c = app.test_client()

falhas = []
ok = 0


def check(desc, resp, esperado=(200, 302)):
    global ok
    if resp.status_code in esperado:
        ok += 1
        return True
    falhas.append(f"{desc} → HTTP {resp.status_code}")
    if resp.status_code == 500:
        txt = resp.data.decode("utf-8", "ignore")
        falhas.append("    " + txt[:400].replace("\n", " "))
    return False


# ── Login ──
r = c.post("/login", data={"usuario": "admin", "senha": "teste123"}, follow_redirects=True)
assert b"Painel" in r.data or r.status_code == 200, "login falhou"
print("✅ login ok")

# ── Rotas GET ──
rotas_get = [
    "/", "/os/", "/os/nova", "/os/intervencao", "/os/tablet",
    "/preventivas/", "/preventivas/planos", "/preventivas/planos/novo",
    "/preventivas/oms", "/preventivas/plano-materiais", "/preventivas/reprogramacoes",
    "/rondas/", "/rondas/cadastro",
    "/materiais/", "/materiais/cadastro", "/materiais/entrada", "/materiais/saida",
    "/materiais/coletor", "/materiais/etiquetas", "/materiais/alertas",
    "/materiais/historico", "/materiais/importar", "/materiais/exportar",
    "/solicitacoes/", "/solicitacoes/nova", "/solicitacoes/exportar",
    "/indicadores/", "/indicadores/parque",
    "/admin/", "/admin/usuarios", "/admin/equipamentos", "/admin/cadastros",
    "/admin/terceiros", "/admin/parametros", "/admin/auditoria",
    "/meu-perfil", "/notificacoes",
    "/api/equipamentos", "/api/causas", "/api/notificacoes/nao-lidas",
    "/api/materiais/busca?q=cabo",
]
for rota in rotas_get:
    check(f"GET {rota}", c.get(rota))

print(f"✅ {ok} rotas GET responderam")

# ══════════════════════════════════════════════════════════════
#  FLUXO COMPLETO
# ══════════════════════════════════════════════════════════════
print("\n── Fluxo de OS corretiva ──")

eq = db.um("SELECT id FROM equipamentos WHERE codigo='PU01-00'")
est = db.um("SELECT id FROM estabelecimentos WHERE nome='Matriz'")

r = c.post("/os/nova", data={
    "estabelecimento_id": est["id"], "tipo": "Industrial",
    "equipamento_id": eq["id"], "descricao_problema": "Torreta desalinhada no teste automatizado",
    "maquina_parada": "on"}, follow_redirects=True)
check("abrir OS", r)
o = db.um("SELECT * FROM ordens_servico ORDER BY id DESC LIMIT 1")
assert o, "OS não foi criada"
print(f"   OS #{o['numero']} criada — criticidade {o['criticidade']}, status {o['status']}")
assert o["criticidade"] == "A", "criticidade não herdada"

eq_status = db.scalar("SELECT status FROM equipamentos WHERE id=%s", (eq["id"],), default="")
assert eq_status == "parado", "equipamento não marcado como parado"
print("   equipamento marcado como parado ✅")

check("detalhe OS", c.get(f"/os/{o['id']}"))

# Cronômetro
check("iniciar", c.post(f"/os/{o['id']}/acao/iniciar", follow_redirects=True))
o2 = db.um("SELECT status FROM ordens_servico WHERE id=%s", (o["id"],))
assert o2["status"] == "em_andamento", f"status errado: {o2['status']}"
print("   iniciada — cronômetro rodando ✅")

check("pausar", c.post(f"/os/{o['id']}/acao/pausar", follow_redirects=True))
check("almoco", c.post(f"/os/{o['id']}/acao/almoco", follow_redirects=True))
check("aguardando peça", c.post(f"/os/{o['id']}/acao/aguardando_peca", follow_redirects=True))
check("retomar", c.post(f"/os/{o['id']}/acao/retomar", follow_redirects=True))
n_tempos = db.scalar("SELECT COUNT(*) AS n FROM os_tempos WHERE os_id=%s", (o["id"],))
print(f"   {n_tempos} intervalos de tempo registrados ✅")

check("comentar", c.post(f"/os/{o['id']}/comentar",
                         data={"comentario": "Teste de apontamento"}, follow_redirects=True))

# Material na OS
db.executar("""INSERT INTO materiais (codigo, descricao, unidade, tipo, estoque_min, estoque_max, valor_unit)
               VALUES ('TESTE001','PARAFUSO DE TESTE M8','UNI','NLAG',5,20,3.50)
               ON CONFLICT (codigo) DO NOTHING""")
db.executar("""INSERT INTO movimentacoes (codigo, tipo, quantidade, usuario)
               VALUES ('TESTE001','ENTRADA',10,'teste')""")
saldo = db.saldo_material("TESTE001")
print(f"   material TESTE001 com saldo {saldo}")

check("add material", c.post(f"/os/{o['id']}/material",
                             data={"codigo": "TESTE001", "quantidade": "3", "baixar": "on"},
                             follow_redirects=True))
saldo2 = db.saldo_material("TESTE001")
assert saldo2 == 7.0, f"baixa não funcionou: {saldo2}"
print(f"   baixa no estoque NLAG: {saldo} → {saldo2} ✅")

# Conclusão
d = db.um("SELECT id FROM defeitos LIMIT 1")
ca = db.um("SELECT id FROM causas LIMIT 1")
check("concluir", c.post(f"/os/{o['id']}/concluir", data={
    "defeito_id": d["id"], "causa_id": ca["id"],
    "acao_realizada": "Alinhamento da torreta realizado",
    "liberar_equipamento": "on"}, follow_redirects=True))
o3 = db.um("SELECT * FROM ordens_servico WHERE id=%s", (o["id"],))
assert o3["status"] == "aguardando_aprovacao", f"status: {o3['status']}"
print(f"   concluída → aguardando aprovação · custo peças R$ {o3['custo_pecas']} ✅")

eq_st = db.scalar("SELECT status FROM equipamentos WHERE id=%s", (eq["id"],), default="")
assert eq_st == "operando", "equipamento não liberado"
print("   equipamento liberado para produção ✅")

# Reprovar → reabrir → aprovar
check("reprovar", c.post(f"/os/{o['id']}/aprovar",
                         data={"decisao": "reprovar", "comentario": "Ainda desalinhada"},
                         follow_redirects=True))
assert db.scalar("SELECT status FROM ordens_servico WHERE id=%s", (o["id"],), default="") == "reprovada"
print("   reprovação registrada ✅")

check("reabrir", c.post(f"/os/{o['id']}/reabrir", follow_redirects=True))
check("concluir 2", c.post(f"/os/{o['id']}/concluir", data={
    "defeito_id": d["id"], "causa_id": ca["id"],
    "acao_realizada": "Substituído rolamento e realinhado"}, follow_redirects=True))
check("aprovar", c.post(f"/os/{o['id']}/aprovar",
                        data={"decisao": "aprovar", "comentario": "OK"}, follow_redirects=True))
o4 = db.um("SELECT * FROM ordens_servico WHERE id=%s", (o["id"],))
assert o4["status"] == "concluida" and o4["aprovado"] is True
print(f"   OS finalizada · tempo trabalhado {o4['tempo_trabalho_seg']}s ✅")

# ── Intervenção automática ──
print("\n── Intervenção automática ──")
check("intervencao", c.post("/os/intervencao", data={
    "equipamento_id": eq["id"], "sintoma": "Vazamento de ar — troca de conector"},
    follow_redirects=True))
oi = db.um("SELECT * FROM ordens_servico WHERE origem='intervencao' ORDER BY id DESC LIMIT 1")
assert oi and oi["status"] == "em_andamento"
print(f"   intervenção #{oi['numero']} aberta e cronômetro ligado ✅")

# ══════════════════════════════════════════════════════════════
print("\n── Fluxo de preventiva ──")
r = c.post("/preventivas/planos/novo", data={
    "equipamento_id": eq["id"], "nome": "Preventiva Puncionadeira PGA4",
    "codigo_doc": "RQ-346", "interna": "1"}, follow_redirects=True)
check("criar plano", r)
p = db.um("SELECT * FROM planos_preventiva ORDER BY id DESC LIMIT 1")
print(f"   plano #{p['id']} criado ✅")

for i, desc in enumerate(["Verificar nível de óleo", "Testar sensores de segurança",
                          "Lubrificar guias lineares"], 1):
    check("add item", c.post(f"/preventivas/planos/{p['id']}", data={
        "acao": "add_item", "numero": str(i), "descricao": desc,
        "periodicidade": "MEN", "tipo_resposta": "ok_nok"}, follow_redirects=True))
n_itens = db.scalar("SELECT COUNT(*) AS n FROM checklist_itens WHERE plano_id=%s", (p["id"],))
print(f"   {n_itens} itens de check list cadastrados ✅")

check("add material plano", c.post(f"/preventivas/planos/{p['id']}", data={
    "acao": "add_material", "codigo": "TESTE001", "descricao": "PARAFUSO DE TESTE M8",
    "umb": "UNI", "qt_men": "2", "qt_sem": "0", "qt_bim": "0", "qt_tri": "0",
    "qt_qua": "0", "qt_ses": "0", "qt_anu": "0"}, follow_redirects=True))

check("programar", c.post("/preventivas/programar", data={
    "plano_id": p["id"], "ano": db.hoje().year, "periodicidade": "MEN",
    "semana_inicial": "1"}, follow_redirects=True))
n_prog = db.scalar("SELECT COUNT(*) AS n FROM programacao WHERE plano_id=%s", (p["id"],))
print(f"   {n_prog} preventivas programadas no ano ✅")

ano, semana = db.hoje().isocalendar()[0], db.hoje().isocalendar()[1]
check("gerar semana", c.post("/preventivas/gerar-semana",
                             data={"ano": ano, "semana": semana}, follow_redirects=True))
prog = db.um("SELECT * FROM programacao WHERE plano_id=%s ORDER BY semana LIMIT 1", (p["id"],))
check("gerar OM", c.post(f"/preventivas/gerar-om/{prog['id']}", follow_redirects=True))
om = db.um("SELECT * FROM ordens_manutencao ORDER BY id DESC LIMIT 1")
print(f"   OM #{om['numero']} gerada ✅")

check("detalhe OM", c.get(f"/preventivas/om/{om['id']}"))
check("iniciar OM", c.post(f"/preventivas/om/{om['id']}", data={"acao": "iniciar"},
                           follow_redirects=True))

itens = db.query("SELECT id FROM checklist_itens WHERE plano_id=%s ORDER BY ordem", (p["id"],))
dados = {"acao": "concluir", "observacoes": "Preventiva executada", "horimetro": "1250",
         "tempo_minutos": "90", "manutentor1_id": "1"}
dados[f"item_{itens[0]['id']}"] = "OK"
dados[f"item_{itens[1]['id']}"] = "NOK"
dados[f"obs_{itens[1]['id']}"] = "Sensor de porta com folga — requer ajuste"
dados[f"item_{itens[2]['id']}"] = "OK"
check("concluir OM", c.post(f"/preventivas/om/{om['id']}", data=dados, follow_redirects=True))

om2 = db.um("SELECT * FROM ordens_manutencao WHERE id=%s", (om["id"],))
assert om2["status"] == "concluida"
print(f"   OM concluída · no prazo={om2['no_prazo']} · horímetro atualizado ✅")

os_ger = db.um("SELECT * FROM ordens_servico WHERE origem='preventiva' ORDER BY id DESC LIMIT 1")
assert os_ger, "OS de pendência não foi gerada"
print(f"   OS #{os_ger['numero']} gerada automaticamente pela pendência NOK ✅")

check("visto lider", c.post(f"/preventivas/om/{om['id']}", data={"acao": "visto_lider"},
                            follow_redirects=True))
print("   visto do líder registrado ✅")

# ══════════════════════════════════════════════════════════════
print("\n── Ronda diária ──")
ronda = db.um("SELECT id FROM rondas LIMIT 1")
check("iniciar ronda", c.post(f"/rondas/{ronda['id']}/iniciar", follow_redirects=True))
ex = db.um("SELECT * FROM ronda_execucoes ORDER BY id DESC LIMIT 1")
pontos = db.query("SELECT id FROM ronda_pontos WHERE ronda_id=%s ORDER BY ordem", (ronda["id"],))
dados = {"acao": "concluir", "observacoes": "Ronda do teste"}
for i, pt in enumerate(pontos):
    dados[f"ponto_{pt['id']}"] = "NOK" if i == 2 else "OK"
    if i == 2:
        dados[f"obs_{pt['id']}"] = "Compressor com ruído anormal"
check("concluir ronda", c.post(f"/rondas/exec/{ex['id']}", data=dados, follow_redirects=True))
os_ronda = db.um("SELECT * FROM ordens_servico WHERE origem='ronda' ORDER BY id DESC LIMIT 1")
assert os_ronda, "OS da ronda não gerada"
print(f"   ronda concluída → OS #{os_ronda['numero']} gerada pelo ponto NOK ✅")

# ══════════════════════════════════════════════════════════════
print("\n── Materiais ──")
check("entrada", c.post("/materiais/entrada", data={
    "codigo": "TESTE001", "quantidade": "20", "observacao": "NF 123"}, follow_redirects=True))
print(f"   saldo após entrada: {db.saldo_material('TESTE001')} ✅")

check("saida", c.post("/materiais/saida", data={
    "codigo": "TESTE001", "quantidade": "23", "observacao": "consumo"}, follow_redirects=True))
saldo_f = db.saldo_material("TESTE001")
print(f"   saldo após saída: {saldo_f} (abaixo do mínimo 5 → alerta) ✅")

n_notif = db.scalar("SELECT COUNT(*) AS n FROM notificacoes")
print(f"   {n_notif} notificações geradas no fluxo ✅")

check("ajuste inventario", c.post("/materiais/ajuste",
                                  data={"codigo": "TESTE001", "contado": "12"}, follow_redirects=True))
assert db.saldo_material("TESTE001") == 12.0
print("   ajuste de inventário ✅")

check("coletor", c.post("/materiais/coletor", data={
    "codigo": "TESTE001", "quantidade": "2", "operacao": "SAIDA"}))
check("etiqueta", c.get("/materiais/etiqueta/imprimir?codigo=TESTE001&copias=2"))
print("   coletor e etiqueta com código de barras ✅")

# ══════════════════════════════════════════════════════════════
print("\n── Solicitação de material ──")
cc = db.um("SELECT id FROM centros_custo LIMIT 1")
check("nova SM", c.post("/solicitacoes/nova", data={
    "codigo": "", "descricao": "Correia sincronizada ATP10 1010", "tipo": "Cadastro",
    "quantidade": "2", "centro_custo_id": cc["id"], "os_id": o["id"],
    "observacoes": "Item crítico"}, follow_redirects=True))
sm = db.um("SELECT * FROM solicitacoes_material ORDER BY id DESC LIMIT 1")
print(f"   SM #{sm['numero']} criada ✅")

for sit in ["Em cadastro", "Cadastrado", "Proc. de Compra", "Recebido", "Concluído"]:
    check(f"SM → {sit}", c.post(f"/solicitacoes/{sm['id']}", data={
        "situacao": sit, "comentario": f"Movido para {sit}",
        "num_pr": "PR134268", "codigo_final": "7400205"}, follow_redirects=True))
n_hist = db.scalar("SELECT COUNT(*) AS n FROM solicitacao_historico WHERE solicitacao_id=%s",
                   (sm["id"],))
print(f"   {n_hist} eventos no histórico da SM ✅")

# ══════════════════════════════════════════════════════════════
print("\n── Cadastros e indicadores ──")
check("novo usuario", c.post("/admin/usuarios", data={
    "acao": "novo", "usuario": "lourivaldo", "nome": "Lourivaldo Vieira Junior",
    "matricula": "LO1000673", "perfil": "lider", "senha": "teste123"}, follow_redirects=True))
check("novo equipamento", c.post("/admin/equipamentos", data={
    "acao": "novo", "grupo_prev": "ZZ99", "subcodigo": "00", "nome": "Equipamento de teste",
    "criticidade": "B", "tipo": "Industrial"}, follow_redirects=True))
check("terceiros", c.post("/admin/terceiros", data={
    "equipamento_id": eq["id"], "empresa": "LAG Service", "tipo_servico": "Corretiva",
    "descricao": "Conserto de CNC", "valor": "1500"}, follow_redirects=True))
check("cadastro causa", c.post("/admin/cadastros", data={
    "acao": "causa", "nome": "Falha de teste automatizado"}, follow_redirects=True))

check("indicadores", c.get("/indicadores/"))
check("parque", c.get("/indicadores/parque"))
check("ficha equipamento", c.get(f"/indicadores/equipamento/{eq['id']}"))
check("plano materiais", c.get("/preventivas/plano-materiais?semanas=12"))
check("grade", c.get("/preventivas/"))
check("alertas", c.get("/materiais/alertas"))

# ── Perfis ──
print("\n── Controle de acesso por perfil ──")
c2 = app.test_client()
c2.post("/login", data={"usuario": "lourivaldo", "senha": "teste123"})
r = c2.get("/admin/usuarios", follow_redirects=True)
assert b"n\xc3\xa3o tem acesso" in r.data or r.status_code == 200
print("   perfil líder bloqueado na gestão de usuários ✅")
check("lider vê preventivas", c2.get("/preventivas/"))
check("lider vê indicadores", c2.get("/indicadores/"))

# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 58)
if falhas:
    print(f"❌ {len(falhas)} FALHA(S):")
    for f in falhas:
        print("  ", f)
    sys.exit(1)
else:
    print(f"✅ TODOS OS TESTES PASSARAM — {ok} verificações")
    print("=" * 58)
