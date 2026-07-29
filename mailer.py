"""
ENVIO DE E-MAIL
Notificações por e-mail dos eventos da manutenção.

Tudo é configurado pela tela (Administração → E-mail): servidor, conta de
envio, quais eventos disparam e-mail. Nada exige novo deploy.

As variáveis de ambiente SMTP_* continuam funcionando como valor inicial,
mas o que estiver gravado no banco tem prioridade.
"""
import os
import re
import smtplib
import threading
import traceback
from email.message import EmailMessage
from email.utils import formataddr

import db
import email_config

# ── Configuração (vem do banco; ver email_config.py) ──
SMTP_TIMEOUT = int(os.environ.get("SMTP_TIMEOUT", "20") or 20)


def cfg():
    """Configuração atual do servidor de envio."""
    try:
        return email_config.configuracao()
    except Exception:
        return dict(email_config.PADRAO, smtp_porta=587, senha_definida=False,
                    senha_ilegivel=False, ativo=False, completo=False)


# Eventos disponíveis: chave → (rótulo, padrão ligado?)
EVENTOS = {
    "os_aberta":         ("Nova OS aberta — avisa a equipe de manutenção", True),
    "os_atribuida":      ("OS atribuída — avisa o manutentor designado", True),
    "os_concluida":      ("OS concluída — avisa o solicitante para aprovar", True),
    "os_aprovada":       ("OS aprovada — avisa o manutentor", True),
    "os_reprovada":      ("OS reprovada — avisa o manutentor e a liderança", True),
    "material_solicitado": ("Peça solicitada — avisa o analista de materiais", True),
    "material_recebido": ("Material recebido — avisa o manutentor da OS", True),
    "om_atribuida":      ("Preventiva atribuída — avisa o manutentor", True),
    "ronda_atribuida":   ("Ronda destinada — avisa o manutentor", True),
    "estoque_minimo":    ("Estoque mínimo atingido — avisa o analista", False),
    "preventiva_semana": ("Preventivas da semana — avisa os responsáveis", False),
}

# Guarda o último erro para exibir na tela de configuração
_ULTIMO_ERRO = {"mensagem": None, "quando": None}

# Em testes, o transporte é substituído para capturar as mensagens
_CAIXA_DE_TESTE = []
_MODO_TESTE = False


def configurado():
    c = cfg()
    return bool(c["completo"] and c["ativo"])


def evento_ativo(evento):
    """Consulta se o evento está ligado (padrão vem de EVENTOS)."""
    padrao = EVENTOS.get(evento, ("", False))[1]
    try:
        v = db.scalar("SELECT valor FROM parametros WHERE chave=%s",
                      (f"email_{evento}",), default=None)
    except Exception:
        return padrao
    if v is None:
        return padrao
    return str(v).strip() in ("1", "true", "True", "sim")


def email_valido(endereco):
    return bool(endereco) and re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", endereco.strip())


def emails_dos_usuarios(ids):
    """E-mails válidos e ativos a partir de uma lista de IDs de usuário."""
    limpos = []
    for i in ids or []:
        try:
            if i is not None:
                limpos.append(int(i))
        except (TypeError, ValueError):
            continue
    if not limpos:
        return []
    linhas = db.query("""SELECT nome, email FROM usuarios
                         WHERE id = ANY(%s) AND ativo=TRUE AND email IS NOT NULL""",
                      (list(set(limpos)),))
    return [formataddr((u["nome"], u["email"].strip()))
            for u in linhas or [] if email_valido(u["email"])]


def emails_dos_perfis(perfis):
    linhas = db.query("""SELECT nome, email FROM usuarios
                         WHERE perfil = ANY(%s) AND ativo=TRUE AND email IS NOT NULL""",
                      (list(perfis),))
    return [formataddr((u["nome"], u["email"].strip()))
            for u in linhas or [] if email_valido(u["email"])]


def base_url():
    """
    Endereço público do sistema. Usa o que estiver configurado; se estiver
    em branco, deduz do próprio acesso em curso — assim os links funcionam
    sem ninguém precisar preencher nada.
    """
    salvo = (email_config.obter("app_url") or "").rstrip("/")
    if salvo:
        return salvo
    try:
        from flask import request
        if request:
            return request.host_url.rstrip("/")
    except Exception:
        pass
    return ""


