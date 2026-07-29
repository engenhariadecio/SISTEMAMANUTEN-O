"""
Camada de acesso a dados — PostgreSQL.
Sistema Centralizado de Manutenção — Décio Metalúrgica
"""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras
from psycopg2 import pool as pgpool
from werkzeug.security import generate_password_hash

DATABASE_URL = os.environ.get("DATABASE_URL", "")
TZ_BR = ZoneInfo("America/Sao_Paulo")

_POOL = None


def agora():
    """Horário de Brasília (o servidor do Railway roda em UTC)."""
    return datetime.now(TZ_BR)


def hoje():
    return agora().date()


def fmt(valor, formato="%d/%m/%Y %H:%M"):
    """Formata data/hora no fuso de Brasília para uso em textos e e-mails."""
    if not valor:
        return "—"
    try:
        return valor.astimezone(TZ_BR).strftime(formato)
    except (AttributeError, ValueError, TypeError):
        try:
            return valor.strftime(formato)
        except Exception:
            return str(valor)


def get_pool():
    global _POOL
    if _POOL is None:
        url = DATABASE_URL
        # Railway às vezes entrega postgres:// (formato legado do psycopg2)
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        _POOL = pgpool.ThreadedConnectionPool(1, 12, url)
    return _POOL


class conexao:
    """Context manager que devolve a conexão ao pool no final."""

    def __init__(self, commit=False):
        self.commit = commit
        self.conn = None

    def __enter__(self):
        self.conn = get_pool().getconn()
        self.cur = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        return self.cur

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None and self.commit:
                self.conn.commit()
            elif exc_type is not None:
                self.conn.rollback()
        finally:
            get_pool().putconn(self.conn)
        return False


def query(sql, params=None, one=False, commit=False):
    """Executa SQL. Devolve lista de dicts, um dict, ou None."""
    with conexao(commit=commit) as cur:
        cur.execute(sql, params or ())
        if commit and not cur.description:
            return None
        if not cur.description:
            return None
        if one:
            return cur.fetchone()
        return cur.fetchall()


def executar(sql, params=None):
    """INSERT/UPDATE/DELETE sem retorno."""
    with conexao(commit=True) as cur:
        cur.execute(sql, params or ())


def inserir(sql, params=None):
    """INSERT ... RETURNING id — devolve o id gerado."""
    with conexao(commit=True) as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row["id"] if row else None


def um(sql, params=None):
    return query(sql, params, one=True)


def scalar(sql, params=None, default=0):
    row = query(sql, params, one=True)
    if not row:
        return default
    valor = list(row.values())[0]
    return default if valor is None else valor


