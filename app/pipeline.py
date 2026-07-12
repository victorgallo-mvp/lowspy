"""Pipeline determinístico Fase 1: varredura do DB de keywords → N0 → N1 → storage.

Idempotente (upsert por aweme_id / cid; 1 Score/Produto por post). Log de custo por
chamada em tabela. Cascata: N0 metadado (grátis) → N0.5 sinal-de-legenda (grátis, prioriza
o fetch pago) → N1 comentários. Ranqueia por dois sinais (demanda vs vendedor).
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select

from . import config
from .db import SessionLocal, init_db
from .models import Comment, CostLog, Keyword, Post, Produto, Score
from .scrapecreators import DryRunClient, LiveClient
from .signals import (
    caption_seller_score,
    classify_signal,
    extract_price,
    intent_score,
    is_ptbr,
    normalize_score,
    select_level0_relative,
)

LOG = logging.getLogger("pipeline")


class DBCost:
    """Callback de custo → grava CostLog e mede créditos reais via credits_remaining."""

    def __init__(self, session) -> None:
        self.session = session
        self.records: list[dict] = []
        self.counts: dict[str, int] = {}

    def record(self, endpoint: str, credits: Optional[int], params: dict) -> None:
        self.counts[endpoint] = self.counts.get(endpoint, 0) + 1
        self.records.append({"endpoint": endpoint, "credits": credits})
        self.session.add(CostLog(endpoint=endpoint, params=params, credits_remaining=credits))

    def total_credits(self) -> Optional[int]:
        known = [r["credits"] for r in self.records if r["credits"] is not None]
        return (known[0] - known[-1]) if len(known) >= 2 else None


# --------------------------------------------------------------------------- #
# Upserts idempotentes
# --------------------------------------------------------------------------- #
def upsert_post(session, item, market: str) -> Post:
    post = session.get(Post, item.id)
    if post is None:
        post = Post(id=item.id)
        session.add(post)
    post.url = item.url
    post.descricao = item.desc
    post.content_type = item.content_type
    post.create_time = item.ct_int()
    post.region = item.region
    post.author_id = item.author_id
    post.author_nick = item.author_nick
    post.market = market
    post.digg_count = item.statistics.digg_count
    post.comment_count = item.statistics.comment_count
    post.play_count = item.statistics.play_count
    post.share_count = item.statistics.share_count
    return post


def upsert_score(session, post_id: str, intent: dict, cap: dict, combined: float,
                 sinal: str) -> None:
    sc = session.execute(select(Score).where(Score.post_id == post_id)).scalar_one_or_none()
    if sc is None:
        sc = Score(post_id=post_id)
        session.add(sc)
    sc.n_comentarios_intencao = intent["n_comentarios_intencao"]
    sc.n_comentarios_lidos = intent["n_comentarios_lidos"]
    sc.densidade_intencao = intent["densidade_intencao"]
    sc.caption_score = cap["score"]
    sc.comment_score = intent["score"]
    sc.score_final = combined
    sc.sinal = sinal


def upsert_produto(session, post, combined: float, sinal: str, preco) -> None:
    pr = session.execute(select(Produto).where(Produto.post_id == post.id)).scalar_one_or_none()
    if pr is None:
        pr = Produto(post_id=post.id)
        session.add(pr)
    pr.mercado = post.market
    pr.sinal = sinal
    pr.score_final = combined
    if preco:
        pr.preco = preco


# --------------------------------------------------------------------------- #
# Varredura
# --------------------------------------------------------------------------- #
def run_sweep(session, cfg: dict, live: bool,
              max_hashtags: Optional[int] = None,
              max_comment_fetches: Optional[int] = None) -> dict[str, Any]:
    caps = cfg["caps"]
    max_hashtags = max_hashtags or caps.get("max_hashtags", 999)
    max_fetches = max_comment_fetches or caps.get("max_comment_fetches", 999)
    require_pt = cfg.get("language", {}).get("require_ptbr", False)

    cost = DBCost(session)
    if live:
        if not config.SCRAPECREATORS_API_KEY:
            raise RuntimeError("--live requer SCRAPECREATORS_API_KEY no .env")
        client: Any = LiveClient(config.SCRAPECREATORS_API_KEY, cost.record)
    else:
        client = DryRunClient(cost.record)

    keywords = session.execute(
        select(Keyword).where(Keyword.ativo == True)  # noqa: E712
    ).scalars().all()[:max_hashtags]

    total_seen = 0
    lang_dropped = 0
    n0_by_id: dict[str, Any] = {}  # dedup por id (mesmo post surge em várias hashtags)
    thr = cfg["thresholds"]["intent_threshold"]

    try:
        for kw in keywords:
            LOG.info("Busca %s | %s/%s | %r", kw.tipo, kw.mercado, kw.sinal_esperado, kw.termo)
            try:
                items = (
                    client.search_hashtag(kw.termo)
                    if kw.tipo == "hashtag"
                    else client.search_top(kw.termo, cfg)
                )
            except Exception as e:  # falha de coleta não derruba o pipeline
                LOG.error("Busca falhou para %r: %s", kw.termo, e)
                continue
            total_seen += len(items)
            if require_pt:
                kept = [it for it in items if is_ptbr(it.desc)]
                lang_dropped += len(items) - len(kept)
                items = kept
            for it in select_level0_relative(items, cfg):
                if not it.id or it.id in n0_by_id:
                    continue  # mantém a 1ª ocorrência (mercado que surfou primeiro)
                it.market = kw.mercado
                it.sinal_esperado = kw.sinal_esperado
                n0_by_id[it.id] = it

        # Upsert dos posts únicos (1 por id) — idempotente
        for it in n0_by_id.values():
            upsert_post(session, it, it.market)
        session.commit()

        # Dedup p/ FETCH: 1 post por autor (breadth + economia de crédito)
        seen_authors: set[str] = set()
        by_market: dict[str, list] = {}
        for it in sorted(n0_by_id.values(), key=lambda x: x.statistics.comment_count, reverse=True):
            if not it.url or it.author_id in seen_authors:
                continue
            seen_authors.add(it.author_id)
            by_market.setdefault(it.market, []).append(it)

        # N0.5 (grátis): prioriza por sinal-de-legenda; cota por mercado
        for mkt in by_market:
            by_market[mkt].sort(
                key=lambda x: (caption_seller_score(x.desc, cfg)["score"],
                               x.statistics.comment_count),
                reverse=True,
            )
        n_markets = max(1, len(by_market))
        quota = max(2, max_fetches // n_markets)

        comment_fetches = 0
        survivors = 0
        for mkt, items in by_market.items():
            for it in items[:quota]:
                if comment_fetches >= max_fetches:
                    break
                cap = caption_seller_score(it.desc, cfg)
                try:
                    comments = client.video_comments(it.url)
                except Exception as e:
                    LOG.error("Comentários falharam p/ %s: %s", it.url, e)
                    continue
                comment_fetches += 1
                texts = [c.text for c in comments if c.text]
                intent = intent_score(texts, it.desc, cfg)
                combined = round(intent["score"] + cap["score"], 2)
                sinal = classify_signal(intent, cap, cfg)

                # persiste comentários (dedup por cid) marcando os de intenção
                intent_set = set(intent["matched_comments"])
                for c in comments:
                    if not c.cid:
                        continue
                    cm = session.get(Comment, {"cid": c.cid, "post_id": it.id})
                    if cm is None:
                        cm = Comment(cid=c.cid, post_id=it.id)
                        session.add(cm)
                    cm.texto = c.text
                    cm.digg_count = c.digg_count
                    cm.reply_total = c.reply_comment_total
                    try:
                        cm.create_time = int(c.create_time)
                    except (TypeError, ValueError):
                        cm.create_time = None
                    cm.is_intent = c.text in intent_set

                norm = normalize_score(combined, cfg)
                upsert_score(session, it.id, intent, cap, norm, sinal)
                post = session.get(Post, it.id)
                post.processed_at = datetime.now(timezone.utc)
                if combined >= thr and sinal != "sem_sinal":
                    upsert_produto(session, post, norm, sinal, extract_price(it.desc, *texts))
                    survivors += 1
                LOG.info("  N1 [%s] %s tot=%.1f norm=%.1f | %s",
                         mkt, sinal, combined, norm, it.desc[:45])
        session.commit()
    finally:
        client.close()

    breadth: dict[str, int] = {}
    for pr in session.execute(select(Produto)).scalars().all():
        breadth[pr.mercado] = breadth.get(pr.mercado, 0) + 1

    return {
        "modo": "live" if live else "dry-run",
        "total_buscado": total_seen,
        "idioma_dropados": lang_dropped,
        "n0_posts": sum(len(v) for v in by_market.values()),
        "comment_fetches": comment_fetches,
        "sobreviventes": survivors,
        "breadth": breadth,
        "creditos_gastos": cost.total_credits(),
        "requests": dict(cost.counts),
    }


def ranked_products(session, limit: int = 20) -> list[dict]:
    rows = (
        session.execute(
            select(Produto, Post, Score)
            .join(Post, Produto.post_id == Post.id)
            .join(Score, Score.post_id == Post.id)
            .order_by(Produto.score_final.desc())
            .limit(limit)
        ).all()
    )
    out = []
    for pr, post, sc in rows:
        intent_comments = [
            c.texto for c in post.comentarios if c.is_intent
        ][:5]
        out.append({
            "mercado": pr.mercado,
            "sinal": pr.sinal,
            "score": pr.score_final,
            "produto": pr.produto or post.descricao[:80],
            "preco": pr.preco,
            "url": post.url,
            "curtidas": post.digg_count,
            "comentarios": post.comment_count,
            "comentarios_intencao": intent_comments,  # LGPD: sem nick
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Fase 1 — varredura + storage")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--seed", action="store_true", help="Semeia o DB de keywords antes")
    ap.add_argument("--max-hashtags", type=int)
    ap.add_argument("--max-comment-fetches", type=int)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    init_db()
    cfg = config.load_config()
    session = SessionLocal()
    try:
        if args.seed:
            from .seed_keywords import seed
            LOG.info("Seed: %d keywords", seed(session))
        summary = run_sweep(session, cfg, args.live, args.max_hashtags, args.max_comment_fetches)
        print("\n=== RESUMO DA VARREDURA ===")
        for k, v in summary.items():
            print(f"  {k}: {v}")
        print("\n=== TOP PRODUTOS (ranqueado) ===")
        for i, p in enumerate(ranked_products(session, 15), 1):
            print(f"  {i}. [{p['mercado']}/{p['sinal']}] score={p['score']} preço={p['preco']}")
            print(f"     {p['produto'][:80]}")
            print(f"     {p['url'][:90]}")
            for c in p["comentarios_intencao"]:
                print(f"       • {c}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
