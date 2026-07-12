#!/usr/bin/env python3
"""
Fase 0 — Piloto isolado do minerador de produtos low-ticket no TikTok orgânico.

Valida COLETA e CUSTO via ScrapeCreators, aplicando a cascata de custo crescente:
  Nível 0 (grátis) : metadado da busca (comentários > 20, curtidas, região)
  Nível 1 (gate)   : 1 página de comentários + regex de intenção ponderado
  Nível 2 (opcional): Haiku 4.5 via Batch API extrai {produto, preço, score, nicho}

Reporta: sobrevivência por nível, custo/1.000 posts (créditos e USD), projeção do
Nível 2, e exemplos de produtos com os comentários de intenção que provam a demanda.

Dry-run é o PADRÃO (usa fixtures, gasto zero). --live chama a API paga.
Endpoints (docs.scrapecreators.com, confirmados):
  GET /v1/tiktok/search/top      query, publish_time, sort_by, region, cursor
  GET /v1/tiktok/video/comments  url, cursor, trim
Auth: header x-api-key.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

BASE_URL = "https://api.scrapecreators.com"
HERE = Path(__file__).resolve().parent
LOG = logging.getLogger("pilot")


# =============================================================================
# Schemas (Pydantic) — reaproveitáveis na Fase 1. extra="ignore" tolera a
# variância do payload real da API sem quebrar o pipeline.
# =============================================================================
class SearchStats(BaseModel):
    model_config = ConfigDict(extra="ignore")
    comment_count: int = 0
    digg_count: int = 0
    play_count: int = 0
    share_count: int = 0


class SearchItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = ""
    desc: str = ""
    content_type: str = ""
    create_time: Any = ""  # API varia entre str e int (unix ts) por endpoint
    region: str = ""
    url: str = ""
    statistics: SearchStats = Field(default_factory=SearchStats)
    author: dict[str, Any] = Field(default_factory=dict)
    market: str = ""  # preenchido pelo pipeline (não vem da API)

    @property
    def author_id(self) -> str:
        return str(self.author.get("unique_id") or self.author.get("uid") or self.id)


class Comment(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = ""
    cid: str = ""
    digg_count: int = 0
    reply_comment_total: int = 0
    create_time: Any = ""  # API varia entre str e int (unix ts) por endpoint
    user: dict[str, Any] = Field(default_factory=dict)


def hashtag_to_item(a: dict[str, Any]) -> SearchItem:
    """Normaliza um item de /search/hashtag (aweme_list) para SearchItem.
    Campos diferem do /search/top: aweme_id, share_url em vez de id/url."""
    return SearchItem(
        id=str(a.get("aweme_id", "")),
        desc=a.get("desc", "") or "",
        content_type=a.get("content_type", "") or "",
        create_time=a.get("create_time", ""),
        region=a.get("region", "") or "",
        url=a.get("share_url", "") or "",
        statistics=SearchStats.model_validate(a.get("statistics", {}) or {}),
        author=a.get("author", {}) or {},
    )


# =============================================================================
# Log de custo — precursor da tabela de custo da Fase 1.
# =============================================================================
class CostTracker:
    """Mede créditos reais (via credits_remaining) e conta requests por endpoint."""

    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.records: list[dict[str, Any]] = []  # {endpoint, credits, ts}
        self.request_counts: dict[str, int] = {}
        # limpa log anterior do run
        self.log_path.write_text("", encoding="utf-8")

    def record(self, endpoint: str, credits_remaining: Optional[int], params: dict) -> None:
        self.request_counts[endpoint] = self.request_counts.get(endpoint, 0) + 1
        self.records.append(
            {"endpoint": endpoint, "credits": credits_remaining, "ts": time.time()}
        )
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "endpoint": endpoint,
                        "credits_remaining": credits_remaining,
                        "params": params,
                        "ts": time.time(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    def summarize(self, pricing: dict) -> dict[str, Any]:
        known = [r for r in self.records if r["credits"] is not None]
        measured_total: Optional[int] = None
        per_endpoint_measured: dict[str, int] = {}
        if len(known) >= 2:
            measured_total = known[0]["credits"] - known[-1]["credits"]
            # atribui cada queda ao endpoint que a causou (aproximação)
            for prev, cur in zip(known, known[1:]):
                drop = prev["credits"] - cur["credits"]
                if drop > 0:
                    per_endpoint_measured[cur["endpoint"]] = (
                        per_endpoint_measured.get(cur["endpoint"], 0) + drop
                    )

        # Fallback por assunção (útil quando o endpoint não expõe credits_remaining)
        assumed = {
            "search_top": pricing["assumed_credits_per_search_request"],
            "video_comments": pricing["assumed_credits_per_comment_request"],
        }
        per_endpoint_credits: dict[str, int] = {}
        for ep, n in self.request_counts.items():
            per_endpoint_credits[ep] = per_endpoint_measured.get(ep, assumed.get(ep, 1) * n)

        total_credits = measured_total if measured_total is not None else sum(
            per_endpoint_credits.values()
        )
        return {
            "measured_total_credits": measured_total,
            "request_counts": dict(self.request_counts),
            "per_endpoint_credits": per_endpoint_credits,
            "total_credits": total_credits,
            "credits_source": "measured (credits_remaining)"
            if measured_total is not None
            else "assumido (config)",
        }


# =============================================================================
# Clients — finos, viram o conector da Fase 1 sem refatorar.
# =============================================================================
class RetryableHTTP(Exception):
    pass


class LiveClient:
    def __init__(self, api_key: str, cost: CostTracker) -> None:
        self._client = httpx.Client(
            base_url=BASE_URL, headers={"x-api-key": api_key}, timeout=30.0
        )
        self.cost = cost

    @retry(
        retry=retry_if_exception_type(RetryableHTTP),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _get(self, path: str, params: dict) -> dict:
        resp = self._client.get(path, params=params)
        if resp.status_code == 429 or resp.status_code >= 500:
            LOG.warning("HTTP %s em %s — retry", resp.status_code, path)
            raise RetryableHTTP(f"{resp.status_code} {path}")
        resp.raise_for_status()
        return resp.json()

    def search_top(self, query: str, cfg: dict, cursor: Optional[int] = None) -> tuple[list[SearchItem], Optional[int]]:
        s = cfg["search"]
        params = {
            "query": query,
            "publish_time": s["publish_time"],
            "sort_by": s["sort_by"],
            "region": s["region"],
        }
        if cursor is not None:
            params["cursor"] = cursor
        data = self._get("/v1/tiktok/search/top", params)
        credits = data.get("credits_remaining")
        self.cost.record("search_top", credits, {"query": query, "cursor": cursor})
        items = [SearchItem.model_validate(it) for it in data.get("items", [])]
        return items, data.get("cursor")

    def search_hashtag(self, hashtag: str) -> list[SearchItem]:
        data = self._get("/v1/tiktok/search/hashtag", {"hashtag": hashtag})
        credits = data.get("credits_remaining")
        self.cost.record("search_hashtag", credits, {"hashtag": hashtag})
        return [hashtag_to_item(a) for a in data.get("aweme_list", [])]

    def video_comments(self, url: str, cfg: dict) -> list[Comment]:
        # trim=false: trim=true devolve só ~4 comentários simplificados e mata o
        # gate de intenção (achado do piloto --live). Queremos a página inteira.
        params = {"url": url, "trim": "false"}
        data = self._get("/v1/tiktok/video/comments", params)
        credits = data.get("credits_remaining")  # endpoint pode não expor
        self.cost.record("video_comments", credits, {"url": url})
        return [Comment.model_validate(c) for c in data.get("comments", [])]

    def close(self) -> None:
        self._client.close()


class DryRunClient:
    """Injeta fixtures no lugar do client real. Simula credits_remaining caindo
    1 por request pra exercitar a medição de custo offline (gasto zero)."""

    def __init__(self, cost: CostTracker) -> None:
        self.cost = cost
        self._top = json.loads((HERE / "fixtures" / "top_search.json").read_text("utf-8"))
        self._comments = json.loads((HERE / "fixtures" / "comments.json").read_text("utf-8"))
        self._fake_credits = int(self._top.get("credits_remaining", 1000))

    def search_top(self, query: str, cfg: dict, cursor: Optional[int] = None) -> tuple[list[SearchItem], Optional[int]]:
        self._fake_credits -= 1
        self.cost.record("search_top", self._fake_credits, {"query": query, "cursor": cursor})
        items = [SearchItem.model_validate(it) for it in self._top.get("items", [])]
        return items, self._top.get("cursor")

    def search_hashtag(self, hashtag: str) -> list[SearchItem]:
        self._fake_credits -= 1
        self.cost.record("search_hashtag", self._fake_credits, {"hashtag": hashtag})
        # reusa a fixture do top mapeando pros mesmos campos
        return [SearchItem.model_validate(it) for it in self._top.get("items", [])]

    def video_comments(self, url: str, cfg: dict) -> list[Comment]:
        self._fake_credits -= 1
        self.cost.record("video_comments", self._fake_credits, {"url": url})
        return [Comment.model_validate(c) for c in self._comments.get("comments", [])]

    def close(self) -> None:
        pass


# --- Filtro de idioma pt-BR (heurística leve) -------------------------------
_PT_HINTS = re.compile(
    r"\b(você|voce|não|nao|com|para|meu|minha|grátis|gratis|apenas|preço|preco|"
    r"link na bio|compre|loja|fornecedor|receita|planilha|apostila|acesse|clique)\b",
    re.IGNORECASE,
)
_NONPT_HINTS = re.compile(
    r"\b(dinero|ganar|tienes|aquí|cómo|negocio|honesta|libros|the|your|you are|free|"
    r"how to|link in bio|money|rich|dans|pour|des beaux|ton premier|en bio|"
    r"yang|bisa|murah|untuk|semua|aplikasi|terbaru|kali|pieniadze)\b",
    re.IGNORECASE,
)


def is_ptbr(text: str) -> bool:
    t = text or ""
    pt = len(_PT_HINTS.findall(t))
    non = len(_NONPT_HINTS.findall(t))
    has_pt_accents = bool(re.search(r"[ãõçáéíóúâ]", t))
    if pt or has_pt_accents:
        return pt + (1 if has_pt_accents else 0) >= non
    # sem sinal pt claro: só rejeita se houver sinal não-pt forte
    return non == 0


def select_level0_relative(items: list[SearchItem], cfg: dict) -> list[SearchItem]:
    """Threshold RELATIVO por hashtag: mantém os top-frac mais comentados,
    com piso absoluto baixo. Preserva vendedor de nicho de baixo engajamento."""
    t = cfg["thresholds"]
    floor = [
        it for it in items
        if it.statistics.comment_count >= t["abs_min_comments"]
        and it.statistics.digg_count >= t["abs_min_likes"]
    ]
    if not floor:
        return []
    ranked = sorted(floor, key=lambda x: x.statistics.comment_count, reverse=True)
    import math
    keep = max(1, math.ceil(len(ranked) * t["relative_top_frac"]))
    return ranked[:keep]


# =============================================================================
# Cascata — funções puras, reusáveis e testáveis na Fase 1.
# =============================================================================
def passes_level0(item: SearchItem, cfg: dict) -> bool:
    t = cfg["thresholds"]
    st = item.statistics
    region_ok = (not cfg["search"]["region"]) or (item.region or "").upper() in (
        cfg["search"]["region"].upper(),
        "",
    )
    return (
        st.comment_count > t["min_comments"]
        and st.digg_count >= t["min_likes"]
        and region_ok
    )


PRICE_RE = re.compile(r"(R\$\s?\d{1,4}(?:[.,]\d{2})?|\d{1,4}\s?reais)", re.IGNORECASE)


def extract_price(*texts: str) -> Optional[str]:
    for txt in texts:
        m = PRICE_RE.search(txt or "")
        if m:
            return m.group(0).strip()
    return None


def caption_seller_score(caption: str, cfg: dict) -> dict[str, Any]:
    """Sinal GRÁTIS de que há vendedor (CTA + comportamento + checkout na legenda).
    Usado pra (a) priorizar quais posts recebem o fetch pago de comentário e
    (b) recuperar vendedor digital cuja demanda é off-platform."""
    w = cfg["weights"]
    low = (caption or "").lower()
    score = 0.0
    hits: list[str] = []
    for gname, kws in (
        ("cta_legenda", cfg["keywords"]["cta_legenda"]),
        ("comportamento_venda", cfg["keywords"]["comportamento_venda"]),
        ("checkout", cfg["keywords"]["checkout"]),
    ):
        for kw in kws:
            if kw.lower() in low:
                score += w[gname]
                hits.append(kw)
                break
    return {"score": round(score, 2), "hits": hits}


def intent_score(comment_texts: list[str], caption: str, cfg: dict) -> dict[str, Any]:
    """Score ponderado (peso, não match cru). Conta comentário de intenção 1x por
    grupo, soma densidade como bônus. Mitiga ruído: match isolado pesa menos."""
    w = cfg["weights"]
    groups = {
        "intencao": cfg["intencao"],
        "comportamento_venda": cfg["keywords"]["comportamento_venda"],
        "checkout": cfg["keywords"]["checkout"],
    }
    total = len(comment_texts)
    intent_comments = 0
    raw = 0.0
    matched: list[str] = []
    for text in comment_texts:
        low = text.lower()
        hit = 0.0
        for gname, words in groups.items():
            if any(kw.lower() in low for kw in words):
                hit += w[gname]
        if hit > 0:
            intent_comments += 1
            raw += hit
            matched.append(text)

    caption_low = (caption or "").lower()
    caption_score = 0.0
    for gname in ("comportamento_venda", "checkout"):
        if any(kw.lower() in caption_low for kw in cfg["keywords"][gname]):
            caption_score += w[gname]

    density = (intent_comments / total) if total else 0.0
    density_bonus = density * w["density_bonus_max"]
    score = raw + caption_score + density_bonus
    return {
        "score": round(score, 2),
        "n_comentarios_intencao": intent_comments,
        "n_comentarios_lidos": total,
        "densidade_intencao": round(density, 3),
        "caption_score": round(caption_score, 2),
        "matched_comments": matched,
    }


def mask_nick(nick: str) -> str:
    """LGPD: nickname mascarado no relatório (não expõe PII desnecessária)."""
    if not nick:
        return "***"
    return nick[0] + "***" + (nick[-1] if len(nick) > 1 else "")


# =============================================================================
# Nível 2 (opcional) — Haiku 4.5 via Batch API.
# =============================================================================
def run_level2_llm(survivors: list[dict], cfg: dict) -> dict[str, Any]:
    try:
        import anthropic
    except ImportError:
        LOG.error("--llm requer o pacote 'anthropic' (pip install anthropic)")
        return {"ran": False, "error": "anthropic não instalado"}
    if not os.getenv("ANTHROPIC_API_KEY"):
        LOG.error("--llm requer ANTHROPIC_API_KEY no .env")
        return {"ran": False, "error": "sem ANTHROPIC_API_KEY"}

    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = anthropic.Anthropic()
    model = os.getenv("MODEL_TIER2") or cfg["pricing"]["model_tier2"]

    schema_instr = (
        "Extraia do post do TikTok um JSON com as chaves: "
        '"produto" (string curta), "preco" (string ou null), '
        '"score_intencao" (0-100, quão forte é a intenção de compra), '
        '"nicho" (string curta). Responda SÓ com o JSON.'
    )
    requests = []
    for i, s in enumerate(survivors):
        comments = "\n".join("- " + c for c in s["intent"]["matched_comments"][:15])
        prompt = (
            f"{schema_instr}\n\nLEGENDA:\n{s['item']['desc']}\n\n"
            f"COMENTÁRIOS DE INTENÇÃO:\n{comments}"
        )
        requests.append(
            Request(
                custom_id=f"post-{i}",
                params=MessageCreateParamsNonStreaming(
                    model=model,
                    max_tokens=400,
                    messages=[{"role": "user", "content": prompt}],
                ),
            )
        )

    LOG.info("Nível 2: enviando batch de %d posts para %s", len(requests), model)
    batch = client.messages.batches.create(requests=requests)
    while True:
        b = client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        LOG.info("  batch %s: %s", b.id, b.processing_status)
        time.sleep(10)

    results, in_tok, out_tok = {}, 0, 0
    for res in client.messages.batches.results(batch.id):
        if res.result.type == "succeeded":
            msg = res.result.message
            in_tok += msg.usage.input_tokens
            out_tok += msg.usage.output_tokens
            text = next((b.text for b in msg.content if b.type == "text"), "")
            try:
                results[res.custom_id] = json.loads(text)
            except json.JSONDecodeError:
                results[res.custom_id] = {"raw": text}
    p = cfg["pricing"]
    usd = (in_tok / 1e6) * p["haiku_batch_input_usd_per_mtok"] + (
        out_tok / 1e6
    ) * p["haiku_batch_output_usd_per_mtok"]
    return {
        "ran": True,
        "model": model,
        "extractions": results,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd_measured": round(usd, 6),
    }


# =============================================================================
# Orquestração do piloto
# =============================================================================
def run_pilot(cfg: dict, live: bool, use_llm: bool) -> dict[str, Any]:
    cost = CostTracker(HERE / "cost_log.jsonl")
    if live:
        api_key = os.getenv("SCRAPECREATORS_API_KEY")
        if not api_key:
            LOG.error("--live requer SCRAPECREATORS_API_KEY no .env"); sys.exit(1)
        client: Any = LiveClient(api_key, cost)
        LOG.info(">>> MODO --live: chamadas PAGAS ao ScrapeCreators")
    else:
        client = DryRunClient(cost)
        LOG.info(">>> MODO dry-run (fixtures, gasto ZERO)")

    total_seen = 0
    lang_dropped = 0
    n0_survivors: list[SearchItem] = []
    comment_fetches = 0
    survivors: list[dict] = []
    max_fetches = cfg["caps"]["max_comment_fetches"]

    disc = cfg.get("discovery", {})
    mode = disc.get("mode", "top")
    require_pt = cfg.get("language", {}).get("require_ptbr", False)

    # Monta as fontes de busca (market, source)
    sources: list[tuple[str, str]] = []
    if mode == "hashtag":
        for market in disc.get("enabled_markets", []):
            for tag in disc.get("markets", {}).get(market, []):
                sources.append((market, tag))
        sources = sources[: cfg["caps"].get("max_hashtags", 999)]
    else:
        for q in cfg["keywords"]["produto"]:
            sources.append(("produto", q))

    try:
        for market, source in sources:
            LOG.info("Busca %s | %s | %r", mode, market, source)
            try:
                if mode == "hashtag":
                    items = client.search_hashtag(source)
                else:
                    items, _cursor = client.search_top(source, cfg)
            except Exception as e:  # falha de coleta não derruba o pipeline
                LOG.error("Busca falhou para %r: %s", source, e)
                continue
            total_seen += len(items)

            # Filtro de idioma pt-BR
            if require_pt:
                kept = [it for it in items if is_ptbr(it.desc)]
                lang_dropped += len(items) - len(kept)
                items = kept

            # Nível 0: relativo por hashtag (breadth) ou absoluto no modo top
            passed = (
                select_level0_relative(items, cfg)
                if mode == "hashtag"
                else [it for it in items if passes_level0(it, cfg)]
            )
            for it in passed:
                it.market = market
                n0_survivors.append(it)

        # Dedup por URL + no máx. 1 post por autor (diversidade + economia de crédito)
        seen_urls: set[str] = set()
        seen_authors: set[str] = set()
        deduped: list[SearchItem] = []
        for it in sorted(n0_survivors, key=lambda x: x.statistics.comment_count, reverse=True):
            if not it.url or it.url in seen_urls or it.author_id in seen_authors:
                continue
            seen_urls.add(it.url)
            seen_authors.add(it.author_id)
            deduped.append(it)
        n0_survivors = deduped
        LOG.info(
            "Nível 0: %d posts (de %d vistos; %d dropados por idioma; dedup por autor)",
            len(n0_survivors), total_seen, lang_dropped,
        )

        # --- Nível 0.5 (grátis): sinal de vendedor na legenda p/ priorizar fetch ---
        by_market: dict[str, list[SearchItem]] = {}
        for it in n0_survivors:
            by_market.setdefault(it.market, []).append(it)
        for mkt in by_market:
            # ordena por (sinal de legenda, engajamento) — vendedor primeiro
            by_market[mkt].sort(
                key=lambda x: (
                    caption_seller_score(x.desc, cfg)["score"],
                    x.statistics.comment_count,
                ),
                reverse=True,
            )
        # cota por mercado: garante que nicho de baixo engajamento seja lido
        n_markets = max(1, len(by_market))
        per_market_quota = max(2, max_fetches // n_markets)

        # --- Nível 1 (gate): fetch pago de comentário, cota por mercado ---
        thr = cfg["thresholds"]["intent_threshold"]
        min_demand = cfg["weights"].get("min_intent_comments_for_demand", 2)
        for mkt, items in by_market.items():
            for it in items[:per_market_quota]:
                if comment_fetches >= max_fetches:
                    break
                cap = caption_seller_score(it.desc, cfg)
                try:
                    comments = client.video_comments(it.url, cfg)
                except Exception as e:
                    LOG.error("Comentários falharam para %s: %s", it.url, e)
                    continue
                comment_fetches += 1
                texts = [c.text for c in comments if c.text]
                intent = intent_score(texts, it.desc, cfg)
                combined = round(intent["score"] + cap["score"], 2)
                # classifica o sinal: demanda real (comentário) vs vendedor (legenda)
                if intent["n_comentarios_intencao"] >= min_demand:
                    sinal = "demanda_confirmada"
                elif cap["score"] > 0 or intent["score"] > 0:
                    sinal = "vendedor_off_platform"
                else:
                    sinal = "sem_sinal"
                intent["comment_score"] = intent["score"]
                intent["caption_score"] = cap["score"]
                intent["caption_hits"] = cap["hits"]
                intent["score"] = combined
                intent["sinal"] = sinal
                LOG.info(
                    "  N1 [%s] %s | %d coments | cap=%.1f com=%.1f tot=%.1f | %s",
                    mkt, sinal, len(texts), cap["score"], intent["comment_score"],
                    combined, it.desc[:45],
                )
                if combined >= thr and sinal != "sem_sinal":
                    survivors.append(
                        {
                            "item": it.model_dump(),
                            "market": it.market,
                            "sinal": sinal,
                            "intent": intent,
                            "preco_detectado": extract_price(it.desc, *texts),
                        }
                    )
        LOG.info("Nível 1: %d/%d sobreviveram ao gate", len(survivors), comment_fetches)
    finally:
        client.close()

    # --- Nível 2 (opcional) ---
    level2 = None
    if use_llm and survivors:
        level2 = run_level2_llm(survivors, cfg)

    return build_report(
        cfg, cost, total_seen, n0_survivors, comment_fetches, survivors, level2, live, lang_dropped
    )


def build_report(cfg, cost, total_seen, n0, comment_fetches, survivors, level2, live, lang_dropped=0) -> dict[str, Any]:
    p = cfg["pricing"]
    credit_usd = float(os.getenv("CREDIT_USD") or p["credit_usd"])
    csum = cost.summarize(p)

    def pct(a, b):
        return round(100.0 * a / b, 1) if b else 0.0

    total_credits = csum["total_credits"]
    cost_per_1000_credits = (total_credits / total_seen * 1000) if total_seen else 0.0
    cost_per_1000_usd = cost_per_1000_credits * credit_usd

    # Projeção Nível 2 (analítica) sobre 1.000 posts
    n1_rate = (len(survivors) / comment_fetches) if comment_fetches else 0.0
    n0_rate = (len(n0) / total_seen) if total_seen else 0.0
    # posts que chegariam ao N2 por 1.000 buscados ≈ taxa_N0 * taxa_N1 * 1000
    n2_posts_per_1000 = n0_rate * n1_rate * 1000
    l2_usd_per_post = (
        p["est_input_tokens_per_post"] / 1e6 * p["haiku_batch_input_usd_per_mtok"]
        + p["est_output_tokens_per_post"] / 1e6 * p["haiku_batch_output_usd_per_mtok"]
    )
    l2_proj_per_1000 = round(n2_posts_per_1000 * l2_usd_per_post, 6)

    # Breadth: sobreviventes por mercado (o ponto crítico — não colapsar num nicho)
    breadth: dict[str, int] = {}
    for s in survivors:
        breadth[s.get("market", "?")] = breadth.get(s.get("market", "?"), 0) + 1

    report = {
        "modo": "live" if live else "dry-run",
        "descoberta": cfg.get("discovery", {}).get("mode", "top"),
        "sobreviventes_por_mercado": breadth,
        "idioma_dropados": lang_dropped,
        "sobrevivencia": {
            "total_buscado": total_seen,
            "nivel_0": {"n": len(n0), "pct_do_total": pct(len(n0), total_seen)},
            "nivel_1": {
                "n": len(survivors),
                "pct_do_n0": pct(len(survivors), len(n0)),
                "comentarios_fetchados": comment_fetches,
            },
        },
        "custo": {
            "creditos_gastos_total": total_credits,
            "fonte": csum["credits_source"],
            "requests_por_endpoint": csum["request_counts"],
            "creditos_por_endpoint": csum["per_endpoint_credits"],
            "credit_usd": credit_usd,
            "custo_por_1000_posts_creditos": round(cost_per_1000_credits, 2),
            "custo_por_1000_posts_usd": round(cost_per_1000_usd, 4),
            "projecao_nivel2_usd_por_1000_posts": l2_proj_per_1000,
            "projecao_nivel2_nota": (
                f"~{round(n2_posts_per_1000,1)} posts/1000 chegam ao Haiku "
                f"(N0 {pct(len(n0),total_seen)}% × N1 {pct(len(survivors),comment_fetches)}%)"
            ),
        },
        "exemplos": [],
        "nivel0_sobreviventes": [],
    }
    survivor_urls = {s["item"]["url"] for s in survivors}
    for it in n0:
        report["nivel0_sobreviventes"].append(
            {
                "url": it.url,
                "passou_n1": it.url in survivor_urls,
                "curtidas": it.statistics.digg_count,
                "comentarios": it.statistics.comment_count,
                "tipo": it.content_type,
                "legenda": it.desc[:90],
            }
        )
    if level2:
        report["custo"]["nivel2_medido"] = {
            k: level2.get(k) for k in ("ran", "model", "input_tokens", "output_tokens", "cost_usd_measured")
        }

    ranked = sorted(survivors, key=lambda s: s["intent"]["score"], reverse=True)
    for i, s in enumerate(ranked):
        it = s["item"]
        ex = {
            "mercado": s.get("market", ""),
            "sinal": s.get("sinal", ""),
            "produto_legenda": it["desc"][:140],
            "nicho_content_type": it["content_type"],
            "url": it["url"],
            "preco_detectado": s["preco_detectado"],
            "score_intencao": s["intent"]["score"],
            "n_comentarios_intencao": s["intent"]["n_comentarios_intencao"],
            "densidade_intencao": s["intent"]["densidade_intencao"],
            "engajamento": {
                "curtidas": it["statistics"]["digg_count"],
                "comentarios": it["statistics"]["comment_count"],
            },
            # LGPD: só o texto do comentário, sem nick/uid.
            "comentarios_de_intencao": s["intent"]["matched_comments"][:5],
        }
        if level2 and level2.get("extractions"):
            ex["extracao_llm"] = level2["extractions"].get(f"post-{i}")
        report["exemplos"].append(ex)
    return report


def print_report(r: dict) -> None:
    sob, cus = r["sobrevivencia"], r["custo"]
    print("\n" + "=" * 68)
    print(f"  RELATÓRIO DO PILOTO  ({r['modo']})")
    print("=" * 68)
    print("\n[ SOBREVIVÊNCIA POR NÍVEL ]")
    print(f"  Total buscado (Nível 0 entrada) : {sob['total_buscado']}")
    print(f"  Nível 0 (metadado)  → {sob['nivel_0']['n']:>4}  ({sob['nivel_0']['pct_do_total']}% do total)")
    print(f"  Nível 1 (intenção)  → {sob['nivel_1']['n']:>4}  ({sob['nivel_1']['pct_do_n0']}% do N0, "
          f"{sob['nivel_1']['comentarios_fetchados']} coments fetchados)")
    print(f"  Descoberta={r.get('descoberta')} | idioma dropados={r.get('idioma_dropados')}")
    print(f"  >>> BREADTH (sobreviventes por mercado): {r.get('sobreviventes_por_mercado')}")

    print("\n[ CUSTO ]")
    print(f"  Créditos gastos      : {cus['creditos_gastos_total']}  ({cus['fonte']})")
    print(f"  Requests/endpoint    : {cus['requests_por_endpoint']}")
    print(f"  Créditos/endpoint    : {cus['creditos_por_endpoint']}")
    print(f"  >>> Custo/1.000 posts: {cus['custo_por_1000_posts_creditos']} créditos "
          f"= US$ {cus['custo_por_1000_posts_usd']}  (credit_usd={cus['credit_usd']})")
    print(f"  Projeção Nível 2     : US$ {cus['projecao_nivel2_usd_por_1000_posts']}/1.000 posts")
    print(f"                         {cus['projecao_nivel2_nota']}")
    if "nivel2_medido" in cus:
        print(f"  Nível 2 (medido)     : {cus['nivel2_medido']}")

    print(f"\n[ EXEMPLOS DE PRODUTOS DETECTADOS  ({len(r['exemplos'])}) ]")
    for i, ex in enumerate(r["exemplos"], 1):
        print(f"\n  {i}. [{ex.get('mercado','')}/{ex.get('sinal','')}] {ex['produto_legenda']}")
        print(f"     tipo={ex['nicho_content_type']} | preço={ex['preco_detectado']} | "
              f"score_intenção={ex['score_intencao']} | "
              f"👍{ex['engajamento']['curtidas']} 💬{ex['engajamento']['comentarios']}")
        if ex.get("extracao_llm"):
            print(f"     LLM: {ex['extracao_llm']}")
        print(f"     comentários de intenção ({ex['n_comentarios_intencao']}):")
        for c in ex["comentarios_de_intencao"]:
            print(f"       • {c}")
    print(f"\n[ LINKS — SOBREVIVENTES DO NÍVEL 0 ({len(r['nivel0_sobreviventes'])}) ]")
    for s in r["nivel0_sobreviventes"]:
        flag = "✅ N1" if s["passou_n1"] else "  ·  "
        print(f"  {flag} 👍{s['curtidas']:>6} 💬{s['comentarios']:>4}  {s['url']}")
        print(f"        {s['legenda']}")

    print("\n" + "=" * 68)
    print("  Piloto concluído. Revise os números antes de aprovar a Fase 1.")
    print("=" * 68 + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Piloto Fase 0 — coleta + custo (TikTok/ScrapeCreators)")
    ap.add_argument("--live", action="store_true", help="Chama a API PAGA (padrão: dry-run com fixtures)")
    ap.add_argument("--llm", action="store_true", help="Roda Nível 2 (Haiku Batch) nos sobreviventes")
    ap.add_argument("--config", default=str(HERE / "pilot_config.yaml"))
    ap.add_argument("--keywords", nargs="*", help="Sobrescreve as queries de busca")
    ap.add_argument("--max-comment-fetches", type=int, help="Sobrescreve o cap de fetches de comentário")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(HERE / "pilot_run.log")],
    )
    load_dotenv(HERE / ".env")
    cfg = yaml.safe_load(Path(args.config).read_text("utf-8"))
    if args.keywords:
        cfg["keywords"]["produto"] = args.keywords
    if args.max_comment_fetches is not None:
        cfg["caps"]["max_comment_fetches"] = args.max_comment_fetches

    report = run_pilot(cfg, live=args.live, use_llm=args.llm)
    (HERE / "pilot_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print_report(report)
    LOG.info("Relatório salvo em pilot_report.json e cost_log.jsonl")


if __name__ == "__main__":
    main()