# ──────────────────────────────────────────────────────────────────
#  SCHEMA
# ──────────────────────────────────────────────────────────────────
SCHEMA = """
-- ═══════════ USUÁRIOS E PERMISSÕES ═══════════
CREATE TABLE IF NOT EXISTS usuarios (
    id          SERIAL PRIMARY KEY,
    usuario     TEXT UNIQUE NOT NULL,
    senha_hash  TEXT NOT NULL,
    nome        TEXT NOT NULL,
    email       TEXT,
    matricula   TEXT,
    perfil      TEXT NOT NULL DEFAULT 'solicitante',
    telefone    TEXT,
    ativo       BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em   TIMESTAMPTZ DEFAULT NOW(),
    ultimo_acesso TIMESTAMPTZ
);

-- ═══════════ CADASTROS BÁSICOS ═══════════
CREATE TABLE IF NOT EXISTS estabelecimentos (
    id     SERIAL PRIMARY KEY,
    nome   TEXT UNIQUE NOT NULL,
    codigo TEXT,
    ativo  BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS centros_trabalho (
    id                 SERIAL PRIMARY KEY,
    codigo             TEXT UNIQUE NOT NULL,
    nome               TEXT NOT NULL,
    estabelecimento_id INTEGER REFERENCES estabelecimentos(id),
    responsavel_id     INTEGER REFERENCES usuarios(id),
    ativo              BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS centros_custo (
    id     SERIAL PRIMARY KEY,
    codigo TEXT UNIQUE NOT NULL,
    nome   TEXT NOT NULL,
    ativo  BOOLEAN NOT NULL DEFAULT TRUE
);

-- ═══════════ CRITICIDADE (configurável) ═══════════
CREATE TABLE IF NOT EXISTS criticidades (
    codigo          CHAR(1) PRIMARY KEY,
    nome            TEXT NOT NULL,
    descricao       TEXT,
    ordem           INTEGER NOT NULL,
    cor             TEXT NOT NULL DEFAULT '#5B93C4',
    sla_resposta_h  NUMERIC(8,2),
    sla_conclusao_h NUMERIC(8,2),
    ativo           BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS equipamentos (
    id                 SERIAL PRIMARY KEY,
    grupo_prev         TEXT NOT NULL,
    subcodigo          TEXT NOT NULL DEFAULT '00',
    codigo             TEXT UNIQUE NOT NULL,
    nome               TEXT NOT NULL,
    descricao          TEXT,
    centro_trabalho_id INTEGER REFERENCES centros_trabalho(id),
    estabelecimento_id INTEGER REFERENCES estabelecimentos(id),
    criticidade        CHAR(1) NOT NULL DEFAULT 'C',
    tipo               TEXT NOT NULL DEFAULT 'Industrial',
    n_serie            TEXT,
    fabricante         TEXT,
    patrimonio         TEXT,
    ano_fabricacao     TEXT,
    capacidade         TEXT,
    custo_hora_parada  NUMERIC(12,2) DEFAULT 0,
    custo_hh           NUMERIC(12,2) DEFAULT 0,
    horimetro          NUMERIC(12,2) DEFAULT 0,
    status             TEXT NOT NULL DEFAULT 'operando',
    ativo              BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_equip_ct ON equipamentos(centro_trabalho_id);

-- Matriz de criticidade: notas 0..4 por critério + pontuação apurada
ALTER TABLE equipamentos ADD COLUMN IF NOT EXISTS mtz_seguranca   SMALLINT;
ALTER TABLE equipamentos ADD COLUMN IF NOT EXISTS mtz_producao    SMALLINT;
ALTER TABLE equipamentos ADD COLUMN IF NOT EXISTS mtz_qualidade   SMALLINT;
ALTER TABLE equipamentos ADD COLUMN IF NOT EXISTS mtz_frequencia  SMALLINT;
ALTER TABLE equipamentos ADD COLUMN IF NOT EXISTS mtz_reparo      SMALLINT;
ALTER TABLE equipamentos ADD COLUMN IF NOT EXISTS mtz_redundancia SMALLINT;
ALTER TABLE equipamentos ADD COLUMN IF NOT EXISTS mtz_pontuacao   NUMERIC(6,2);
ALTER TABLE equipamentos ADD COLUMN IF NOT EXISTS mtz_avaliado_em TIMESTAMPTZ;
ALTER TABLE equipamentos ADD COLUMN IF NOT EXISTS mtz_avaliado_por INTEGER;
ALTER TABLE equipamentos ADD COLUMN IF NOT EXISTS mtz_justificativa TEXT;

CREATE TABLE IF NOT EXISTS defeitos (
    id    SERIAL PRIMARY KEY,
    nome  TEXT UNIQUE NOT NULL,
    ativo BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS causas (
    id         SERIAL PRIMARY KEY,
    nome       TEXT UNIQUE NOT NULL,
    defeito_id INTEGER REFERENCES defeitos(id),
    ativo      BOOLEAN NOT NULL DEFAULT TRUE
);

-- ═══════════ ORDENS DE SERVIÇO (CORRETIVAS) ═══════════
CREATE TABLE IF NOT EXISTS ordens_servico (
    id                 SERIAL PRIMARY KEY,
    numero             INTEGER UNIQUE,
    tipo_manutencao    TEXT NOT NULL DEFAULT 'corretiva',
    tipo               TEXT NOT NULL DEFAULT 'Industrial',
    estabelecimento_id INTEGER REFERENCES estabelecimentos(id),
    centro_trabalho_id INTEGER REFERENCES centros_trabalho(id),
    equipamento_id     INTEGER REFERENCES equipamentos(id),
    equipamento_outro  TEXT,
    descricao_problema TEXT NOT NULL,
    solicitante_id     INTEGER REFERENCES usuarios(id),
    responsavel_id     INTEGER REFERENCES usuarios(id),
    criticidade        CHAR(1) NOT NULL DEFAULT 'C',
    maquina_parada     BOOLEAN NOT NULL DEFAULT FALSE,
    status             TEXT NOT NULL DEFAULT 'aberta',
    acao_realizada     TEXT,
    defeito_id         INTEGER REFERENCES defeitos(id),
    causa_id           INTEGER REFERENCES causas(id),
    origem             TEXT DEFAULT 'manual',
    origem_id          INTEGER,
    data_abertura      TIMESTAMPTZ DEFAULT NOW(),
    data_inicio        TIMESTAMPTZ,
    data_conclusao     TIMESTAMPTZ,
    data_aprovacao     TIMESTAMPTZ,
    aprovado           BOOLEAN,
    comentario_reprova TEXT,
    tempo_trabalho_seg INTEGER NOT NULL DEFAULT 0,
    tempo_parada_seg   INTEGER NOT NULL DEFAULT 0,
    custo_pecas        NUMERIC(12,2) DEFAULT 0,
    custo_hh           NUMERIC(12,2) DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_os_status ON ordens_servico(status);
CREATE INDEX IF NOT EXISTS idx_os_equip  ON ordens_servico(equipamento_id);
CREATE INDEX IF NOT EXISTS idx_os_data   ON ordens_servico(data_abertura);

CREATE TABLE IF NOT EXISTS os_anexos (
    id        SERIAL PRIMARY KEY,
    os_id     INTEGER REFERENCES ordens_servico(id) ON DELETE CASCADE,
    nome      TEXT NOT NULL,
    mime      TEXT,
    dados     BYTEA,
    usuario_id INTEGER REFERENCES usuarios(id),
    criado_em TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS os_apontamentos (
    id         SERIAL PRIMARY KEY,
    os_id      INTEGER REFERENCES ordens_servico(id) ON DELETE CASCADE,
    usuario_id INTEGER REFERENCES usuarios(id),
    tipo       TEXT NOT NULL,
    descricao  TEXT,
    criado_em  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_apont_os ON os_apontamentos(os_id);

-- Cronômetro: cada intervalo de trabalho/pausa/almoço/aguardando peça
CREATE TABLE IF NOT EXISTS os_tempos (
    id         SERIAL PRIMARY KEY,
    os_id      INTEGER REFERENCES ordens_servico(id) ON DELETE CASCADE,
    usuario_id INTEGER REFERENCES usuarios(id),
    tipo       TEXT NOT NULL,
    inicio     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fim        TIMESTAMPTZ,
    duracao_seg INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tempos_os ON os_tempos(os_id);

CREATE TABLE IF NOT EXISTS os_materiais (
    id           SERIAL PRIMARY KEY,
    os_id        INTEGER REFERENCES ordens_servico(id) ON DELETE CASCADE,
    material_id  INTEGER,
    codigo       TEXT,
    descricao    TEXT,
    quantidade   NUMERIC(12,3) NOT NULL DEFAULT 1,
    valor_unit   NUMERIC(12,2) DEFAULT 0,
    origem       TEXT DEFAULT 'NLAG',
    baixado      BOOLEAN NOT NULL DEFAULT FALSE,
    usuario_id   INTEGER REFERENCES usuarios(id),
    criado_em    TIMESTAMPTZ DEFAULT NOW()
);

-- ═══════════ PREVENTIVAS ═══════════
CREATE TABLE IF NOT EXISTS planos_preventiva (
    id             SERIAL PRIMARY KEY,
    equipamento_id INTEGER REFERENCES equipamentos(id) ON DELETE CASCADE,
    nome           TEXT NOT NULL,
    codigo_doc     TEXT,
    responsavel_id INTEGER REFERENCES usuarios(id),
    interna        BOOLEAN NOT NULL DEFAULT TRUE,
    empresa        TEXT,
    ativo          BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS checklist_itens (
    id            SERIAL PRIMARY KEY,
    plano_id      INTEGER REFERENCES planos_preventiva(id) ON DELETE CASCADE,
    ordem         INTEGER NOT NULL DEFAULT 0,
    numero        TEXT,
    descricao     TEXT NOT NULL,
    periodicidade TEXT NOT NULL DEFAULT 'MEN',
    tipo_resposta TEXT NOT NULL DEFAULT 'ok_nok',
    ativo         BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_cl_plano ON checklist_itens(plano_id);

CREATE TABLE IF NOT EXISTS plano_materiais (
    id         SERIAL PRIMARY KEY,
    plano_id   INTEGER REFERENCES planos_preventiva(id) ON DELETE CASCADE,
    codigo     TEXT NOT NULL,
    descricao  TEXT,
    umb        TEXT DEFAULT 'UNI',
    qt_sem     NUMERIC(12,3) DEFAULT 0,
    qt_men     NUMERIC(12,3) DEFAULT 0,
    qt_bim     NUMERIC(12,3) DEFAULT 0,
    qt_tri     NUMERIC(12,3) DEFAULT 0,
    qt_qua     NUMERIC(12,3) DEFAULT 0,
    qt_ses     NUMERIC(12,3) DEFAULT 0,
    qt_anu     NUMERIC(12,3) DEFAULT 0
);

-- Grade 52 semanas: o que está previsto
CREATE TABLE IF NOT EXISTS programacao (
    id             SERIAL PRIMARY KEY,
    plano_id       INTEGER REFERENCES planos_preventiva(id) ON DELETE CASCADE,
    equipamento_id INTEGER REFERENCES equipamentos(id) ON DELETE CASCADE,
    ano            INTEGER NOT NULL,
    semana         INTEGER NOT NULL,
    periodicidade  TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'previsto',
    om_id          INTEGER,
    UNIQUE (plano_id, ano, semana, periodicidade)
);
CREATE INDEX IF NOT EXISTS idx_prog_ano ON programacao(ano, semana);

CREATE TABLE IF NOT EXISTS ordens_manutencao (
    id              SERIAL PRIMARY KEY,
    numero          INTEGER UNIQUE,
    plano_id        INTEGER REFERENCES planos_preventiva(id),
    programacao_id  INTEGER REFERENCES programacao(id),
    equipamento_id  INTEGER REFERENCES equipamentos(id),
    ano             INTEGER,
    semana          INTEGER,
    periodicidade   TEXT,
    data_prevista   DATE,
    data_inicio     TIMESTAMPTZ,
    data_fim        TIMESTAMPTZ,
    manutentor1_id  INTEGER REFERENCES usuarios(id),
    manutentor2_id  INTEGER REFERENCES usuarios(id),
    terceirizado    BOOLEAN NOT NULL DEFAULT FALSE,
    empresa         TEXT,
    horimetro       NUMERIC(12,2),
    observacoes     TEXT,
    status          TEXT NOT NULL DEFAULT 'aberta',
    visto_lider_id  INTEGER REFERENCES usuarios(id),
    visto_lider_em  TIMESTAMPTZ,
    tempo_total_seg INTEGER NOT NULL DEFAULT 0,
    no_prazo        BOOLEAN,
    criado_em       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_om_status ON ordens_manutencao(status);

CREATE TABLE IF NOT EXISTS om_respostas (
    id         SERIAL PRIMARY KEY,
    om_id      INTEGER REFERENCES ordens_manutencao(id) ON DELETE CASCADE,
    item_id    INTEGER REFERENCES checklist_itens(id),
    resposta   TEXT,
    observacao TEXT,
    os_gerada  INTEGER REFERENCES ordens_servico(id)
);

CREATE TABLE IF NOT EXISTS om_anexos (
    id        SERIAL PRIMARY KEY,
    om_id     INTEGER REFERENCES ordens_manutencao(id) ON DELETE CASCADE,
    item_id   INTEGER,
    nome      TEXT NOT NULL,
    mime      TEXT,
    tipo      TEXT DEFAULT 'foto',
    dados     BYTEA,
    criado_em TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS reprogramacoes (
    id            SERIAL PRIMARY KEY,
    plano_id      INTEGER REFERENCES planos_preventiva(id),
    tipo          TEXT NOT NULL DEFAULT 'Reprogramação',
    ano           INTEGER,
    de_semana     INTEGER,
    para_semana   INTEGER,
    motivo        TEXT,
    usuario_id    INTEGER REFERENCES usuarios(id),
    criado_em     TIMESTAMPTZ DEFAULT NOW()
);

-- ═══════════ RONDAS DIÁRIAS DE INSPEÇÃO ═══════════
CREATE TABLE IF NOT EXISTS rondas (
    id     SERIAL PRIMARY KEY,
    nome   TEXT NOT NULL,
    turno  TEXT,
    ativo  BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS ronda_pontos (
    id             SERIAL PRIMARY KEY,
    ronda_id       INTEGER REFERENCES rondas(id) ON DELETE CASCADE,
    ordem          INTEGER DEFAULT 0,
    descricao      TEXT NOT NULL,
    equipamento_id INTEGER REFERENCES equipamentos(id),
    ativo          BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS ronda_execucoes (
    id          SERIAL PRIMARY KEY,
    ronda_id    INTEGER REFERENCES rondas(id),
    usuario_id  INTEGER REFERENCES usuarios(id),
    data        DATE NOT NULL,
    status      TEXT NOT NULL DEFAULT 'em_andamento',
    observacoes TEXT,
    criado_em   TIMESTAMPTZ DEFAULT NOW(),
    concluido_em TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS ronda_respostas (
    id           SERIAL PRIMARY KEY,
    execucao_id  INTEGER REFERENCES ronda_execucoes(id) ON DELETE CASCADE,
    ponto_id     INTEGER REFERENCES ronda_pontos(id),
    resposta     TEXT,
    observacao   TEXT,
    foto         BYTEA,
    os_gerada    INTEGER REFERENCES ordens_servico(id)
);

-- ═══════════ MATERIAIS (NLAG + HIBE/ERSA) ═══════════
CREATE TABLE IF NOT EXISTS materiais (
    id            SERIAL PRIMARY KEY,
    codigo        TEXT UNIQUE NOT NULL,
    descricao     TEXT NOT NULL,
    unidade       TEXT NOT NULL DEFAULT 'UNI',
    tipo          TEXT NOT NULL DEFAULT 'NLAG',
    aplicacao     TEXT,
    critico       BOOLEAN NOT NULL DEFAULT FALSE,
    estoque_min   NUMERIC(12,3) DEFAULT 0,
    estoque_max   NUMERIC(12,3) DEFAULT 0,
    saldo_sap     NUMERIC(12,3) DEFAULT 0,
    valor_unit    NUMERIC(12,2) DEFAULT 0,
    localizacao   TEXT,
    imagem        BYTEA,
    ativo         BOOLEAN NOT NULL DEFAULT TRUE,
    atualizado_em TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mat_tipo ON materiais(tipo);

CREATE TABLE IF NOT EXISTS movimentacoes (
    id         SERIAL PRIMARY KEY,
    codigo     TEXT NOT NULL,
    tipo       TEXT NOT NULL,
    quantidade NUMERIC(12,3) NOT NULL,
    data_hora  TIMESTAMPTZ DEFAULT NOW(),
    usuario    TEXT,
    observacao TEXT,
    os_id      INTEGER,
    om_id      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_mov_cod ON movimentacoes(codigo);
CREATE INDEX IF NOT EXISTS idx_mov_dt  ON movimentacoes(data_hora);

CREATE TABLE IF NOT EXISTS solicitacoes_material (
    id              SERIAL PRIMARY KEY,
    numero          INTEGER UNIQUE,
    solicitante_id  INTEGER REFERENCES usuarios(id),
    codigo          TEXT,
    descricao       TEXT NOT NULL,
    link            TEXT,
    tipo            TEXT NOT NULL DEFAULT 'Estoque',
    quantidade      NUMERIC(12,3) NOT NULL DEFAULT 1,
    centro_custo_id INTEGER REFERENCES centros_custo(id),
    observacoes     TEXT,
    os_id           INTEGER REFERENCES ordens_servico(id),
    om_id           INTEGER REFERENCES ordens_manutencao(id),
    num_ficha       TEXT,
    id_4mdg         TEXT,
    num_pr          TEXT,
    codigo_final    TEXT,
    tipo_material   TEXT,
    dt_solicitacao  DATE,
    dt_cadastro     DATE,
    dt_chegada      DATE,
    situacao        TEXT NOT NULL DEFAULT 'Solicitado',
    criado_em       TIMESTAMPTZ DEFAULT NOW(),
    atualizado_em   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sm_sit ON solicitacoes_material(situacao);

CREATE TABLE IF NOT EXISTS solicitacao_historico (
    id             SERIAL PRIMARY KEY,
    solicitacao_id INTEGER REFERENCES solicitacoes_material(id) ON DELETE CASCADE,
    usuario_id     INTEGER REFERENCES usuarios(id),
    situacao       TEXT,
    comentario     TEXT,
    criado_em      TIMESTAMPTZ DEFAULT NOW()
);

-- ═══════════ TERCEIROS / PERMISSÕES DE TRABALHO ═══════════
CREATE TABLE IF NOT EXISTS manutencoes_terceiros (
    id             SERIAL PRIMARY KEY,
    equipamento_id INTEGER REFERENCES equipamentos(id),
    manutentor_id  INTEGER REFERENCES usuarios(id),
    empresa        TEXT NOT NULL,
    tipo_servico   TEXT,
    descricao      TEXT,
    data_envio     DATE,
    data_retorno   DATE,
    valor          NUMERIC(12,2) DEFAULT 0,
    recebido_por   TEXT,
    criado_em      TIMESTAMPTZ DEFAULT NOW()
);

-- ═══════════ NOTIFICAÇÕES ═══════════
CREATE TABLE IF NOT EXISTS notificacoes (
    id         SERIAL PRIMARY KEY,
    usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
    titulo     TEXT NOT NULL,
    mensagem   TEXT,
    link       TEXT,
    lida       BOOLEAN NOT NULL DEFAULT FALSE,
    criado_em  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notificacoes(usuario_id, lida);

-- ═══════════ AUDITORIA ═══════════
CREATE TABLE IF NOT EXISTS log_auditoria (
    id         SERIAL PRIMARY KEY,
    usuario_id INTEGER,
    usuario    TEXT,
    acao       TEXT,
    entidade   TEXT,
    entidade_id INTEGER,
    detalhe    TEXT,
    criado_em  TIMESTAMPTZ DEFAULT NOW()
);

-- ═══════════ PARÂMETROS DO SISTEMA ═══════════
CREATE TABLE IF NOT EXISTS parametros (
    chave TEXT PRIMARY KEY,
    valor TEXT
);
"""


