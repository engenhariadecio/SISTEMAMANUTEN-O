"""
Autenticação e controle de acesso por perfil.
"""
from functools import wraps
from flask import session, redirect, url_for, flash, request, abort

# ── Perfis do sistema ──────────────────────────────────────────────
PERFIS = {
    "admin":       "Administrador",
    "supervisao":  "Supervisão",
    "lider":       "Líder de Manutenção",
    "analista":    "Analista de Materiais",
    "manutentor":  "Manutentor",
    "solicitante": "Solicitante de OS",
    "visualizador": "Visualizador",
}

# Grupos usados nas verificações
GESTAO = ("admin", "supervisao", "lider")
GESTAO_ANALISTA = ("admin", "supervisao", "lider", "analista")
EXECUCAO = ("admin", "supervisao", "lider", "manutentor")
TODOS = tuple(PERFIS.keys())

# ── Permissões por módulo (usado no menu e nas rotas) ──────────────
#
# O depósito NLAG (entradas, saídas, cadastro, inventário, etiquetas,
# coletor e importações) pertence ao Analista de Materiais.
# O manutentor NÃO abre o módulo de materiais: ele pede a peça de dentro
# da OS e o sistema decide — se houver saldo NLAG, dá baixa na hora;
# se não houver, gera a solicitação para o analista.
#
PERMISSOES = {
    "os_abrir":         TODOS,
    "os_ver_todas":     ("admin", "supervisao", "lider", "manutentor", "analista"),
    "os_executar":      EXECUCAO,
    "os_triagem":       GESTAO,          # só a liderança distribui as OS
    "os_aprovar":       TODOS,          # o solicitante aprova a própria OS
    # Planejamento das preventivas (grade 52 semanas, planos, plano de materiais)
    "preventiva_ver":   ("admin", "supervisao", "lider", "analista"),
    # Executar uma OM que lhe foi atribuída
    "preventiva_exec":  EXECUCAO,
    "preventiva_cad":   GESTAO,
    # Executar a ronda que lhe foi destinada
    "ronda_exec":       EXECUCAO,
    # Criar rondas e destinar a um manutentor
    "ronda_cad":        GESTAO,

    # ── Depósito NLAG — analista de materiais e gestão ──
    "material_ver":     ("admin", "supervisao", "lider", "analista", "visualizador"),
    "material_mov":     ("admin", "supervisao", "lider", "analista"),
    "material_cad":     GESTAO_ANALISTA,

    # ── Solicitar peça: o manutentor faz, de dentro da OS ──
    "solicitar_material": EXECUCAO + ("analista",),
    "tratar_solicitacao": GESTAO_ANALISTA,

    "indicadores":      ("admin", "supervisao", "lider", "analista"),
    "relatorios":       ("admin", "supervisao", "lider", "analista"),
    "backup":           ("admin", "supervisao"),
    "admin":            ("admin",),
    "cadastros":        GESTAO,
}


def usuario_atual():
    if "uid" not in session:
        return None
    return {
        "id": session.get("uid"),
        "nome": session.get("nome"),
        "usuario": session.get("usuario"),
        "perfil": session.get("perfil"),
        "email": session.get("email"),
    }


def perfil_atual():
    return session.get("perfil")


def pode(chave):
    """Verifica se o perfil logado tem a permissão informada."""
    p = perfil_atual()
    if p == "admin":
        return True
    return p in PERMISSOES.get(chave, ())


def login_obrigatorio(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "uid" not in session:
            return redirect(url_for("auth.login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


def exige(chave):
    """Decorator: exige a permissão informada."""
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if "uid" not in session:
                return redirect(url_for("auth.login", next=request.path))
            if not pode(chave):
                flash("Seu perfil não tem acesso a esta área.", "warning")
                return redirect(url_for("home.index"))
            return f(*args, **kwargs)
        return wrapper
    return deco


def exige_perfil(*perfis):
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if "uid" not in session:
                return redirect(url_for("auth.login", next=request.path))
            if session.get("perfil") not in perfis and session.get("perfil") != "admin":
                flash("Seu perfil não tem acesso a esta área.", "warning")
                return redirect(url_for("home.index"))
            return f(*args, **kwargs)
        return wrapper
    return deco
