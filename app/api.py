"""Fase 3 — API FastAPI. Serve a lista rankeada pro dashboard (Vercel) e o custo/dia.

CORS configurável via CORS_ORIGINS (setar a URL do Vercel em prod). Tabelas criadas
no startup (idempotente). LGPD: comentários de intenção sem nickname/uid.
"""
from __future__ import annotations

import os
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from . import config
from .db import SessionLocal, init_db
from .models import CostLog, Post, Produto, Score


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # cria tabelas (idempotente) — Railway sobe sem passo de migração manual
    if os.getenv("AUTO_SEED", "").lower() in ("1", "true", "yes"):
        from .seed_keywords import seed
        seed()
    yield


app = FastAPI(title="TikTok Miner API", version="1.0", lifespan=lifespan)

_origins = ["*"] if config.CORS_ORIGINS.strip() == "*" else [
    o.strip() for o in config.CORS_ORIGINS.split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _serialize(pr: Produto, post: Post, sc: Score) -> dict:
    return {
        "post_id": post.id,
        "mercado": pr.mercado,
        "sinal": pr.sinal,
        "score": pr.score_final,
        "produto": pr.produto or post.descricao[:120],
        "preco": pr.preco,
        "nicho": pr.nicho,
        "url": post.url,
        "engajamento": {"curtidas": post.digg_count, "comentarios": post.comment_count},
        "score_componentes": {
            "comment_score": sc.comment_score,
            "caption_score": sc.caption_score,
            "n_comentarios_intencao": sc.n_comentarios_intencao,
            "densidade_intencao": sc.densidade_intencao,
        },
        # LGPD: só o texto do comentário, sem nick/uid.
        "comentarios_intencao": [c.texto for c in post.comentarios if c.is_intent][:8],
    }


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/produtos")
def listar_produtos(
    db=Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    min_score: float = Query(0.0, ge=0, le=100),
    mercado: Optional[str] = None,
    sinal: Optional[str] = None,
    preco_max: Optional[float] = None,
):
    q = (
        select(Produto, Post, Score)
        .join(Post, Produto.post_id == Post.id)
        .join(Score, Score.post_id == Post.id)
        .where(Produto.score_final >= min_score)
    )
    if mercado:
        q = q.where(Produto.mercado == mercado)
    if sinal:
        q = q.where(Produto.sinal == sinal)
    q = q.order_by(Produto.score_final.desc()).limit(limit)
    itens = [_serialize(pr, post, sc) for pr, post, sc in db.execute(q).all()]
    # filtro de preço aplicado em Python (preço é string livre extraída)
    if preco_max is not None:
        def _num(p):
            import re
            m = re.search(r"\d+[.,]?\d*", p or "")
            return float(m.group(0).replace(",", ".")) if m else None
        itens = [i for i in itens if (_num(i["preco"]) or 0) <= preco_max]
    return {"total": len(itens), "produtos": itens}


@app.get("/produtos/{post_id}")
def detalhe_produto(post_id: str, db=Depends(get_db)):
    row = db.execute(
        select(Produto, Post, Score)
        .join(Post, Produto.post_id == Post.id)
        .join(Score, Score.post_id == Post.id)
        .where(Post.id == post_id)
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="produto não encontrado")
    return _serialize(*row)


@app.get("/custo/dia")
def custo_dia(db=Depends(get_db)):
    """Custo por dia: requests do ScrapeCreators (≈1 crédito/req) + tokens Haiku."""
    rows = db.execute(select(CostLog)).scalars().all()
    per_day: dict = defaultdict(lambda: {"scrape_requests": 0, "haiku_usd": 0.0})
    for r in rows:
        dia = r.ts.date().isoformat() if r.ts else "sem_data"
        if r.endpoint == "haiku_batch":
            per_day[dia]["haiku_usd"] += float((r.params or {}).get("usd", 0.0))
        else:
            per_day[dia]["scrape_requests"] += 1
    out = []
    for dia, d in sorted(per_day.items()):
        scrape_usd = d["scrape_requests"] * config.CREDIT_USD
        out.append({
            "dia": dia,
            "scrape_requests": d["scrape_requests"],
            "scrape_usd": round(scrape_usd, 4),
            "haiku_usd": round(d["haiku_usd"], 6),
            "total_usd": round(scrape_usd + d["haiku_usd"], 4),
        })
    return {"credit_usd": config.CREDIT_USD, "dias": out}