def init_db():
    """Cria/atualiza o schema. Idempotente — pode rodar em todo deploy."""
    with conexao(commit=True) as cur:
        cur.execute(SCHEMA)
    print("✅ Schema verificado/criado.", flush=True)
    _seed_inicial()


def _seed_inicial():
    """Popula cadastros essenciais na primeira execução."""
    # ── Níveis de criticidade (padrão de 5 níveis usado em PCM) ──
    if scalar("SELECT COUNT(*) AS n FROM criticidades") == 0:
        for cod, nome, ordem, cor, resp, concl, desc in NIVEIS_CRITICIDADE:
            executar("""INSERT INTO criticidades
                        (codigo, nome, ordem, cor, sla_resposta_h, sla_conclusao_h, descricao)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (codigo) DO NOTHING""",
                     (cod, nome, ordem, cor, resp, concl, desc))
        print("[seed] 5 níveis de criticidade cadastrados.", flush=True)

    # ── Admin inicial ──
    total = scalar("SELECT COUNT(*) AS n FROM usuarios")
    if total == 0:
        usuario = os.environ.get("APP_USUARIO", "admin").strip().lower()
        senha = os.environ.get("APP_SENHA", "decio@2026")
        executar(
            """INSERT INTO usuarios (usuario, senha_hash, nome, perfil, ativo)
               VALUES (%s,%s,%s,'admin',TRUE) ON CONFLICT (usuario) DO NOTHING""",
            (usuario, generate_password_hash(senha), "Administrador"),
        )
        print(f"[seed] admin criado: {usuario}", flush=True)

    # ── Estabelecimentos ──
    if scalar("SELECT COUNT(*) AS n FROM estabelecimentos") == 0:
        for nome, cod in [("Matriz", "2101"), ("Filial", "2102")]:
            executar(
                "INSERT INTO estabelecimentos (nome, codigo) VALUES (%s,%s) "
                "ON CONFLICT (nome) DO NOTHING", (nome, cod))

    # ── Defeitos e causas ──
    if scalar("SELECT COUNT(*) AS n FROM defeitos") == 0:
        for d in ["Elétrico", "Mecânico", "Hidráulico", "Pneumático",
                  "Eletrônico/CNC", "Civil/Predial", "Lubrificação", "Outros"]:
            executar("INSERT INTO defeitos (nome) VALUES (%s) ON CONFLICT DO NOTHING", (d,))
    if scalar("SELECT COUNT(*) AS n FROM causas") == 0:
        for c in ["Desgaste natural", "Falta de lubrificação", "Mau uso / operação",
                  "Falha de componente", "Fim de vida útil", "Sobrecarga",
                  "Falta de manutenção preventiva", "Sujeira / contaminação",
                  "Vibração / desalinhamento", "Oscilação de energia",
                  "Erro de montagem", "Causa externa", "Não identificada"]:
            executar("INSERT INTO causas (nome) VALUES (%s) ON CONFLICT DO NOTHING", (c,))

    # ── Centros de custo (extraídos das planilhas atuais) ──
    if scalar("SELECT COUNT(*) AS n FROM centros_custo") == 0:
        for cod, nome in [
            ("210248001", "Manutenção"),
            ("210148502", "Corte Puncionadeira"),
            ("210148503", "Dobra"),
            ("210248511", "Banho"),
            ("210248512", "Pint. Monovia"),
            ("210248516", "Mnt Customizados"),
        ]:
            executar("INSERT INTO centros_custo (codigo, nome) VALUES (%s,%s) "
                     "ON CONFLICT (codigo) DO NOTHING", (cod, nome))

    # ── Centros de trabalho ──
    if scalar("SELECT COUNT(*) AS n FROM centros_trabalho") == 0:
        matriz = scalar("SELECT id FROM estabelecimentos WHERE nome='Matriz'", default=None)
        filial = scalar("SELECT id FROM estabelecimentos WHERE nome='Filial'", default=None)
        setores = [
            ("CORTE", "Corte", matriz), ("DOBRA", "Dobra", matriz),
            ("SOLDA", "Solda", matriz), ("PRENSA", "Prensa / Repuxo", matriz),
            ("USIN", "Usinagem", matriz), ("UTIL", "Utilidades", matriz),
            ("PRED", "Predial", matriz), ("PINT", "Pintura", filial),
            ("BANHO", "Banho / Tratamento", filial), ("ETE", "Estação de Tratamento", filial),
            ("EXPED", "Expedição / Logística", matriz), ("SERIG", "Serigrafia", matriz),
        ]
        for cod, nome, est in setores:
            executar("INSERT INTO centros_trabalho (codigo, nome, estabelecimento_id) "
                     "VALUES (%s,%s,%s) ON CONFLICT (codigo) DO NOTHING", (cod, nome, est))

    # ── Equipamentos (inventário real DC-014) ──
    if scalar("SELECT COUNT(*) AS n FROM equipamentos") == 0:
        _seed_equipamentos()

    # ── Rondas diárias ──
    if scalar("SELECT COUNT(*) AS n FROM rondas") == 0:
        rid = inserir("INSERT INTO rondas (nome, turno) VALUES "
                      "('Ronda Diária de Inspeção','1º Turno') RETURNING id")
        pontos = [
            "Abastecimento de água — caixas d'água matriz e filial",
            "Lubrificação da monovia",
            "Compressores — pressão, nível de óleo e temperatura",
            "Secador de ar — ponto de orvalho",
            "Purgadores da rede de ar comprimido",
            "Estação de Tratamento (ETE) — bombas e nível",
            "Nível de óleo hidráulico das prensas",
            "Gerador — nível de combustível e bateria",
            "Linha de gás — vazamentos e pressão",
            "Iluminação de emergência",
        ]
        for i, p in enumerate(pontos, 1):
            executar("INSERT INTO ronda_pontos (ronda_id, ordem, descricao) VALUES (%s,%s,%s)",
                     (rid, i, p))

    # ── Parâmetros ──
    for chave, valor in [
        ("empresa", "Décio Metalúrgica"),
        ("tolerancia_preventiva_dias", "7"),
        ("custo_hh_padrao", "45.00"),
        ("email_os_aberta", "1"),
        ("email_os_atribuida", "1"),
        ("email_os_concluida", "1"),
        ("email_os_aprovada", "1"),
        ("email_os_reprovada", "1"),
        ("email_material_solicitado", "1"),
        ("email_material_recebido", "1"),
        ("email_estoque_minimo", "0"),
        ("email_preventiva_semana", "0"),
    ]:
        executar("INSERT INTO parametros (chave, valor) VALUES (%s,%s) "
                 "ON CONFLICT (chave) DO NOTHING", (chave, valor))


