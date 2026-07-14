"""Fase 3 — API FastAPI. Serve a lista rankeada pro dashboard (Vercel) e o custo/dia.

CORS configurável via CORS_ORIGINS (setar a URL do Vercel em prod). Tabelas criadas
no startup (idempotente). LGPD: comentários de intenção sem nickname/uid.
"""
from __future__ import annotations

import os
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from . import config, jobs
from .db import SessionLocal, init_db
from .models import CostLog, Post, Produto, Run, Score


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # cria tabelas (idempotente) — Railway sobe sem passo de migração manual
    if os.getenv("AUTO_SEED", "").lower() in ("1", "true", "yes"):
        from .seed_keywords import seed
        seed()
    jobs.reset_stale()  # runs presos por restart → interrompidos
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
        "cover_url": post.cover_url,
        "engajamento": {
            "curtidas": post.digg_count,
            "comentarios": post.comment_count,
            "views": post.play_count,
        },
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


def _latest_run_id(db) -> Optional[int]:
    return db.execute(select(func.max(Produto.run_id))).scalar()


@app.get("/produtos")
def listar_produtos(
    db=Depends(get_db),
    limit: int = Query(60, ge=1, le=200),
    min_score: float = Query(0.0, ge=0, le=100),
    min_views: int = Query(0, ge=0),
    min_likes: int = Query(0, ge=0),
    min_comments: int = Query(0, ge=0),
    preco_max: Optional[float] = None,
    run: str = Query("latest", description="latest | all | <run_id>"),
):
    q = (
        select(Produto, Post, Score)
        .join(Post, Produto.post_id == Post.id)
        .join(Score, Score.post_id == Post.id)
        .where(Produto.score_final >= min_score)
    )
    # filtros de engajamento (a régua é do operador)
    if min_views:
        q = q.where(Post.play_count >= min_views)
    if min_likes:
        q = q.where(Post.digg_count >= min_likes)
    if min_comments:
        q = q.where(Post.comment_count >= min_comments)
    # filtro por varredura: "latest" (padrão) mostra só os resultados da última busca
    if run != "all":
        rid: Optional[int] = None
        if run and run != "latest":
            try:
                rid = int(run)
            except ValueError:
                rid = None
        if rid is None:
            rid = _latest_run_id(db)
        if rid is not None:
            q = q.where(Produto.run_id == rid)
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


# --------------------------------------------------------------------------- #
# Varredura disparada pelo dashboard (assíncrona)
# --------------------------------------------------------------------------- #
def _require_token(x_api_token: Optional[str]) -> None:
    """Protege o disparo pago. Sem TRIGGER_TOKEN setado (dev) = liberado."""
    if config.TRIGGER_TOKEN and x_api_token != config.TRIGGER_TOKEN:
        raise HTTPException(status_code=401, detail="token inválido")


def _run_dict(run: Run) -> dict:
    return {
        "id": run.id,
        "status": run.status,
        "mode": run.mode,
        "summary": run.summary,
        "error": run.error,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@app.post("/varredura")
def disparar_varredura(
    db=Depends(get_db),
    dry: bool = Query(False, description="true = dry-run (fixtures, gasto zero)"),
    x_api_token: Optional[str] = Header(None),
):
    _require_token(x_api_token)
    run_id = jobs.start_sweep(db, live=not dry)
    if run_id is None:
        raise HTTPException(status_code=409, detail="já existe uma varredura em andamento")
    return {"run_id": run_id, "status": "queued", "mode": "dry-run" if dry else "live"}


@app.get("/varredura/status")
def varredura_status(db=Depends(get_db)):
    run = db.execute(select(Run).order_by(Run.id.desc())).scalars().first()
    return {"running": jobs.is_running(), "ultima": _run_dict(run) if run else None}


@app.get("/varredura/{run_id}")
def varredura_run(run_id: int, db=Depends(get_db)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run não encontrado")
    return _run_dict(run)


@app.get("/varreduras")
def listar_varreduras(db=Depends(get_db), limit: int = Query(20, ge=1, le=100)):
    """Varreduras recentes + nº de produtos de cada — alimenta o seletor do dash."""
    runs = db.execute(select(Run).order_by(Run.id.desc()).limit(limit)).scalars().all()
    counts = dict(
        db.execute(select(Produto.run_id, func.count()).group_by(Produto.run_id)).all()
    )
    return {
        "varreduras": [
            {
                "id": r.id,
                "status": r.status,
                "mode": r.mode,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "n_produtos": counts.get(r.id, 0),
            }
            for r in runs
        ]
    }
