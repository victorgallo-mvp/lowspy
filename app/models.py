from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from .db import Base


class Keyword(Base):
    """O DB de palavras: {termo, mercado, sinal}. A varredura lê os ativos."""

    __tablename__ = "keywords"
    __table_args__ = (UniqueConstraint("termo", "tipo", name="uq_keyword_termo_tipo"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    termo = Column(String(120), nullable=False)
    tipo = Column(String(16), nullable=False, default="hashtag")  # hashtag | top
    mercado = Column(String(60), nullable=False)
    sinal_esperado = Column(String(32), nullable=False, default="demanda")  # demanda | vendedor
    ativo = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.now())


class Post(Base):
    """Idempotente por id (aweme_id). Reprocessar faz upsert do metadado."""

    __tablename__ = "posts"

    id = Column(String(40), primary_key=True)  # aweme_id (tiktok) | ad_archive_id (meta)
    fonte = Column(String(10), nullable=False, default="tiktok")  # tiktok | meta
    idioma = Column(String(8), nullable=False, default="pt")  # pt | es_en
    url = Column(Text, nullable=False)
    cover_url = Column(Text, nullable=True)  # capa do TikTok p/ preview (pode expirar)
    descricao = Column(Text, default="")
    content_type = Column(String(32), default="")
    create_time = Column(BigInteger, nullable=True)
    region = Column(String(8), default="")
    author_id = Column(String(120), default="")   # tiktok: author_id  | meta: page_id
    author_nick = Column(String(120), default="")  # tiktok: nickname   | meta: page_name
    market = Column(String(60), default="")
    termo_origem = Column(String(120), default="")  # palavra-chave exata que achou o post
    digg_count = Column(Integer, default=0)
    comment_count = Column(Integer, default=0)
    play_count = Column(Integer, default=0)
    share_count = Column(Integer, default=0)
    total_active_time = Column(Integer, nullable=True)  # meta: dias de veiculação
    collation_count = Column(Integer, nullable=True)     # meta: nº de variações desse anúncio
    is_active = Column(Boolean, nullable=True)           # meta: anúncio ainda ativo?
    anunciante_total_ads = Column(Integer, nullable=True)      # meta: total de anúncios ativos da página
    anunciante_tem_mais_ads = Column(Boolean, nullable=True)   # meta: contagem é piso, não exata
    first_seen = Column(DateTime, server_default=func.now())
    last_seen = Column(DateTime, server_default=func.now(), onupdate=func.now())
    processed_at = Column(DateTime, nullable=True)  # N1 concluído (dedup de fetch)

    comentarios = relationship("Comment", back_populates="post", cascade="all, delete-orphan")
    score = relationship("Score", back_populates="post", uselist=False, cascade="all, delete-orphan")


class Comment(Base):
    """Dedup por cid. LGPD: guardamos texto + autor mínimo; dashboard mascara nick."""

    __tablename__ = "comentarios"

    # PK composta: um comentário pertence a um post (cid é único no TikTok, mas
    # compor com post_id é semântico e robusto).
    cid = Column(String(40), primary_key=True)
    post_id = Column(String(40), ForeignKey("posts.id"), primary_key=True, index=True)
    texto = Column(Text, default="")
    digg_count = Column(Integer, default=0)
    reply_total = Column(Integer, default=0)
    create_time = Column(BigInteger, nullable=True)
    is_intent = Column(Boolean, default=False)  # bateu no regex de intenção

    post = relationship("Post", back_populates="comentarios")


class Score(Base):
    """Componentes salvos separados pra auditoria (peso, não match cru)."""

    __tablename__ = "scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(String(40), ForeignKey("posts.id"), nullable=False, unique=True, index=True)
    n_comentarios_intencao = Column(Integer, default=0)
    n_comentarios_lidos = Column(Integer, default=0)
    densidade_intencao = Column(Float, default=0.0)
    caption_score = Column(Float, default=0.0)
    comment_score = Column(Float, default=0.0)
    engaj_score = Column(Float, default=0.0)  # engajamento normalizado (Bloco 3)
    dias_ativos = Column(Integer, default=0)  # meta: tempo de veiculação (sinal de demanda)
    score_final = Column(Float, default=0.0)  # normalizado 0-100
    sinal = Column(String(32), default="")  # demanda_confirmada | vendedor_off_platform
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    post = relationship("Post", back_populates="score")


class Produto(Base):
    """Sobrevivente exibível. produto/nicho preenchidos pelo Nível 2 (Haiku)."""

    __tablename__ = "produtos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(String(40), ForeignKey("posts.id"), nullable=False, unique=True, index=True)
    run_id = Column(Integer, ForeignKey("runs.id"), nullable=True, index=True)  # varredura que achou
    produto = Column(String(200), nullable=True)
    preco = Column(String(40), nullable=True)
    nicho = Column(String(80), nullable=True)
    mercado = Column(String(60), default="")
    sinal = Column(String(32), default="")
    score_final = Column(Float, default=0.0)
    novo = Column(Boolean, default=False)  # 1ª vez que aparece (não visto em run anterior)
    created_at = Column(DateTime, server_default=func.now())


class Run(Base):
    """Job de varredura disparado pelo dashboard (assíncrono). Status + resumo."""

    __tablename__ = "runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    status = Column(String(20), default="queued")  # queued|running|done|error|interrupted
    mode = Column(String(10), default="live")       # live|dry-run
    fonte = Column(String(10), default="tiktok")     # tiktok|meta
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    summary = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class CostLog(Base):
    """Log de custo por chamada (API). Endpoint /custo/dia lê daqui na Fase 3."""

    __tablename__ = "cost_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    endpoint = Column(String(60), nullable=False)
    params = Column(JSON, default=dict)
    credits_remaining = Column(Integer, nullable=True)
    credits_spent = Column(Integer, nullable=True)  # delta calculado no run
    ts = Column(DateTime, server_default=func.now())


class TermoSugerido(Base):
    """Termo de busca sugerido manualmente pelo operador, pra avaliar depois — NÃO
    entra na varredura sozinho (curadoria manual, igual o link da engenharia reversa;
    decide-se separadamente quando/se promover pro yaml de discovery/meta_ads)."""

    __tablename__ = "termos_sugeridos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    termo = Column(String(200), nullable=False)
    fonte = Column(String(10), nullable=False, default="geral")  # tiktok | meta | geral
    nota = Column(Text, default="")  # por que o operador acha que vale testar
    created_at = Column(DateTime, server_default=func.now())


class ReversoHistorico(Base):
    """Histórico de links analisados na engenharia reversa — cada consulta ao
    /reverso/tiktok fica salva aqui, pra não perder a análise depois que a página
    fecha."""

    __tablename__ = "reverso_historico"

    id = Column(Integer, primary_key=True, autoincrement=True)
    url = Column(Text, nullable=False)
    legenda = Column(Text, default="")
    hashtags_encontradas = Column(JSON, default=list)
    preco_detectado = Column(String(40), nullable=True)
    autor = Column(String(120), default="")
    views = Column(Integer, default=0)
    curtidas = Column(Integer, default=0)
    comentarios = Column(Integer, default=0)
    comentarios_lidos = Column(Integer, default=0)
    n_comentarios_intencao = Column(Integer, default=0)
    comentarios_intencao = Column(JSON, default=list)  # LGPD: só texto, sem nick/uid
    sinal_legenda = Column(JSON, default=list)
    creditos_gastos = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