# Inventário real extraído da planilha DC-014
EQUIPAMENTOS_SEED = [
    ("BE01", "Bebedouro Produção - Matriz", "C", "Predial", "Matriz", "PRED"),
    ("BE02", "Bebedouro Produção - Filial", "C", "Predial", "Filial", "PRED"),
    ("CA01", "Calandra", "B", "Industrial", "Matriz", "USIN"),
    ("CH01", "Chiller Solda Ponto", "B", "Industrial", "Matriz", "SOLDA"),
    ("CO02", "Compressor Atlas 15+", "A", "Industrial", "Matriz", "UTIL"),
    ("CO03", "Compressor Kaeser ASD 40S", "A", "Industrial", "Matriz", "UTIL"),
    ("CO04", "Compressor Kaeser BSD 50", "A", "Industrial", "Matriz", "UTIL"),
    ("CP01", "Cabine de pintura contínua", "A", "Industrial", "Filial", "PINT"),
    ("CX00", "Caixas d'água matriz e filial", "B", "Predial", "Matriz", "UTIL"),
    ("DO01", "Dobradeira Xelexct100", "B", "Industrial", "Matriz", "DOBRA"),
    ("DO02", "Dobradeira PB45", "B", "Industrial", "Matriz", "DOBRA"),
    ("DO03", "Dobradeira DBR 25", "B", "Industrial", "Matriz", "DOBRA"),
    ("DO04", "Dobradeira Newton", "B", "Industrial", "Matriz", "DOBRA"),
    ("DO05", "Dobradeira Bystronic I Xpert80", "A", "Industrial", "Matriz", "DOBRA"),
    ("DO06", "Dobradeira Bystronic II Xpert80", "A", "Industrial", "Matriz", "DOBRA"),
    ("DO07", "Dobradeira Bystronic III Xpert80", "A", "Industrial", "Matriz", "DOBRA"),
    ("DP01", "Dobra Pneumática Barra (Azul)", "C", "Industrial", "Matriz", "DOBRA"),
    ("ES01", "Estabilizadores", "B", "Industrial", "Matriz", "UTIL"),
    ("ET01", "Estação de Tratamento", "A", "Industrial", "Filial", "ETE"),
    ("FB01", "Furadeira Joinville", "C", "Industrial", "Matriz", "USIN"),
    ("FB02", "Furadeira FSB 16 (1)", "C", "Industrial", "Matriz", "USIN"),
    ("FB03", "Furadeira FSB 16 (2)", "C", "Industrial", "Matriz", "USIN"),
    ("FB04", "Furadeira FSB 16 (3)", "C", "Industrial", "Matriz", "USIN"),
    ("FB05", "Furadeira Dauer", "C", "Industrial", "Matriz", "USIN"),
    ("FB06", "Furadeira Somar", "C", "Industrial", "Matriz", "USIN"),
    ("FF01", "Filtro Prensa do Fosfato", "A", "Industrial", "Filial", "BANHO"),
    ("GE01", "Gerador STEMAC", "A", "Industrial", "Matriz", "UTIL"),
    ("GE02", "Subestação", "A", "Industrial", "Matriz", "UTIL"),
    ("GI00", "Linha GNV Filial", "A", "Industrial", "Filial", "UTIL"),
    ("GI01", "Linha de gás Oxigênio e Nitrogênio", "A", "Industrial", "Matriz", "UTIL"),
    ("GU01", "Guilhotina Newton 3003", "A", "Industrial", "Matriz", "CORTE"),
    ("GU02", "Guilhotina Newton GMN 1203", "B", "Industrial", "Matriz", "CORTE"),
    ("LA01", "Laser Bystronic Bysprint 3000", "A", "Industrial", "Matriz", "CORTE"),
    ("LA02", "Laser CS3000 / Welle", "A", "Industrial", "Matriz", "CORTE"),
    ("PA00", "Transpaleteira 1", "C", "Industrial", "Matriz", "EXPED"),
    ("PI01", "Motor Diesel (Incêndio)", "A", "Industrial", "Matriz", "UTIL"),
    ("PI02", "Central de Alarme", "B", "Predial", "Matriz", "PRED"),
    ("PI03", "SPDA Matriz", "B", "Predial", "Matriz", "PRED"),
    ("PO01", "Ponte rolante banho", "A", "Industrial", "Filial", "BANHO"),
    ("PR01", "Prensa PE/V.15C - 15 TON (1)", "B", "Industrial", "Matriz", "PRENSA"),
    ("PR02", "Prensa PE/V.15C - 15 TON (2)", "B", "Industrial", "Matriz", "PRENSA"),
    ("PR03", "Prensa MRF 15 TON", "B", "Industrial", "Matriz", "PRENSA"),
    ("PR04", "Prensa MRF 40 TON", "B", "Industrial", "Matriz", "PRENSA"),
    ("PR05", "Prensa Ricetti - 80 TON", "A", "Industrial", "Matriz", "PRENSA"),
    ("PU01", "Puncionadeira PGA4", "A", "Industrial", "Matriz", "CORTE"),
    ("PU02", "Puncionadeira Cupra 30", "A", "Industrial", "Matriz", "CORTE"),
    ("PU03", "Puncionadeira Murata TS 48", "A", "Industrial", "Matriz", "CORTE"),
    ("PU04", "Puncionadeira Murata TC 44", "A", "Industrial", "Matriz", "CORTE"),
    ("RT01", "Retífica Sharp", "B", "Industrial", "Matriz", "USIN"),
    ("RT02", "Retífica SAIM E-4", "C", "Industrial", "Matriz", "USIN"),
    ("SC03", "Secador Fábrica - Matriz", "A", "Industrial", "Matriz", "UTIL"),
    ("SC04", "Secador Puma - Filial", "A", "Industrial", "Filial", "UTIL"),
    ("SE01", "Serra Cortesa", "B", "Industrial", "Matriz", "CORTE"),
    ("SE02", "Serra Fita Starret", "C", "Industrial", "Matriz", "CORTE"),
    ("SE03", "Serra Circular Bancada", "C", "Industrial", "Matriz", "CORTE"),
    ("SG01", "Setor de serigrafia", "C", "Industrial", "Matriz", "SERIG"),
    ("SI01", "Solda Pino HBS", "B", "Industrial", "Matriz", "SOLDA"),
    ("SI02", "Solda Pino Soyar", "B", "Industrial", "Matriz", "SOLDA"),
    ("SM01", "Máquina Solda MIG 01", "B", "Industrial", "Matriz", "SOLDA"),
    ("SM02", "Máquina Solda MIG 02", "B", "Industrial", "Matriz", "SOLDA"),
    ("SM03", "Máquina Solda MIG 03", "B", "Industrial", "Matriz", "SOLDA"),
    ("SM04", "Máquina Solda MIG 04", "B", "Industrial", "Matriz", "SOLDA"),
    ("SM05", "Máquina Solda MIG 05", "B", "Industrial", "Matriz", "SOLDA"),
    ("SP01", "Solda Ponto CPDN", "B", "Industrial", "Matriz", "SOLDA"),
    ("SP02", "Solda Ponto CPDN 75", "B", "Industrial", "Matriz", "SOLDA"),
    ("SP03", "Solda Ponto CPD", "B", "Industrial", "Matriz", "SOLDA"),
    ("SP04", "Solda Ponto 4", "B", "Industrial", "Matriz", "SOLDA"),
    ("SP05", "Solda Ponto 5", "B", "Industrial", "Matriz", "SOLDA"),
    ("ST01", "Máquina Solda TIG", "B", "Industrial", "Matriz", "SOLDA"),
    ("ST02", "Máquina Solda TIG Lincoln", "B", "Industrial", "Matriz", "SOLDA"),
    ("TA01", "Tanque estufa", "A", "Industrial", "Filial", "PINT"),
    ("TA02", "Tanques Banho", "A", "Industrial", "Filial", "BANHO"),
    ("TA05", "Tanque Desengraxante", "A", "Industrial", "Filial", "BANHO"),
    ("VP00", "Vasos de pressão - Matriz e Filial", "A", "Industrial", "Matriz", "UTIL"),
]


