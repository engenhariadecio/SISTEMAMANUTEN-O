"""
Migração dos 232 itens do NLAG em produção e o seletor de peças do manutentor.
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

CSV = "/mnt/user-data/uploads/saldo_estoque__12_.csv"

adm = app.test_client()
adm.post("/login", data={"usuario": "admin", "senha": "teste123"})

# ══ MIGRAÇÃO ══════════════════════════════════════════════════
print("── Migração do NLAG em produção ──")
db.executar("DELETE FROM movimentacoes")
db.executar("DELETE FROM materiais")

dados = open(CSV, "rb").read()
r = adm.post("/materiais/importar",
             data={"destino": "nlag", "arquivo": (io.BytesIO(dados), "saldo_estoque.csv")},
             content_type="multipart/form-data", follow_redirects=True)
assert r.status_code == 200

total = db.scalar("SELECT COUNT(*) AS n FROM materiais WHERE tipo='NLAG'")
com_saldo = db.scalar("""SELECT COUNT(*) AS n FROM (
    SELECT m.codigo FROM materiais m JOIN movimentacoes mv ON mv.codigo=m.codigo
    GROUP BY m.codigo HAVING SUM(CASE WHEN mv.tipo IN ('ENTRADA','AJUSTE')
      THEN mv.quantidade ELSE -mv.quantidade END) > 0) t""")
print(f"   {total} itens · {com_saldo} com saldo · {total - com_saldo} zerados")
assert total == 232, f"esperado 232, veio {total}"
assert com_saldo == 29, f"esperado 29 com saldo, veio {com_saldo}"
print("   ✅ bate exatamente com o painel do sistema atual (232 / 29 / 203)")

# Confere alguns saldos contra o CSV
import csv as csvmod
linhas = list(csvmod.DictReader(io.StringIO(dados.decode("utf-8-sig")), delimiter=";"))
divergentes = []
for l in linhas:
    cod = l["Codigo"].strip().upper()
    esperado = float(l["Saldo"].replace(",", ".") or 0)
    real = db.saldo_material(cod)
    if abs(real - esperado) > 0.001:
        divergentes.append((cod, esperado, real))
assert not divergentes, f"saldos divergentes: {divergentes[:5]}"
print(f"   ✅ os {len(linhas)} saldos conferem, um a um, com o CSV")

unidades = db.query("SELECT DISTINCT unidade FROM materiais ORDER BY unidade")
print(f"   ✅ unidades preservadas: {', '.join(u['unidade'] for u in unidades)}")

# Reimportar não duplica
adm.post("/materiais/importar",
         data={"destino": "nlag", "arquivo": (io.BytesIO(dados), "saldo_estoque.csv")},
         content_type="multipart/form-data", follow_redirects=True)
assert db.scalar("SELECT COUNT(*) AS n FROM materiais WHERE tipo='NLAG'") == 232
assert db.saldo_material("6996974") == 200.0
print("   ✅ reimportar não duplica nem soma em dobro")

# ══ CATÁLOGO PARA O MANUTENTOR ════════════════════════════════
print("\n── Catálogo de peças ──")
for usuario, nome, perfil in [("charles", "Charles", "solicitante"),
                              ("jaime", "Jaime Matias", "manutentor"),
                              ("lourivaldo", "Lourivaldo", "lider"),
                              ("maria", "Maria Geucilene", "analista")]:
    db.executar("""INSERT INTO usuarios (usuario, senha_hash, nome, perfil, email)
                   VALUES (%s,%s,%s,%s,%s) ON CONFLICT (usuario) DO UPDATE
                   SET perfil=EXCLUDED.perfil, nome=EXCLUDED.nome""",
                (usuario, generate_password_hash("teste123"), nome, perfil,
                 f"{usuario}@intelbras.com.br"))

def entrar(u):
    c = app.test_client(); c.post("/login", data={"usuario": u, "senha": "teste123"})
    return c

sol, jaime, lid, ana = entrar("charles"), entrar("jaime"), entrar("lourivaldo"), entrar("maria")

j = jaime.get("/api/materiais/busca?com_saldo=1").get_json()
print(f"   {len(j)} peças com saldo oferecidas ao manutentor")
assert len(j) == 29, f"deveria listar as 29 com saldo, veio {len(j)}"
print("   ✅ o filtro de saldo roda no banco, sem perder itens pelo limite")

todas = jaime.get("/api/materiais/busca").get_json()
assert len(todas) == 80, "deveria trazer o catálogo até o limite"
print(f"   ✅ sem filtro, mostra o catálogo completo ({len(todas)} por página)")

busca = jaime.get("/api/materiais/busca?q=abracadeira").get_json()
assert len(busca) >= 2 and all("ABRACADEIRA" in m["descricao"].upper()
                               or "ABRACADEIRA" in m["codigo"] for m in busca)
print(f"   ✅ busca por descrição funciona ({len(busca)} resultados para 'abracadeira')")

por_cod = jaime.get("/api/materiais/busca?q=6996974").get_json()
assert por_cod and por_cod[0]["codigo"] == "6996974"
print("   ✅ busca por código também")

item = por_cod[0]
assert set(item) >= {"codigo", "descricao", "unidade", "tipo", "saldo",
                     "localizacao", "tem_foto", "minimo"}
print(f"   ✅ o catálogo entrega saldo e unidade: "
      f"{item['codigo']} · {item['saldo']:g} {item['unidade']}")

# ══ ESCOLHER E BAIXAR ═════════════════════════════════════════
print("\n── Manutentor escolhe a peça na OS ──")
est = db.um("SELECT id FROM estabelecimentos WHERE nome='Matriz'")
eq = db.um("SELECT id FROM equipamentos LIMIT 1")
sol.post("/os/nova", data={"estabelecimento_id": est["id"], "tipo": "Industrial",
                           "equipamento_id": eq["id"],
                           "descricao_problema": "Trocar abraçadeiras da tubulação"},
         follow_redirects=True)
o = db.um("SELECT * FROM ordens_servico ORDER BY id DESC LIMIT 1")
id_jaime = db.scalar("SELECT id FROM usuarios WHERE usuario='jaime'")
lid.post(f"/os/{o['id']}/assumir", data={"responsavel_id": id_jaime}, follow_redirects=True)
jaime.post(f"/os/{o['id']}/acao/iniciar", follow_redirects=True)

tela = jaime.get(f"/os/{o['id']}").data.decode()
assert 'id="mCatalogo"' in tela and "Catálogo de peças" in tela
print("   ✅ o catálogo está na tela da OS")

antes = db.saldo_material("6996974")
jaime.post(f"/os/{o['id']}/material",
           data={"codigo": "6996974", "descricao": "ABR NYLON PRETO 400MMX4,8MM",
                 "quantidade": "50"}, follow_redirects=True)
depois = db.saldo_material("6996974")
print(f"   escolheu ABR NYLON · saldo {antes:g} → {depois:g}")
assert depois == antes - 50
print("   ✅ baixa automática ao escolher item com saldo")

# Item zerado do catálogo → solicitação
sm_antes = db.scalar("SELECT COUNT(*) AS n FROM solicitacoes_material")
jaime.post(f"/os/{o['id']}/material",
           data={"codigo": "6998543", "descricao": "ABRACADEIRA ACO CARBONO",
                 "quantidade": "10", "pausar": "1"}, follow_redirects=True)
assert db.scalar("SELECT COUNT(*) AS n FROM solicitacoes_material") == sm_antes + 1
sm = db.um("SELECT * FROM solicitacoes_material ORDER BY id DESC LIMIT 1")
print(f"   item zerado → SM #{sm['numero']} de {float(sm['quantidade']):g}")
assert sm["codigo"] == "6998543" and float(sm["quantidade"]) == 10
print("   ✅ item sem saldo vira solicitação para a analista")

# ══ ANALISTA NÃO SOLICITA ═════════════════════════════════════
print("\n── Papéis no material ──")
BLOQ = b"n\xc3\xa3o tem acesso"
r = ana.get("/solicitacoes/nova", follow_redirects=True)
assert BLOQ in r.data, "a analista não deveria abrir o formulário de solicitação"
print("   ✅ a analista não solicita material — ela atende")
assert ana.get("/solicitacoes/", follow_redirects=True).status_code == 200
assert ana.post(f"/solicitacoes/{sm['id']}",
                data={"situacao": "Em cadastro", "comentario": "Cadastrando no SAP"},
                follow_redirects=True).status_code == 200
assert db.scalar("SELECT situacao FROM solicitacoes_material WHERE id=%s",
                 (sm["id"],), default="") == "Em cadastro"
print("   ✅ mas trata e atualiza as solicitações normalmente")

assert BLOQ in jaime.get("/solicitacoes/nova", follow_redirects=True).data
print("   ✅ o manutentor pede peça só de dentro da OS")
assert lid.get("/solicitacoes/nova", follow_redirects=True).status_code == 200
print("   ✅ a liderança usa o formulário avulso para repor estoque")

# ══ NLAG COMPLETO COM A ANALISTA ══════════════════════════════
print("\n── Depósito na área da analista ──")
d = ana.get("/materiais/").data.decode()
for termo in ["Itens cadastrados", "Itens com saldo", "Itens zerados"]:
    assert termo in d, f"faltou o cartão: {termo}"
print("   ✅ dashboard com os três cartões do sistema original")
assert "232" in d and "29" in d
print("   ✅ mostrando 232 cadastrados e 29 com saldo")

ana.post("/materiais/rapida", data={"codigo": "6998543", "operacao": "ENTRADA",
                                    "quantidade": "25", "observacao": "NF 4471"},
         follow_redirects=True)
assert db.saldo_material("6998543") == 25.0
print("   ✅ entrada rápida direto da linha do dashboard")

ana.post("/materiais/rapida", data={"codigo": "6998543", "operacao": "SAIDA",
                                    "quantidade": "5"}, follow_redirects=True)
assert db.saldo_material("6998543") == 20.0
print("   ✅ saída rápida idem")

r = ana.post("/materiais/rapida", data={"codigo": "6998543", "operacao": "SAIDA",
                                        "quantidade": "999"}, follow_redirects=True)
assert db.saldo_material("6998543") == 20.0 and "insuficiente".encode() in r.data
print("   ✅ saída acima do saldo é recusada")

# Cadastro novo, edição de foto e exclusão
ana.post("/materiais/cadastro", data={
    "acao": "novo", "codigo": "9999001", "descricao": "PECA NOVA DE TESTE",
    "unidade": "UNI", "tipo": "NLAG", "estoque_min": "2", "estoque_max": "10",
    "localizacao": "Prateleira A3"}, follow_redirects=True)
assert db.um("SELECT * FROM materiais WHERE codigo='9999001'")
print("   ✅ analista cadastra material novo")

ana.post("/materiais/cadastro", data={"acao": "excluir", "codigo": "9999001"},
         follow_redirects=True)
assert not db.um("SELECT * FROM materiais WHERE codigo='9999001'")
print("   ✅ e exclui quando não há movimentação")

ana.post("/materiais/cadastro", data={"acao": "excluir", "codigo": "6996974"},
         follow_redirects=True)
m = db.um("SELECT ativo FROM materiais WHERE codigo='6996974'")
assert m and m["ativo"] is False
print("   ✅ item com histórico é desativado, não apagado — o histórico fica de pé")

# ══ ENTRADA COM ETIQUETA ══════════════════════════════════════
print("\n── Entrada com geração de etiqueta ──")
d = ana.get("/materiais/entrada").data.decode()
for termo in ["Bipe, digite ou use a câmera", "Quantidade de etiquetas",
              "Imprimir na Zebra", "html5-qrcode"]:
    assert termo in d, f"faltou na tela de entrada: {termo}"
print("   ✅ formulário, leitor de câmera e painel de impressão")

d = ana.get("/materiais/entrada?codigo=6996974").data.decode()
assert "data:image/png;base64" in d and "NLAG · MANUTENÇÃO INDUSTRIAL" in d
assert "DEC-Zebra003" in d
print("   ✅ prévia da etiqueta com código de barras e orientação da impressora")

antes = db.saldo_material("6996974")
r = ana.post("/materiais/entrada",
             data={"codigo": "6996974", "quantidade": "25", "observacao": "NF 8842"},
             follow_redirects=True)
d = r.data.decode()
assert db.saldo_material("6996974") == antes + 25
assert "Imprima a etiqueta ao lado" in d
assert "data:image/png;base64" in d
print(f"   ✅ entrada lançada ({antes:g} → {db.saldo_material('6996974'):g}) "
      "e etiqueta já pronta na mesma tela")
assert "NF 8842" in d
print("   ✅ a movimentação aparece na lista, com botão de reimprimir")

# ── A etiqueta em si ──
r = ana.get("/materiais/print/6996974")
assert r.status_code == 200
et = r.data.decode()
for termo in ["size: 100mm 50mm landscape", "padding: 7mm 3mm 2mm 13mm",
              "NLAG · MANUTENÇÃO INDUSTRIAL", "height: 14mm", "width: 94mm",
              "image-rendering: pixelated", "window.print()", "window.close()"]:
    assert termo in et, f"a etiqueta perdeu: {termo}"
assert "ABR NYLON PRETO 400MMX4,8MM" in et and "6996974" in et
print("   ✅ etiqueta 100×50mm idêntica à do depósito NLAG, com impressão automática")

assert ana.get("/materiais/print/NAOEXISTE").status_code == 404
print("   ✅ código inexistente devolve 404 em vez de etiqueta em branco")

# O mesmo formato em todos os pontos do sistema
for rota in ["/materiais/", "/materiais/etiquetas?q=NYLON"]:
    assert "materiais/print/" in ana.get(rota).data.decode(), f"{rota} usa outro formato"
print("   ✅ dashboard e tela de etiquetas usam a mesma etiqueta")

d = ana.get("/materiais/saida").data.decode()
assert "html5-qrcode" in d
print("   ✅ a saída também lê código de barras pela câmera")

print("\n" + "=" * 60)
print("✅ MIGRAÇÃO E CATÁLOGO VALIDADOS")
print("=" * 60)
