"""Fase 3 — API FastAPI. Serve a lista rankeada pro dashboard (Vercel) e o custo/dia.

CORS configurável via CORS_ORIGINS (setar a URL do Vercel em prod). Tabelas criadas
no startup (idempotente). LGPD: comentários de intenção sem nickname/uid.
"""
from __future__ import annotations

import os
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from . import config, jobs
from .db import SessionLocal, init_db
from .models import CostLog, Post, Produto, Run, Score, TermoSugerido
from .pipeline import DBCost
from .scrapecreators import DryRunClient, LiveClient
from .signals import caption_seller_score, extract_hashtags, extract_price, intent_score


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
    base = {
        "post_id": post.id,
        "fonte": post.fonte,
        "idioma": post.idioma,
        "mercado": pr.mercado,
        "termo_origem": post.termo_origem,
        "sinal": pr.sinal,
        "novo": bool(pr.novo),
        "score": pr.score_final,
        "produto": pr.produto or post.descricao[:120],
        "preco": pr.preco,
        "nicho": pr.nicho,
        "url": post.url,
        "cover_url": post.cover_url,
    }
    if post.fonte == "meta":
        base["meta"] = {
            "pagina": post.author_nick,
            "dias_ativos": post.total_active_time,
            "variacoes_ativas": post.collation_count,
            "ativo": bool(post.is_active),
            "total_anuncios_anunciante": post.anunciante_total_ads,
            "tem_mais_anuncios": bool(post.anunciante_tem_mais_ads),
        }
        base["score_componentes"] = {
            "caption_score": sc.caption_score,
            "dias_ativos": sc.dias_ativos,
        }
        base["comentarios_intencao"] = []  # Meta Ad Library não tem comentário
    else:
        base["engajamento"] = {
            "curtidas": post.digg_count,
            "comentarios": post.comment_count,
            "views": post.play_count,
        }
        base["score_componentes"] = {
            "comment_score": sc.comment_score,
            "caption_score": sc.caption_score,
            "engaj_score": sc.engaj_score,
            "n_comentarios_intencao": sc.n_comentarios_intencao,
            "densidade_intencao": sc.densidade_intencao,
        }
        # LGPD: só o texto do comentário, sem nick/uid.
        base["comentarios_intencao"] = [c.texto for c in post.comentarios if c.is_intent][:8]
    return base


@app.get("/health")
def health():
    return {"ok": True}


def _latest_run_id(db, fonte: str = "all") -> Optional[int]:
    q = select(func.max(Produto.run_id))
    if fonte != "all":
        q = q.join(Post, Produto.post_id == Post.id).where(Post.fonte == fonte)
    return db.execute(q).scalar()


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
    only_new: bool = Query(False, description="só produtos novos (não vistos antes)"),
    sort: str = Query("views", description="views (viralização) | score"),
    fonte: str = Query("all", description="tiktok | meta | all"),
    idioma: str = Query("pt", description="pt | es_en | all — padrão pt-br"),
):
    q = (
        select(Produto, Post, Score)
        .join(Post, Produto.post_id == Post.id)
        .join(Score, Score.post_id == Post.id)
        .where(Produto.score_final >= min_score)
    )
    if fonte != "all":
        q = q.where(Post.fonte == fonte)
    if idioma != "all":
        q = q.where(Post.idioma == idioma)
    if only_new:
        q = q.where(Produto.novo == True)  # noqa: E712
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
            rid = _latest_run_id(db, fonte)
        if rid is not None:
            q = q.where(Produto.run_id == rid)
    # meta não tem views públicas (Ad Library) — cai pra score automaticamente
    order_col = (
        Post.play_count.desc() if sort == "views" and fonte != "meta" else Produto.score_final.desc()
    )
    q = q.order_by(order_col).limit(limit)
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