def _seed_equipamentos():
    for grupo, nome, crit, tipo, estab, setor in EQUIPAMENTOS_SEED:
        est_id = scalar("SELECT id FROM estabelecimentos WHERE nome=%s", (estab,), default=None)
        ct_id = scalar("SELECT id FROM centros_trabalho WHERE codigo=%s", (setor,), default=None)
        executar(
            """INSERT INTO equipamentos
               (grupo_prev, subcodigo, codigo, nome, criticidade, tipo,
                estabelecimento_id, centro_trabalho_id)
               VALUES (%s,'00',%s,%s,%s,%s,%s,%s)
               ON CONFLICT (codigo) DO NOTHING""",
            (grupo, f"{grupo}-00", nome, crit, tipo, est_id, ct_id))
    print(f"[seed] {len(EQUIPAMENTOS_SEED)} equipamentos cadastrados.", flush=True)


# ──────────────────────────────────────────────────────────────────
#  CRITICIDADE — níveis padrão e matriz de classificação
# ──────────────────────────────────────────────────────────────────
# (código, nome, ordem, cor, SLA resposta h, SLA conclusão h, descrição)
NIVEIS_CRITICIDADE = [
    ("A", "Crítico", 1, "#C0392B", 0.5, 4,
     "Parada da fábrica, risco à segurança das pessoas, impacto ambiental ou "
     "exigência legal. Sem alternativa de produção."),
    ("B", "Alto", 2, "#E8590C", 2, 24,
     "Para uma linha ou setor inteiro. Não há equipamento reserva e o reparo "
     "costuma ser demorado."),
    ("C", "Médio", 3, "#E08A00", 8, 72,
     "Reduz o ritmo de produção ou afeta a qualidade, mas há redundância, "
     "desvio de processo ou estoque intermediário."),
    ("D", "Baixo", 4, "#5B93C4", 24, 168,
     "Não interrompe a produção. Atendimento programado dentro da rotina."),
    ("E", "Monitorado", 5, "#7A8899", None, None,
     "Equipamento de apoio. Opera até a falha, sem plano de preventiva."),
]

