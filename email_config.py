"""
CONFIGURAÇÃO DE E-MAIL
Os dados do servidor de envio ficam no banco e são editáveis pela tela
(Administração → E-mail), sem precisar de novo deploy.

A senha é gravada criptografada com uma chave derivada da SECRET_KEY da
aplicação, e nunca aparece nos backups nem na tela.
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

import db

# Chave usada no banco para guardar a senha (excluída dos backups)
CHAVE_SENHA = "smtp_senha"

# Provedores prontos. "sem_2fa" marca os que NÃO exigem verificação em duas
# etapas nem senha de aplicativo — funcionam só com uma chave gerada no painel.
PROVEDORES = {
    "brevo": {
        "rotulo": "Brevo", "host": "smtp-relay.brevo.com", "porta": 587,
        "seguranca": "tls", "sem_2fa": True, "usuario_fixo": None,
        "resumo": "Serviço de envio gratuito · 300 e-mails/dia · sem 2FA",
        "aviso": "Recomendado quando não é possível ativar verificação em duas "
                 "etapas na conta de e-mail.",
        "passos": [
            "Crie uma conta gratuita em <code>brevo.com</code>",
            "No painel, abra <strong>SMTP &amp; API</strong> → aba <strong>SMTP</strong>",
            "Copie o <strong>Login</strong> (um e-mail terminado em @smtp-brevo.com) "
            "e cole no campo “E-mail que envia”",
            "Clique em <strong>Generate a new SMTP key</strong> e cole a chave no campo Senha",
            "Em <strong>Senders</strong>, cadastre e confirme "
            "<code>engenhariadecio2026@gmail.com</code> como remetente",
            "Volte aqui e informe esse mesmo endereço no campo "
            "“Remetente (se diferente da conta)”",
        ],
    },
    "gmail": {
        "rotulo": "Gmail", "host": "smtp.gmail.com", "porta": 587,
        "seguranca": "tls", "sem_2fa": False, "usuario_fixo": None,
        "resumo": "Conta Google · 500 e-mails/dia · exige 2FA",
        "aviso": "O Gmail <strong>não aceita a senha normal da conta</strong>: é "
                 "preciso ativar a verificação em duas etapas e gerar uma senha de "
                 "aplicativo de 16 caracteres. Use uma <strong>conta exclusiva do "
                 "sistema</strong>, que ninguém acessa no dia a dia — assim o 2FA "
                 "fica só com o administrador e não atrapalha ninguém.",
        "passos": [
            "Use uma conta Google <strong>criada só para o sistema</strong>, "
            "com o celular do administrador como telefone de recuperação",
            "Entre em <code>myaccount.google.com</code> → <strong>Segurança</strong> "
            "e ative a <strong>verificação em duas etapas</strong>",
            "Abra <code>myaccount.google.com/apppasswords</code> — o atalho direto, "
            "porque o Google escondeu o link da tela de Segurança",
            "Dê o nome “Sistema de Manutenção” e clique em Criar",
            "Copie os 16 caracteres <strong>na hora</strong> (o Google mostra uma "
            "única vez) e cole no campo Senha aqui ao lado, com ou sem os espaços",
            "Salve e use o botão <strong>Testar</strong> logo abaixo",
        ],
    },
    "sendgrid": {
        "rotulo": "SendGrid", "host": "smtp.sendgrid.net", "porta": 587,
        "seguranca": "tls", "sem_2fa": True, "usuario_fixo": "apikey",
        "resumo": "Serviço de envio · 100 e-mails/dia grátis · sem 2FA",
        "aviso": "O usuário é literalmente a palavra <code>apikey</code> — o sistema "
                 "preenche sozinho. A senha é a chave gerada no painel.",
        "passos": [
            "Crie a conta em <code>sendgrid.com</code>",
            "Abra <strong>Settings → API Keys</strong> e gere uma chave com "
            "permissão de <strong>Mail Send</strong>",
            "Cole a chave no campo Senha (o usuário já vem preenchido)",
            "Em <strong>Sender Authentication</strong>, verifique o endereço "
            "que vai aparecer como remetente",
            "Informe esse endereço no campo “Remetente (se diferente da conta)”",
        ],
    },
    "outlook": {
        "rotulo": "Outlook / Microsoft 365", "host": "smtp.office365.com",
        "porta": 587, "seguranca": "tls", "sem_2fa": False, "usuario_fixo": None,
        "resumo": "Conta corporativa Microsoft",
        "aviso": "A Microsoft desativou autenticação básica na maioria dos tenants. "
                 "Pode ser necessário pedir ao TI um relay SMTP ou uma exceção.",
        "passos": [
            "Confirme com o TI se a conta pode autenticar por SMTP",
            "Se houver verificação em duas etapas, gere uma senha de aplicativo",
            "Informe a conta e a senha nos campos ao lado",
        ],
    },
    "outro": {
        "rotulo": "Outro servidor", "host": "", "porta": 587,
        "seguranca": "tls", "sem_2fa": True, "usuario_fixo": None,
        "resumo": "Servidor próprio ou relay interno da empresa",
        "aviso": "Use quando o TI fornecer um relay SMTP interno — muitos não "
                 "exigem autenticação nenhuma.",
        "passos": [
            "Peça ao TI o servidor, a porta e o tipo de segurança",
            "Se o relay não exigir login, deixe usuário e senha em branco",
        ],
    },
}

PADRAO = {
    "smtp_provedor": "gmail",
    "smtp_host": "smtp.gmail.com",
    "smtp_porta": "587",
    "smtp_seguranca": "tls",
    "smtp_usuario": "",
    "smtp_remetente": "",
    "smtp_nome_remetente": "Manutenção — Décio Metalúrgica",
    "app_url": "",
    "smtp_ativo": "1",
}


# ──────────────────────────────────────────────────────────────────
#  Criptografia da senha
# ──────────────────────────────────────────────────────────────────
def _cofre():
    """Fernet derivado da SECRET_KEY da aplicação."""
    segredo = os.environ.get("SECRET_KEY", "") or "chave-local-de-desenvolvimento"
    chave = base64.urlsafe_b64encode(hashlib.sha256(segredo.encode()).digest())
    return Fernet(chave)


def guardar_senha(texto):
    if not texto:
        return
    cifrada = _cofre().encrypt(texto.encode()).decode()
    db.executar("""INSERT INTO parametros (chave, valor) VALUES (%s,%s)
                   ON CONFLICT (chave) DO UPDATE SET valor=EXCLUDED.valor""",
                (CHAVE_SENHA, cifrada))


def ler_senha():
    guardada = db.scalar("SELECT valor FROM parametros WHERE chave=%s",
                         (CHAVE_SENHA,), default=None)
    if not guardada:
        return os.environ.get("SMTP_SENHA", "")
    try:
        return _cofre().decrypt(guardada.encode()).decode()
    except (InvalidToken, ValueError):
        # A SECRET_KEY mudou — a senha antiga não pode mais ser lida
        return ""


def senha_definida():
    return bool(db.scalar("SELECT valor FROM parametros WHERE chave=%s",
                          (CHAVE_SENHA,), default=None)
                or os.environ.get("SMTP_SENHA"))


def senha_ilegivel():
    """True quando há senha guardada mas a SECRET_KEY mudou."""
    return bool(db.scalar("SELECT valor FROM parametros WHERE chave=%s",
                          (CHAVE_SENHA,), default=None)) and not ler_senha()


def apagar_senha():
    db.executar("DELETE FROM parametros WHERE chave=%s", (CHAVE_SENHA,))


# ──────────────────────────────────────────────────────────────────
#  Leitura e gravação da configuração
# ──────────────────────────────────────────────────────────────────
_MAPA_ENV = {
    "smtp_host": "SMTP_HOST",
    "smtp_porta": "SMTP_PORT",
    "smtp_seguranca": "SMTP_SEGURANCA",
    "smtp_usuario": "SMTP_USUARIO",
    "smtp_remetente": "SMTP_REMETENTE",
    "app_url": "APP_URL",
}


def obter(chave):
    """Banco → variável de ambiente → padrão."""
    valor = db.scalar("SELECT valor FROM parametros WHERE chave=%s",
                      (chave,), default=None)
    if valor not in (None, ""):
        return valor
    env = _MAPA_ENV.get(chave)
    if env:
        do_ambiente = os.environ.get(env, "").strip()
        if do_ambiente:
            return do_ambiente
    return PADRAO.get(chave, "")


def gravar(chave, valor):
    db.executar("""INSERT INTO parametros (chave, valor) VALUES (%s,%s)
                   ON CONFLICT (chave) DO UPDATE SET valor=EXCLUDED.valor""",
                (chave, (valor or "").strip()))


def configuracao():
    """Configuração completa em uso, sem a senha."""
    cfg = {chave: obter(chave) for chave in PADRAO}
    try:
        cfg["smtp_porta"] = int(cfg["smtp_porta"] or 587)
    except (TypeError, ValueError):
        cfg["smtp_porta"] = 587
    cfg["senha_definida"] = senha_definida()
    cfg["senha_ilegivel"] = senha_ilegivel()
    cfg["ativo"] = str(cfg.get("smtp_ativo", "1")) == "1"
    cfg["completo"] = bool(cfg["smtp_host"] and cfg["smtp_remetente"]
                           and cfg["senha_definida"])
    return cfg


def salvar_formulario(form):
    """Grava o formulário da tela de administração."""
    provedor = form.get("smtp_provedor", "outro")
    gravar("smtp_provedor", provedor)

    if provedor in PROVEDORES and provedor != "outro":
        p = PROVEDORES[provedor]
        gravar("smtp_host", p["host"])
        gravar("smtp_porta", str(p["porta"]))
        gravar("smtp_seguranca", p["seguranca"])
    else:
        gravar("smtp_host", form.get("smtp_host", ""))
        gravar("smtp_porta", form.get("smtp_porta", "587"))
        gravar("smtp_seguranca", form.get("smtp_seguranca", "tls"))

    usuario = (form.get("smtp_usuario") or "").strip()
    # Alguns serviços exigem um usuário fixo (o SendGrid usa "apikey")
    fixo = PROVEDORES.get(provedor, {}).get("usuario_fixo")
    if fixo:
        usuario = fixo
    gravar("smtp_usuario", usuario)
    # Sem remetente informado, usa a própria conta que autentica
    remetente = (form.get("smtp_remetente") or "").strip()
    if not remetente:
        remetente = "" if fixo else usuario
    gravar("smtp_remetente", remetente)
    gravar("smtp_nome_remetente",
           form.get("smtp_nome_remetente") or PADRAO["smtp_nome_remetente"])
    gravar("app_url", (form.get("app_url") or "").strip().rstrip("/"))
    gravar("smtp_ativo", "1" if form.get("smtp_ativo") == "1" else "0")

    senha = form.get("smtp_senha") or ""
    # Espaços no meio são comuns ao copiar a senha de app do Google
    senha = senha.replace(" ", "")
    if senha:
        guardar_senha(senha)
