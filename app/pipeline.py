"""Pipeline determinístico Fase 1: varredura do DB de keywords → N0 → N1 → storage.

Idempotente (upsert por aweme_id / cid; 1 Score/Produto por post). Log de custo por
chamada em tabela. Cascata: N0 metadado (grátis) → N0.5 sinal-de-legenda (grátis, prioriza
o fetch pago) → N1 comentários. Ranqueia por dois sinais (demanda vs vendedor).
"""
from __future__ import annotations

import argparse
import logging
from collections import Counter
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
    classify_signal_meta,
    detect_idioma,
    extract_price,
    final_score,
    intent_score,
    is_digital_confirmado,
    is_fisico,
    is_high_ticket,
    is_servico_local,
    lang_allowed,
    meta_final_score,
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
    if item.cover_url:
        post.cover_url = item.cover_url
    post.descricao = item.desc
    post.idioma = detect_idioma(item.desc)
    post.content_type = item.content_type
    post.create_time = item.ct_int()
    post.region = item.region
    post.author_id = item.author_id
    post.author_nick = item.author_nick
    post.market = market
    post.termo_origem = item.termo_origem
    post.digg_count = item.statistics.digg_count
    post.comment_count = item.statistics.comment_count
    post.play_count = item.statistics.play_count
    post.share_count = item.statistics.share_count
    return post


def upsert_post_meta(session, item, market: str) -> Post:
    """Upsert de anúncio do Meta (Facebook Ad Library). Reaproveita Post: author_id/
    author_nick viram page_id/page_name; digg/comment/play/share ficam 0 (não existem
    aqui — o sinal de demanda é total_active_time, não engajamento público)."""
    post = session.get(Post, item.id)
    if post is None:
        post = Post(id=item.id)
        session.add(post)
    post.fonte = "meta"
    post.url = item.url
    if item.cover_url:
        post.cover_url = item.cover_url
    post.descricao = item.desc
    post.idioma = detect_idioma(item.desc)
    post.content_type = "video" if item.snapshot.videos else ("image" if item.snapshot.images else "")
    try:
        post.create_time = int(item.start_date) if item.start_date else None
    except (TypeError, ValueError):
        post.create_time = None
    post.author_id = item.page_id
    post.author_nick = item.page_name
    post.market = market
    post.termo_origem = item.termo_origem
    post.total_active_time = item.dias_ativos
    post.collation_count = item.collation_count
    post.is_active = item.is_active
    return post


def upsert_score_meta(session, post_id: str, cap: dict, dias_ativos: int,
                      score_final: float, sinal: str) -> None:
    sc = session.execute(select(Score).where(Score.post_id == post_id)).scalar_one_or_none()
    if sc is None:
        sc = Score(post_id=post_id)
        session.add(sc)
    sc.caption_score = cap["score"]
    sc.dias_ativos = dias_ativos
    sc.score_final = score_final
    sc.sinal = sinal


def upsert_score(session, post_id: str, intent: dict, cap: dict, score_final: float,
                 sinal: str, engaj: float = 0.0) -> None:
    sc = session.execute(select(Score).where(Score.post_id == post_id)).scalar_one_or_none()
    if sc is None:
        sc = Score(post_id=post_id)
        session.add(sc)
    sc.n_comentarios_intencao = intent["n_comentarios_intencao"]
    sc.n_comentarios_lidos = intent["n_comentarios_lidos"]
    sc.densidade_intencao = intent["densidade_intencao"]
    sc.caption_score = cap["score"]
    sc.comment_score = intent["score"]
    sc.engaj_score = engaj
    sc.score_final = score_final
    sc.sinal = sinal


def upsert_produto(session, post, combined: float, sinal: str, preco,
                   run_id=None, novo=False) -> None:
    pr = session.execute(select(Produto).where(Produto.post_id == post.id)).scalar_one_or_none()
    if pr is None:
        pr = Produto(post_id=post.id)
        session.add(pr)
    pr.mercado = post.market
    pr.sinal = sinal
    pr.score_final = combined
    pr.run_id = run_id  # re-achado numa nova varredura → migra pro run atual
    pr.novo = novo
    if preco:
        pr.preco = preco