def url(caminho):
    """Monta a URL absoluta usada nos botões do e-mail."""
    if not caminho:
        return base_url()
    if caminho.startswith("http"):
        return caminho
    base = base_url()
    return f"{base}{caminho}" if base else caminho


# ──────────────────────────────────────────────────────────────────
#  MODELO HTML
# ──────────────────────────────────────────────────────────────────
def montar_html(titulo, subtitulo, itens, mensagem="", botao=None, rodape=""):
    """
    itens: lista de (rótulo, valor) que vira a tabela de detalhes
    botao: (texto, url)
    """
    linhas = "".join(
        f'<tr>'
        f'<td style="padding:7px 14px 7px 0;color:#8B94A3;font-size:13px;'
        f'white-space:nowrap;vertical-align:top">{r}</td>'
        f'<td style="padding:7px 0;color:#16202E;font-size:13px;font-weight:600">'
        f'{v if v not in (None, "") else "—"}</td></tr>'
        for r, v in (itens or []))

    bloco_msg = ""
    if mensagem:
        bloco_msg = (
            f'<div style="background:#F2F5FA;border-left:3px solid #28A353;'
            f'padding:12px 16px;border-radius:6px;margin:18px 0;'
            f'color:#2C4257;font-size:14px;line-height:1.5">{mensagem}</div>')

    bloco_botao = ""
    if botao:
        texto, link = botao
        bloco_botao = (
            f'<div style="margin:26px 0 6px"><a href="{link}" '
            f'style="background:linear-gradient(120deg,#28A353 0%,#10477D 100%);'
            f'color:#fff;text-decoration:none;padding:13px 30px;border-radius:9px;'
            f'font-weight:700;font-size:14px;display:inline-block">{texto}</a></div>')

    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#EEF1F6;
 font-family:'Segoe UI',Arial,Helvetica,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
 style="background:#EEF1F6;padding:26px 12px">
<tr><td align="center">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0"
   style="max-width:600px;width:100%;background:#fff;border-radius:14px;
   overflow:hidden;box-shadow:0 4px 18px rgba(20,32,46,.10)">

    <tr><td style="background:linear-gradient(120deg,#28A353 0%,#10477D 100%);
     padding:22px 26px">
      <div style="color:rgba(255,255,255,.82);font-size:11px;font-weight:700;
       letter-spacing:.15em;text-transform:uppercase">Décio Metalúrgica</div>
      <div style="color:#fff;font-size:20px;font-weight:800;margin-top:3px">{titulo}</div>
      {f'<div style="color:rgba(255,255,255,.9);font-size:13px;margin-top:4px">{subtitulo}</div>' if subtitulo else ''}
    </td></tr>

    <tr><td style="padding:24px 26px">
      {bloco_msg}
      <table role="presentation" cellpadding="0" cellspacing="0" width="100%">{linhas}</table>
      {bloco_botao}
    </td></tr>

    <tr><td style="background:#F7F9FC;padding:16px 26px;border-top:1px solid #E4E9F1">
      <div style="color:#8B94A3;font-size:11px;line-height:1.6">
        {rodape or 'Mensagem automática do Sistema Centralizado de Manutenção.'}<br>
        Não responda a este e-mail — use o sistema para registrar apontamentos.
      </div>
    </td></tr>
  </table>