# Critérios da matriz: (campo, rótulo, peso, [descrição das notas 0..4])
CRITERIOS_MATRIZ = [
    ("mtz_seguranca", "Segurança e meio ambiente", 30, [
        "Sem risco", "Risco leve, contornável", "Risco de afastamento",
        "Risco grave ou dano ambiental", "Risco de vida ou infração legal"]),
    ("mtz_producao", "Impacto na produção", 25, [
        "Nenhum", "Reduz o ritmo de um posto", "Para um posto de trabalho",
        "Para uma linha ou setor", "Para a fábrica inteira"]),
    ("mtz_qualidade", "Qualidade do produto", 15, [
        "Nenhum", "Variação sem refugo", "Retrabalho pontual",
        "Refugo recorrente", "Refugo total ou risco ao cliente"]),
    ("mtz_frequencia", "Frequência histórica de falha", 10, [
        "Nunca falhou", "Rara (anual)", "Ocasional (semestral)",
        "Frequente (mensal)", "Muito frequente (semanal)"]),
    ("mtz_reparo", "Dificuldade e tempo de reparo", 10, [
        "Minutos, equipe interna", "Poucas horas, equipe interna",
        "Um turno ou peça de estoque", "Dias, depende de compra",
        "Semanas, terceiro ou importação"]),
    ("mtz_redundancia", "Redundância / alternativa", 10, [
        "Reserva imediata disponível", "Reserva com troca demorada",
        "Alternativa parcial", "Alternativa precária", "Não existe alternativa"]),
]