# --------------------------------------------------------------------------- #
# Varredura
# --------------------------------------------------------------------------- #
def run_sweep(session, cfg: dict, live: bool,
              max_hashtags: Optional[int] = None,
              max_comment_fetches: Optional[int] = None,
              run_id: Optional[int] = None) -> dict[str, Any]:
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

    active_kws = session.execute(
        select(Keyword).where(Keyword.ativo == True,  # noqa: E712
                              Keyword.tipo.in_(["hashtag", "top"]))
    ).scalars().all()
    ks_cfg = cfg.get("discovery", {}).get("keyword_search", {})
    ks_max_keywords = ks_cfg.get("max_keywords", 30)
    # Prioridade: termos dessa lista sempre entram primeiro na leva PRINCIPAL — sem
    # isso, o teto (max_hashtags/max_keywords) sempre corta os mesmos e os termos
    # novos nunca são buscados. Não muda QUANTO é buscado na leva principal, só a ORDEM.
    priority_terms = cfg.get("discovery", {}).get("prioridade", [])

    def _priority_key(kw):
        try:
            return (0, priority_terms.index(kw.termo))
        except ValueError:
            return (1, 0)

    hashtag_kws = sorted((k for k in active_kws if k.tipo == "hashtag"), key=_priority_key)
    top_kws = sorted((k for k in active_kws if k.tipo == "top"), key=_priority_key)
    # Leva principal: as ~max_hashtags+max_keywords de sempre (prioridade primeiro).
    # Leva de expansão: o RESTO da fila de prioridade — só usada se a principal não
    # bater a meta (target_produtos).
    main_keywords = hashtag_kws[:max_hashtags] + top_kws[:ks_max_keywords]
    expansion_keywords = hashtag_kws[max_hashtags:] + top_kws[ks_max_keywords:]

    import time

    total_seen = 0
    lang_dropped = 0
    fisico_dropped = 0
    velho_dropped = 0
    highticket_dropped = 0
    nao_digital_dropped = 0  # só keyword-livre: bateu o termo mas não confirma ser digital
    vistos_pulados = 0
    n0_by_id: dict[str, Any] = {}  # dedup por id (mesmo post surge em várias hashtags)
    thr = cfg["thresholds"]["intent_threshold"]
    recency_days = cfg["thresholds"].get("recency_days")
    max_pages = cfg["caps"].get("max_pages_per_hashtag", 1)
    pular_vistos = cfg.get("discovery", {}).get("pular_vistos", False)
    # Keyword livre (/search/top, tipo != "hashtag"): soma ao modo hashtag, com seus
    # próprios limites — mais recente, mais páginas, piso de comentário bem mais alto
    # (canal mais ruidoso; NÃO mexe no piso do hashtag, que já provou preservar nicho).
    ks_recency_days = ks_cfg.get("recency_days", recency_days)
    ks_max_pages = ks_cfg.get("max_pages", max_pages)
    ks_max_items = ks_cfg.get("max_items", 9999)
    ks_min_comments = ks_cfg.get("abs_min_comments")
    # Leva de expansão: hashtag fica mais seletiva (piso de comentário mais alto) e
    # mais recente (foco em produto ativo agora), com teto de páginas EXTRAS no total
    # (não por palavra) — pra não ficar buscando pra sempre num dia ruim.
    exp_cfg = cfg.get("discovery", {}).get("expansao", {})
    exp_min_comments = exp_cfg.get("min_comments", 20)
    exp_recency_days = exp_cfg.get("recency_days", 30)
    exp_max_paginas = exp_cfg.get("max_paginas", 100)
    exp_max_fetches_extra = exp_cfg.get("max_comment_fetches_extra", 0)
    expansion_pages_used = 0
    expansion_ligada = False
    now = time.time()
    # snapshot dos posts que JÁ existem no DB → novidade (visto em run anterior?)
    existing_ids = {r[0] for r in session.execute(select(Post.id)).all()}

    author_count: Counter = Counter()
    max_per_author = cfg["caps"].get("max_posts_per_author", 2)
    exigir = cfg["thresholds"].get("exigir_demanda_confirmada", False)
    target = cfg["caps"].get("target_produtos", 9999)
    comment_fetches = 0
    survivors = 0
    novos = 0
    ranked_ids: set = set()   # já passou pelo autor-dedup (evita duplicar/re-contar autor)
    all_candidates: list = []  # fila ordenada por views, cresce a cada leva
    fetch_idx = 0               # até onde já tentamos ler comentário em all_candidates

    def _collect(kw_list: list, expansion: bool = False) -> None:
        nonlocal total_seen, lang_dropped, fisico_dropped, highticket_dropped
        nonlocal nao_digital_dropped, velho_dropped, vistos_pulados, expansion_pages_used
        for kw in kw_list:
            if expansion and expansion_pages_used >= exp_max_paginas:
                break  # orçamento de expansão esgotado
            LOG.info("Busca%s %s | %s/%s | %r", " [expansão]" if expansion else "",
                     kw.tipo, kw.mercado, kw.sinal_esperado, kw.termo)
            is_top = kw.tipo != "hashtag"
            pages_cap = ks_max_pages if is_top else max_pages
            kw_recency = ks_recency_days if is_top else recency_days
            min_comments_override = ks_min_comments if is_top else None
            if expansion and not is_top:  # hashtag na expansão: mais seletiva + mais recente
                min_comments_override = exp_min_comments
                kw_recency = exp_recency_days
            kw_cfg = cfg
            if min_comments_override is not None:
                kw_cfg = {**cfg, "thresholds": {**cfg["thresholds"], "abs_min_comments": min_comments_override}}
            items_this_kw = 0
            cursor = None
            for _page in range(pages_cap):
                if expansion and expansion_pages_used >= exp_max_paginas:
                    break
                try:
                    if kw.tipo == "hashtag":
                        items, cursor = client.search_hashtag(kw.termo, cursor)
                    else:
                        items, cursor = client.search_top(kw.termo, cfg, cursor)
                except Exception as e:  # falha de coleta não derruba o pipeline
                    LOG.error("Busca falhou para %r: %s", kw.termo, e)
                    break
                if expansion:
                    expansion_pages_used += 1
                total_seen += len(items)
                items_this_kw += len(items)
                if require_pt:
                    kept = [it for it in items if lang_allowed(it.desc)]
                    lang_dropped += len(items) - len(kept)
                    items = kept
                for it in select_level0_relative(items, kw_cfg):
                    if not it.id or it.id in n0_by_id:
                        continue  # mantém a 1ª ocorrência (inclui já achado na leva principal)
                    if is_fisico(it.desc):  # backstop anti-físico (só digital)
                        fisico_dropped += 1
                        continue
                    if is_high_ticket(it.desc, cfg):  # queremos low-ticket
                        highticket_dropped += 1
                        continue
                    # confirmação digital só no keyword-livre (texto solto, sem sinal de
                    # nicho) — no hashtag curada, a própria hashtag JÁ é o sinal de "é
                    # digital" (achado: metade das hashtags não contém palavra de
                    # confirmação no próprio nome, e isso derrubava post bom à toa)
                    if is_top and not is_digital_confirmado(it.desc, cfg):
                        nao_digital_dropped += 1
                        continue
                    if kw_recency:  # recência: mata viral evergreen que ressurge
                        ct = it.ct_int()
                        if ct and (now - float(ct)) > kw_recency * 86400:
                            velho_dropped += 1
                            continue
                    it.market = kw.mercado
                    it.sinal_esperado = kw.sinal_esperado
                    it.termo_origem = kw.termo
                    it.novo = it.id not in existing_ids  # NOVIDADE
                    if pular_vistos and not it.novo:  # novidade na fonte: pula já visto
                        vistos_pulados += 1
                        continue
                    n0_by_id[it.id] = it
                if not cursor:
                    break  # sem próxima página
                if is_top and items_this_kw >= ks_max_items:
                    break  # teto de itens por keyword livre (o que vier primeiro)

    def _rank_new_candidates() -> None:
        """Autor-dedup + ranking por views dos itens NOVOS de n0_by_id (desde a
        última chamada) — soma na lista de candidatos sem mexer nos que já estão
        lá (um item ranqueado mas ainda não lido continua na fila pra próxima leva)."""
        novos_itens = sorted(
            (it for it in n0_by_id.values() if it.id not in ranked_ids),
            key=lambda x: x.statistics.play_count, reverse=True,
        )
        for it in novos_itens:
            ranked_ids.add(it.id)
            if not it.url or author_count[it.author_id] >= max_per_author:
                continue
            author_count[it.author_id] += 1
            all_candidates.append(it)

    def _evaluate() -> None:
        nonlocal comment_fetches, survivors, novos, fetch_idx
        # continua de onde parou (fetch_idx) — não relê quem já foi lido antes, e um
        # item ranqueado mas não lido por falta de orçamento fica na fila pra próxima leva
        while fetch_idx < len(all_candidates):
            if comment_fetches >= max_fetches or survivors >= target:
                break
            it = all_candidates[fetch_idx]
            fetch_idx += 1
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

            demanda_norm = normalize_score(combined, cfg)
            score_val, engaj = final_score(
                demanda_norm, it.statistics.play_count,
                it.statistics.digg_count, it.statistics.comment_count, cfg,
            )
            upsert_score(session, it.id, intent, cap, score_val, sinal, engaj)
            post = session.get(Post, it.id)
            post.processed_at = datetime.now(timezone.utc)
            # gate: no modo teste, exige DEMANDA CONFIRMADA no comentário
            ok = combined >= thr and (
                sinal == "demanda_confirmada" if exigir else sinal != "sem_sinal"
            )
            if ok:
                upsert_produto(session, post, score_val, sinal,
                               extract_price(it.desc, *texts), run_id, novo=it.novo)
                survivors += 1
                if it.novo:
                    novos += 1
            LOG.info("  N1 [%s] %s%s views=%s score=%.1f | %s",
                     it.market, sinal, " NOVO" if it.novo else "",
                     it.statistics.play_count, score_val, it.desc[:40])

    try:
        # Leva principal
        _collect(main_keywords)
        for it in n0_by_id.values():
            upsert_post(session, it, it.market)
        session.commit()
        _rank_new_candidates()
        _evaluate()
        session.commit()

        # Leva de expansão: só se a principal não bateu a meta e sobrou palavra pra tentar
        if survivors < target and expansion_keywords:
            expansion_ligada = True
            # orçamento de leitura de comentário SÓ da expansão, somado por cima do
            # teto principal — senão a leva principal sozinha já esgota o teto e o que
            # a expansão acha nunca chega a ser avaliado
            max_fetches += exp_max_fetches_extra
            _collect(expansion_keywords, expansion=True)
            for it in n0_by_id.values():
                upsert_post(session, it, it.market)
            session.commit()
            _rank_new_candidates()
            _evaluate()
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
        "fisico_dropados": fisico_dropped,
        "highticket_dropados": highticket_dropped,
        "nao_digital_dropados": nao_digital_dropped,
        "velhos_dropados": velho_dropped,
        "vistos_pulados": vistos_pulados,
        "n0_posts": len(all_candidates),
        "comment_fetches": comment_fetches,
        "novos": novos,
        "sobreviventes": survivors,
        "expansao_ligada": expansion_ligada,
        "expansao_paginas_usadas": expansion_pages_used,
        "breadth": breadth,
        "creditos_gastos": cost.total_credits(),
        "requests": dict(cost.counts),
    }