</td></tr></table></body></html>"""


def montar_texto(titulo, itens, mensagem="", botao=None):
    partes = [titulo, "=" * len(titulo), ""]
    if mensagem:
        partes += [re.sub(r"<[^>]+>", "", mensagem), ""]
    for r, v in itens or []:
        partes.append(f"{r}: {v if v not in (None, '') else '—'}")
    if botao:
        partes += ["", f"{botao[0]}: {botao[1]}"]
    partes += ["", "Décio Metalúrgica — Sistema Centralizado de Manutenção"]
    return "\n".join(partes)


# ──────────────────────────────────────────────────────────────────
#  ENVIO
# ──────────────────────────────────────────────────────────────────
def _transportar(destinatarios, assunto, html, texto):
    """Fala com o servidor SMTP. Levanta exceção em caso de falha."""
    if _MODO_TESTE:
        _CAIXA_DE_TESTE.append({"para": destinatarios, "assunto": assunto,
                                "html": html, "texto": texto})
        return True

    msg = EmailMessage()
    msg["Subject"] = assunto
    c = cfg()
    msg["From"] = formataddr((c["smtp_nome_remetente"] or NOME_REMETENTE,
                              c["smtp_remetente"]))
    msg["To"] = ", ".join(destinatarios)
    msg.set_content(texto)
    msg.add_alternative(html, subtype="html")

    if c["smtp_seguranca"] == "ssl":
        servidor = smtplib.SMTP_SSL(c["smtp_host"], c["smtp_porta"], timeout=SMTP_TIMEOUT)
    else:
        servidor = smtplib.SMTP(c["smtp_host"], c["smtp_porta"], timeout=SMTP_TIMEOUT)
    try:
        servidor.ehlo()
        if c["smtp_seguranca"] == "tls":
            servidor.starttls()
            servidor.ehlo()
        if c["smtp_usuario"]:
            servidor.login(c["smtp_usuario"], email_config.ler_senha())
        servidor.send_message(msg)
    finally:
        try:
            servidor.quit()
        except Exception:
            pass
    return True


def enviar_agora(destinatarios, assunto, html, texto=""):
    """Envio síncrono. Devolve (ok, mensagem_de_erro)."""
    destinatarios = [d for d in (destinatarios or []) if d]
    if not destinatarios:
        return False, "Nenhum destinatário com e-mail cadastrado."
    if not configurado() and not _MODO_TESTE:
        return False, ("Envio de e-mail não configurado ou desativado. "
                       "Ajuste em Administração → E-mail.")
    try:
        _transportar(destinatarios, assunto, html, texto or montar_texto(assunto, []))
        _ULTIMO_ERRO["mensagem"] = None
        return True, ""
    except Exception as e:
        erro = f"{type(e).__name__}: {e}"
        _ULTIMO_ERRO["mensagem"] = erro
        _ULTIMO_ERRO["quando"] = db.agora()
        print(f"[email] falha ao enviar: {erro}", flush=True)
        traceback.print_exc()
        return False, erro


def enviar(destinatarios, assunto, html, texto=""):
    """
    Envio em segundo plano — a tela do usuário não espera pelo servidor
    de e-mail. Falhas são registradas no log, nunca derrubam a requisição.
    """
    destinatarios = [d for d in (destinatarios or []) if d]
    if not destinatarios or (not configurado() and not _MODO_TESTE):
        return False
    if _MODO_TESTE:
        enviar_agora(destinatarios, assunto, html, texto)
        return True
    threading.Thread(target=enviar_agora,
                     args=(destinatarios, assunto, html, texto),
                     daemon=True).start()
    return True


def avisar(evento, destinatarios, assunto, titulo, itens,
           subtitulo="", mensagem="", botao=None, rodape=""):
    """
    Ponto único usado pelo sistema: confere se o evento está ligado,
    monta o modelo e envia.
    """
    if not evento_ativo(evento):
        return False
    html = montar_html(titulo, subtitulo, itens, mensagem, botao, rodape)
    texto = montar_texto(titulo, itens, mensagem, botao)
    return enviar(destinatarios, assunto, html, texto)


def status():
    """Resumo da configuração, para a tela de administração."""
    c = cfg()
    return {
        "configurado": configurado(),
        "host": c["smtp_host"] or "—",
        "porta": c["smtp_porta"],
        "usuario": c["smtp_usuario"] or "—",
        "remetente": c["smtp_remetente"] or "—",
        "nome_remetente": c["smtp_nome_remetente"],
        "seguranca": c["smtp_seguranca"],
        "senha_definida": c["senha_definida"],
        "senha_ilegivel": c["senha_ilegivel"],
        "ativo": c["ativo"],
        "completo": c["completo"],
        "provedor": c["smtp_provedor"],
        "app_url": base_url() or "—",
        "app_url_automatica": not email_config.obter("app_url"),
        "ultimo_erro": _ULTIMO_ERRO["mensagem"],
        "erro_em": _ULTIMO_ERRO["quando"],
    }


# ── Apoio a testes ──
def modo_teste(ligado=True):
    global _MODO_TESTE
    _MODO_TESTE = ligado
    _CAIXA_DE_TESTE.clear()


def caixa_de_teste():
    return _CAIXA_DE_TESTE