PESO_TOTAL = sum(c[2] for c in CRITERIOS_MATRIZ)


def niveis_criticidade(ativos=True):
    """Níveis cadastrados, em ordem de prioridade."""
    filtro = "WHERE ativo=TRUE" if ativos else ""
    return query(f"SELECT * FROM criticidades {filtro} ORDER BY ordem") or []


def mapa_criticidade():
    """dict {codigo: {...}} para uso nos templates."""
    return {c["codigo"]: dict(c) for c in niveis_criticidade(ativos=False)}


def ordem_crit(coluna):
    """Trecho SQL que devolve a ordem de prioridade de uma coluna de criticidade."""
    return (f"COALESCE((SELECT cr.ordem FROM criticidades cr "
            f"WHERE cr.codigo = {coluna}), 99)")


def pontuar_matriz(notas):
    """
    notas: dict {campo: 0..4}. Devolve a pontuação de 0 a 100.
    Cada critério contribui com (nota/4) × peso.
    """
    total = 0.0
    for campo, _rot, peso, _desc in CRITERIOS_MATRIZ:
        try:
            n = float(notas.get(campo) or 0)
        except (TypeError, ValueError):
            n = 0.0
        n = max(0.0, min(n, 4.0))
        total += (n / 4.0) * peso
    return round(total / PESO_TOTAL * 100, 2)