# --------------------------------------------------------------------------- #
# Engenharia reversa: cola o link de um produto validado, vê legenda/hashtags/
# comentários — só análise, não escreve em posts/produtos (decide depois como
# aproveitar isso no sistema).
# --------------------------------------------------------------------------- #
@app.get("/reverso/tiktok")
def reverso_tiktok(
    url: str = Query(..., description="link do vídeo do TikTok"),
    dry: bool = Query(False, description="true = dry-run (fixture, gasto zero)"),
    db=Depends(get_db),
    x_api_token: Optional[str] = Header(None),
):
    _require_token(x_api_token)
    url = url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="url obrigatória")

    cfg = config.load_config()
    cost = DBCost(db)
    client: Any
    if dry:
        client = DryRunClient(cost.record)
    else:
        if not config.SCRAPECREATORS_API_KEY:
            raise HTTPException(status_code=500, detail="SCRAPECREATORS_API_KEY não configurada")
        client = LiveClient(config.SCRAPECREATORS_API_KEY, cost.record)
    try:
        aweme = client.video_info(url)
        if not aweme:
            raise HTTPException(status_code=404, detail="vídeo não encontrado")
        try:
            comments = client.video_comments(url)
        except Exception:
            comments = []  # legenda ainda vale mesmo sem comentário
    finally:
        client.close()
    db.commit()  # persiste o CostLog

    desc = aweme.get("desc", "") or ""
    stats = aweme.get("statistics", {}) or {}
    author = aweme.get("author", {}) or {}
    texts = [c.text for c in comments if c.text]
    intent = intent_score(texts, desc, cfg)
    cap = caption_seller_score(desc, cfg)

    return {
        "url": url,
        "legenda": desc,
        "hashtags_encontradas": extract_hashtags(desc),
        "preco_detectado": extract_price(desc, *texts),
        "autor": author.get("nickname") or author.get("unique_id") or "",
        "engajamento": {
            "views": stats.get("play_count", 0),
            "curtidas": stats.get("digg_count", 0),
            "comentarios": stats.get("comment_count", 0),
        },
        "comentarios_lidos": len(texts),
        "n_comentarios_intencao": intent["n_comentarios_intencao"],
        "comentarios_intencao": intent["matched_comments"][:8],  # LGPD: só texto
        "sinal_legenda": cap["hits"],
        "creditos_gastos": cost.total_credits(),
    }


# --------------------------------------------------------------------------- #
# Termos sugeridos: curadoria manual, junto da engenharia reversa — só guarda
# pra avaliar depois, NÃO entra na varredura sozinho (grátis, sem custo de API).
# --------------------------------------------------------------------------- #
@app.post("/termos-sugeridos")
def criar_termo_sugerido(payload: dict, db=Depends(get_db)):
    termo = (payload.get("termo") or "").strip()
    if not termo:
        raise HTTPException(status_code=400, detail="termo obrigatório")
    fonte = (payload.get("fonte") or "geral").strip()
    if fonte not in ("tiktok", "meta", "geral"):
        raise HTTPException(status_code=400, detail="fonte inválida (tiktok|meta|geral)")
    t = TermoSugerido(termo=termo, fonte=fonte, nota=(payload.get("nota") or "").strip())
    db.add(t)
    db.commit()
    return {
        "id": t.id, "termo": t.termo, "fonte": t.fonte, "nota": t.nota,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


@app.get("/termos-sugeridos")
def listar_termos_sugeridos(db=Depends(get_db), limit: int = Query(100, ge=1, le=500)):
    rows = db.execute(
        select(TermoSugerido).order_by(TermoSugerido.id.desc()).limit(limit)
    ).scalars().all()
    return {
        "termos": [
            {
                "id": t.id, "termo": t.termo, "fonte": t.fonte, "nota": t.nota,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in rows
        ]
    }


@app.delete("/termos-sugeridos/{termo_id}")
def apagar_termo_sugerido(termo_id: int, db=Depends(get_db)):
    t = db.get(TermoSugerido, termo_id)
    if not t:
        raise HTTPException(status_code=404, detail="termo não encontrado")
    db.delete(t)
    db.commit()
    return {"ok": True}


def _run_dict(run: Run) -> dict:
    return {
        "id": run.id,
        "status": run.status,
        "mode": run.mode,
        "fonte": run.fonte,
        "summary": run.summary,
        "error": run.error,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@app.post("/varredura")
def disparar_varredura(
    db=Depends(get_db),
    dry: bool = Query(False, description="true = dry-run (fixtures, gasto zero)"),
    fonte: str = Query("tiktok", description="tiktok | meta"),
    x_api_token: Optional[str] = Header(None),
):
    _require_token(x_api_token)
    if fonte not in ("tiktok", "meta"):
        raise HTTPException(status_code=400, detail="fonte inválida (tiktok|meta)")
    run_id = jobs.start_sweep(db, live=not dry, fonte=fonte)
    if run_id is None:
        raise HTTPException(status_code=409, detail="já existe uma varredura em andamento")
    return {"run_id": run_id, "status": "queued", "mode": "dry-run" if dry else "live", "fonte": fonte}


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
                "fonte": r.fonte,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "n_produtos": counts.get(r.id, 0),
            }
            for r in runs
        ]
    }
