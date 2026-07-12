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

    id = Column(String(40), primary_key=True)  # aweme_id
    url = Column(Text, nullable=False)
    cover_url = Column(Text, nullable=True)  # capa do TikTok p/ preview (pode expirar)
    descricao = Column(Text, default="")
    content_type = Column(String(32), default="")
    create_time = Column(BigInteger, nullable=True)
    region = Column(String(8), default="")
    author_id = Column(String(120), default="")
    author_nick = Column(String(120), default="")
    market = Column(String(60), default="")
    digg_count = Column(Integer, default=0)
    comment_count = Column(Integer, default=0)
    play_count = Column(Integer, default=0)
    share_count = Column(Integer, default=0)
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
    produto = Column(String(200), nullable=True)
    preco = Column(String(40), nullable=True)
    nicho = Column(String(80), nullable=True)
    mercado = Column(String(60), default="")
    sinal = Column(String(32), default="")
    score_final = Column(Float, default=0.0)
    created_at = Column(DateTime, server_default=func.now())


class Run(Base):
    """Job de varredura disparado pelo dashboard (assíncrono). Status + resumo."""

    __tablename__ = "runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    status = Column(String(20), default="queued")  # queued|running|done|error|interrupted
    mode = Column(String(10), default="live")       # live|dry-run
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