def classificar(pontuacao):
    """
    Converte a pontuação (0..100) no código de criticidade, distribuindo a
    escala entre os níveis ativos. Adapta-se automaticamente a 3, 4 ou 5 níveis.
    """
    niveis = niveis_criticidade()
    if not niveis:
        return "C"
    n = len(niveis)
    faixa = 100.0 / n
    # niveis[0] é o mais crítico; a maior pontuação cai nele
    for i, nivel in enumerate(niveis):
        piso = 100.0 - (i + 1) * faixa
        if pontuacao >= piso - 1e-9:
            return nivel["codigo"]
    return niveis[-1]["codigo"]


def escalar_criticidade(codigo):
    """Sobe um nível de prioridade (usado quando a máquina está parada)."""
    niveis = niveis_criticidade()
    if not niveis:
        return codigo
    codigos = [n["codigo"] for n in niveis]
    if codigo not in codigos:
        return codigos[-1]
    i = codigos.index(codigo)
    return codigos[max(0, i - 1)]


def sla_criticidade(codigo):
    """(horas de resposta, horas de conclusão) do nível informado."""
    n = um("SELECT sla_resposta_h, sla_conclusao_h FROM criticidades WHERE codigo=%s",
           (codigo,))
    if not n:
        return None, None
    resp = float(n["sla_resposta_h"]) if n["sla_resposta_h"] is not None else None
    concl = float(n["sla_conclusao_h"]) if n["sla_conclusao_h"] is not None else None
    return resp, concl


# ──────────────────────────────────────────────────────────────────
#  Utilidades de negócio
# ──────────────────────────────────────────────────────────────────
def proximo_numero(tabela):
    """Gera número sequencial legível (OS-1, OS-2...) por tabela."""
    n = scalar(f"SELECT COALESCE(MAX(numero),0)+1 AS n FROM {tabela}", default=1)
    return int(n)


def saldo_material(codigo):
    return float(scalar(
        """SELECT COALESCE(SUM(CASE WHEN tipo='ENTRADA' THEN quantidade
                                    WHEN tipo='AJUSTE'  THEN quantidade
                                    ELSE -quantidade END),0) AS s
           FROM movimentacoes WHERE codigo=%s""", (codigo,), default=0))


def notificar(usuario_id, titulo, mensagem="", link=""):
    if not usuario_id:
        return
    executar("INSERT INTO notificacoes (usuario_id, titulo, mensagem, link) VALUES (%s,%s,%s,%s)",
             (usuario_id, titulo, mensagem, link))


def notificar_perfis(perfis, titulo, mensagem="", link=""):
    """Envia notificação a todos os usuários ativos dos perfis informados."""
    users = query("SELECT id FROM usuarios WHERE ativo=TRUE AND perfil = ANY(%s)", (list(perfis),))
    for u in users or []:
        notificar(u["id"], titulo, mensagem, link)


def registrar_log(usuario_id, usuario, acao, entidade=None, entidade_id=None, detalhe=None):
    try:
        executar("""INSERT INTO log_auditoria (usuario_id, usuario, acao, entidade, entidade_id, detalhe)
                    VALUES (%s,%s,%s,%s,%s,%s)""",
                 (usuario_id, usuario, acao, entidade, entidade_id, detalhe))
    except Exception:
        pass


if __name__ == "__main__":
    init_db()