# --------------------------------------------------------------------------- #
# Varredura Meta Ads (Facebook Ad Library) — fonte separada, sem comentário.
# Sinal de demanda: TEMPO DE VEICULAÇÃO (doc do operador — anúncio que sobrevive
# ao teste do mercado), não intenção em comentário.
# --------------------------------------------------------------------------- #
def run_sweep_meta(session, cfg: dict, live: bool, run_id: Optional[int] = None) -> dict[str, Any]:
    m = cfg.get("meta_ads", {})
    if not m.get("enabled", False):
        return {"modo": "meta-disabled", "fonte": "meta", "sobreviventes": 0}

    max_queries = m.get("max_queries", 30)
    max_pages = m.get("max_pages_per_query", 2)
    dias_min = m.get("dias_ativos_min", 15)
    dias_max = m.get("dias_ativos_max")  # banda: fora de [dias_min, dias_max] descarta
    target = m.get("target_produtos", 30)
    pular_vistos = cfg.get("discovery", {}).get("pular_vistos", False)

    cost = DBCost(session)
    if live:
        if not config.SCRAPECREATORS_API_KEY:
            raise RuntimeError("--live requer SCRAPECREATORS_API_KEY no .env")
        client: Any = LiveClient(config.SCRAPECREATORS_API_KEY, cost.record)
    else:
        client = DryRunClient(cost.record)

    keywords = session.execute(
        select(Keyword).where(Keyword.ativo == True, Keyword.tipo == "meta_query")  # noqa: E712
    ).scalars().all()[:max_queries]

    total_seen = 0
    sem_texto_dropped = 0  # sem desc extraída: não dá pra avaliar, não "aprova por padrão"
    fisico_dropped = 0
    servico_local_dropped = 0  # clínica/procedimento estético/hotel — ruído da keyword genérica
    highticket_dropped = 0
    nao_digital_dropped = 0  # bateu a keyword (preço/"Kit") mas não confirma ser digital
    curto_dropped = 0  # dias_ativos < dias_ativos_min
    longo_dropped = 0  # dias_ativos > dias_ativos_max (banda travada, pedido do operador)
    curto_dias: list[int] = []  # distribuição dos descartados (diagnóstico: threshold certo?)
    vistos_pulados = 0
    n0_by_id: dict[str, Any] = {}
    existing_ids = {r[0] for r in session.execute(select(Post.id)).all()}

    try:
        for kw in keywords:
            LOG.info("Busca Meta | %s/%s | %r", kw.mercado, kw.sinal_esperado, kw.termo)
            cursor = None
            for _page in range(max_pages):
                try:
                    items, cursor = client.search_facebook_ads(kw.termo, cfg, cursor)
                except Exception as e:  # falha de coleta não derruba o pipeline
                    LOG.error("Busca Meta falhou para %r: %s", kw.termo, e)
                    break
                total_seen += len(items)
                for it in items:
                    if not it.id or it.id in n0_by_id:
                        continue  # mantém a 1ª ocorrência
                    if not it.desc.strip():  # sem texto extraído: não avaliável, fora
                        sem_texto_dropped += 1
                        continue
                    if is_fisico(it.desc):  # backstop anti-físico (só digital)
                        fisico_dropped += 1
                        continue
                    if is_servico_local(it.desc, cfg):  # clínica/procedimento/hotel etc.
                        servico_local_dropped += 1
                        continue
                    if is_high_ticket(it.desc, cfg):  # queremos low-ticket
                        highticket_dropped += 1
                        continue
                    if not is_digital_confirmado(it.desc, cfg):  # keyword sozinha não prova nada
                        nao_digital_dropped += 1
                        continue
                    if it.dias_ativos < dias_min:  # não sobreviveu ao teste do mercado ainda
                        curto_dropped += 1
                        curto_dias.append(it.dias_ativos)
                        continue
                    if dias_max and it.dias_ativos > dias_max:  # banda travada: velho demais
                        longo_dropped += 1
                        continue
                    it.market = kw.mercado
                    it.sinal_esperado = kw.sinal_esperado
                    it.termo_origem = kw.termo
                    it.novo = it.id not in existing_ids
                    if pular_vistos and not it.novo:
                        vistos_pulados += 1
                        continue
                    n0_by_id[it.id] = it
                if not cursor:
                    break

        # Upsert dos anúncios únicos (1 por ad_archive_id) — idempotente
        for it in n0_by_id.values():
            upsert_post_meta(session, it, it.market)
        session.commit()

        # Sem fetch pago extra: ordena por score_final (tempo ativo + CTA + variações),
        # não por dias_ativos cru — senão conta antiga de anos sempre vence só por idade.
        caps_by_id = {it.id: caption_seller_score(it.desc, cfg) for it in n0_by_id.values()}
        ranked = sorted(
            n0_by_id.values(),
            key=lambda it: meta_final_score(it.dias_ativos, it.collation_count,
                                            caps_by_id[it.id]["score"], cfg),
            reverse=True,
        )

        # "não repetir": limite de anúncios da MESMA página no resultado final (dentro
        # da mesma varredura — diferente de pular_vistos, que evita re-achar entre runs)
        max_per_pagina = m.get("max_ads_por_pagina", 2)
        page_count: Counter = Counter()
        candidates: list = []
        for it in ranked:
            if page_count[it.page_id] >= max_per_pagina:
                continue
            page_count[it.page_id] += 1
            candidates.append(it)

        survivors = 0
        novos = 0
        ads_count_cache: dict[str, tuple[int, bool]] = {}  # 1 chamada por página/anunciante no run
        for it in candidates:
            if survivors >= target:
                break
            cap = caps_by_id[it.id]
            sinal = classify_signal_meta(it.dias_ativos, cap, cfg)
            score_val = meta_final_score(it.dias_ativos, it.collation_count, cap["score"], cfg)
            upsert_score_meta(session, it.id, cap, it.dias_ativos, score_val, sinal)
            post = session.get(Post, it.id)
            post.processed_at = datetime.now(timezone.utc)
            if sinal != "sem_sinal":
                # total de anúncios ativos do anunciante — só pros que viram produto
                # (contagem "opção completa": 1 crédito extra por anunciante, deduplicado)
                if it.page_id not in ads_count_cache:
                    try:
                        ads_count_cache[it.page_id] = client.company_ads_count(it.page_id, cfg)
                    except Exception as e:
                        LOG.error("Contagem de anúncios falhou p/ %s: %s", it.page_id, e)
                        ads_count_cache[it.page_id] = (None, None)
                post.anunciante_total_ads, post.anunciante_tem_mais_ads = ads_count_cache[it.page_id]
                upsert_produto(session, post, score_val, sinal, extract_price(it.desc),
                               run_id, novo=it.novo)
                survivors += 1
                if it.novo:
                    novos += 1
            LOG.info("  META [%s] %s%s dias_ativos=%s score=%.1f | %s",
                     it.market, sinal, " NOVO" if it.novo else "",
                     it.dias_ativos, score_val, it.desc[:40])
        session.commit()
    finally:
        client.close()

    breadth: dict[str, int] = {}
    for pr in session.execute(select(Produto)).scalars().all():
        breadth[pr.mercado] = breadth.get(pr.mercado, 0) + 1

    curto_dias_stats: dict[str, int] = {}
    if curto_dias:
        curto_dias.sort()
        n = len(curto_dias)
        curto_dias_stats = {
            "min": curto_dias[0],
            "mediana": curto_dias[n // 2],
            "max": curto_dias[-1],
        }

    return {
        "modo": "live" if live else "dry-run",
        "fonte": "meta",
        "total_buscado": total_seen,
        "sem_texto_dropados": sem_texto_dropped,
        "fisico_dropados": fisico_dropped,
        "servico_local_dropados": servico_local_dropped,
        "highticket_dropados": highticket_dropped,
        "nao_digital_dropados": nao_digital_dropped,
        "curto_dropados": curto_dropped,
        "longo_dropados": longo_dropped,  # banda travada: dias_ativos > dias_ativos_max
        "curto_dias_stats": curto_dias_stats,  # diagnóstico: threshold errado ou pool é assim mesmo?
        "vistos_pulados": vistos_pulados,
        "n0_posts": len(candidates),
        "novos": novos,
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
            LOG.info("Resync de keywords: %s", seed(session))
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
