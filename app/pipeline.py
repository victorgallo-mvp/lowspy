"""Pipeline determinístico Fase 1: varredura do DB de keywords → N0 → N1 → storage.

Idempotente (upsert por aweme_id / cid; 1 Score/Produto por post). Log de custo por
chamada em tabela. Cascata: N0 metadado (grátis) → N0.5 sinal-de-legenda (grátis, prioriza
o fetch pago) → N1 comentários. Ranqueia por dois sinais (demanda vs vendedor).
"""
from __future__ import annotations

import argparse
import logging
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select

from . import config
from .db import SessionLocal, init_db
from .models import (
    CandidatoMaturacao,
    Comment,
    CostLog,
    Keyword,
    Post,
    Produto,
    Score,
    TermoNegativo,
    TermoSugerido,
)
from .scrapecreators import DryRunClient, LiveClient
from .schemas import ad_details_to_item
from .signals import (
    caption_seller_score,
    classify_cta,
    classify_signal,
    classify_signal_meta,
    contains_termo_negativo,
    detect_idioma,
    extract_hashtags,
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
    passes_level0_abs,
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
    post.cta_link = item.cta_link
    post.cta_tipo = classify_cta(item.cta_tipo_raw, item.cta_link)
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


def _registrar_maturacao(session, post_id: str, fonte: str, motivo: str) -> None:
    """Marca um post/anúncio pra reavaliação futura (motivo TEMPORAL, não
    permanente) — dedup por post_id: se já tá na fila, não reseta o progresso."""
    existente = session.execute(
        select(CandidatoMaturacao).where(CandidatoMaturacao.post_id == post_id)
    ).scalar_one_or_none()
    if existente is not None:
        return  # já rastreado (ativo ou já resolvido antes) — não mexe
    session.add(CandidatoMaturacao(post_id=post_id, fonte=fonte, motivo=motivo))


def _termos_negativos(session, fonte: str) -> list[str]:
    """Termos ativos que excluem post (curadoria manual ou promovidos de feedback
    negativo) — pega os específicos da fonte + os marcados 'todas'."""
    rows = session.execute(
        select(TermoNegativo.termo).where(
            TermoNegativo.ativo == True,  # noqa: E712
            TermoNegativo.fonte.in_([fonte, "todas"]),
        )
    ).all()
    return [r[0] for r in rows]


# hashtag de alcance genérico — aparece em qualquer vídeo viral, não é sinal de nicho
_HASHTAG_RUIDO = {
    "foryou", "foryoupage", "fyp", "fy", "fypage", "viral", "parati", "paratii",
    "tiktok", "trend", "trending", "capcut", "greenscreen", "viralvideo",
    "videoviral", "explore", "destaque", "reels", "video", "videos", "fyyyy",
}


def _colher_termos_vencedores(session, fonte: str, run_id: Optional[int]) -> int:
    """Extrai hashtag dos produtos NOVOS aprovados NESTA varredura, filtra contra
    vocabulário já ativo/já sugerido/ruído de alcance, e sugere (TermoSugerido) o
    que aparecer em 2+ produtos aprovados — fecha o ciclo (run bom alimenta o
    vocabulário do próximo) sem gastar crédito nenhum (só lê legenda já paga)."""
    if not run_id:
        return 0
    vocab_ativo = {
        k.termo.lower() for k in
        session.execute(select(Keyword).where(Keyword.ativo == True)).scalars().all()  # noqa: E712
    }
    ja_sugerido = {t.termo.lower() for t in session.execute(select(TermoSugerido)).scalars().all()}

    rows = session.execute(
        select(Post.descricao)
        .join(Produto, Produto.post_id == Post.id)
        .where(Produto.run_id == run_id, Post.fonte == fonte)
    ).all()

    contagem: Counter = Counter()
    for (desc,) in rows:
        for h in extract_hashtags(desc or ""):
            h_norm = h.lower()
            if len(h_norm) < 4 or h_norm in vocab_ativo or h_norm in ja_sugerido or h_norm in _HASHTAG_RUIDO:
                continue
            contagem[h_norm] += 1

    sugeridos = 0
    for termo, n in contagem.items():
        if n >= 2:
            session.add(TermoSugerido(
                termo=termo, fonte=fonte,
                nota=f"colhido automaticamente — apareceu em {n} produtos aprovados nesta varredura",
            ))
            ja_sugerido.add(termo)
            sugeridos += 1
    return sugeridos


def _reavaliar_maturacao_tiktok(session, client, cfg: dict, run_id: Optional[int]) -> dict[str, int]:
    """Post que caiu só por poucos comentários/curtidas AINDA (não por motivo
    permanente) fica numa fila de reavaliação — pode estar bombando e só não
    juntou massa ainda. video_info (1 crédito) reconfirma os números atuais sem
    re-buscar; se passar o piso agora, lê comentário de verdade (só assim dá pra
    confirmar demanda no TikTok) e avalia igual ao fluxo normal."""
    mat_cfg = cfg.get("discovery", {}).get("maturacao", {})
    max_tentativas = mat_cfg.get("max_tentativas", 3)
    prazo_dias = mat_cfg.get("prazo_dias", 14)
    orcamento_extra = mat_cfg.get("orcamento_extra", 50)
    ks_cfg = cfg.get("discovery", {}).get("keyword_search", {})
    piso_comentarios = ks_cfg.get("abs_min_comments", 100)
    piso_likes = cfg["thresholds"]["abs_min_likes"]
    thr = cfg["thresholds"]["intent_threshold"]
    exigir = cfg["thresholds"].get("exigir_demanda_confirmada", False)

    limite = datetime.utcnow() - timedelta(days=prazo_dias)
    candidatos = session.execute(
        select(CandidatoMaturacao).where(
            CandidatoMaturacao.fonte == "tiktok",
            CandidatoMaturacao.ativo == True,  # noqa: E712
            CandidatoMaturacao.tentativas < max_tentativas,
        ).order_by(CandidatoMaturacao.primeira_vez_visto.asc())
        # mais antigo primeiro: sem isso o orçamento por rodada (orcamento_extra)
        # não garante cobertura justa da fila — pode bater sempre nos mesmos por
        # ordem arbitrária do banco (achado real: fila de 898 quase intocada)
    ).scalars().all()

    resgatados = 0
    tentados = 0
    for cm in candidatos:
        if tentados >= orcamento_extra:
            break
        post = session.get(Post, cm.post_id)
        if post is None:
            cm.ativo = False
            continue
        try:
            aweme = client.video_info(post.url)
        except Exception as e:
            LOG.error("Maturação TikTok: video_info falhou p/ %s: %s", post.id, e)
            continue
        tentados += 1
        cm.tentativas += 1
        cm.ultima_tentativa = datetime.utcnow()
        stats = (aweme or {}).get("statistics", {}) or {}
        comment_count = int(stats.get("comment_count") or 0)
        digg_count = int(stats.get("digg_count") or 0)
        post.comment_count = comment_count
        post.digg_count = digg_count
        post.play_count = int(stats.get("play_count") or post.play_count or 0)
        maturou = comment_count >= piso_comentarios or digg_count >= piso_likes  # OR, ver passes_level0_abs
        if maturou:
            cap = caption_seller_score(post.descricao, cfg)
            try:
                comments, _ = client.video_comments(post.url)
            except Exception as e:
                LOG.error("Maturação TikTok: video_comments falhou p/ %s: %s", post.id, e)
                comments = []
            texts = [c.text for c in comments if c.text]
            intent = intent_score(texts, post.descricao, cfg)
            combined = round(intent["score"] + cap["score"], 2)
            sinal = classify_signal(intent, cap, cfg)

            intent_set = set(intent["matched_comments"])
            persistidos: set = set()
            for c in comments:
                if not c.cid or c.cid in persistidos:
                    continue
                persistidos.add(c.cid)
                comentario = session.get(Comment, {"cid": c.cid, "post_id": post.id})
                if comentario is None:
                    comentario = Comment(cid=c.cid, post_id=post.id)
                    session.add(comentario)
                comentario.texto = c.text
                comentario.digg_count = c.digg_count
                comentario.reply_total = c.reply_comment_total
                try:
                    comentario.create_time = int(c.create_time)
                except (TypeError, ValueError):
                    comentario.create_time = None
                comentario.is_intent = c.text in intent_set

            demanda_norm = normalize_score(combined, cfg)
            score_val, engaj = final_score(demanda_norm, post.play_count, post.digg_count,
                                           post.comment_count, cfg)
            upsert_score(session, post.id, intent, cap, score_val, sinal, engaj)
            post.processed_at = datetime.now(timezone.utc)
            ok = combined >= thr and (sinal == "demanda_confirmada" if exigir else sinal != "sem_sinal")
            if ok:
                upsert_produto(session, post, score_val, sinal, extract_price(post.descricao, *texts),
                               run_id, novo=False)
                resgatados += 1
            cm.ativo = False  # já teve avaliação completa (virou produto ou não) — sai da fila
            LOG.info("  MATURAÇÃO TikTok [%s] resgatado após %sx, comentarios=%s",
                     post.id, cm.tentativas, comment_count)
        elif cm.tentativas >= max_tentativas or _sem_tz(cm.primeira_vez_visto) < _sem_tz(limite):
            cm.ativo = False  # esgotou tentativas/prazo — perdido (tradeoff aceito, evita custo infinito)
        session.commit()
    return {"resgatados": resgatados, "tentados": tentados}


# --------------------------------------------------------------------------- #
# Varredura
# --------------------------------------------------------------------------- #
def run_sweep(session, cfg: dict, live: bool,
              max_hashtags: Optional[int] = None,
              max_comment_fetches: Optional[int] = None,
              run_id: Optional[int] = None,
              on_progress: Optional[Callable[[dict], None]] = None) -> dict[str, Any]:
    """Busca é só keyword-livre agora (/search/top) — hashtag deixou de ser canal de
    busca próprio (era /search/hashtag). As mesmas palavras (mercados do discovery)
    já entram como keyword-livre via seed_keywords.py, então nada se perde.

    Palavra por palavra (por prioridade), busca+avalia JUNTO (intercalado) — nunca
    gasta tudo em busca antes de ler comentário. Um teto ÚNICO de créditos
    (discovery.orcamento_total) cobre busca+leitura somadas e para o run inteiro.

    `max_hashtags`/`max_comment_fetches`: parâmetros legados (CLI/--max-hashtags),
    sem efeito no novo desenho — mantidos só por compatibilidade de assinatura.
    """
    require_pt = cfg.get("language", {}).get("require_ptbr", False)

    cost = DBCost(session)
    if live:
        if not config.SCRAPECREATORS_API_KEY:
            raise RuntimeError("--live requer SCRAPECREATORS_API_KEY no .env")
        client: Any = LiveClient(config.SCRAPECREATORS_API_KEY, cost.record)
    else:
        client = DryRunClient(cost.record)

    active_kws = session.execute(
        select(Keyword).where(Keyword.ativo == True, Keyword.tipo == "top")  # noqa: E712
    ).scalars().all()
    # Prioridade: termos dessa lista sempre entram primeiro na fila — sem isso, os
    # termos novos sempre ficariam pro final. Depois deles, termos de mercado digital
    # curado vêm antes dos genéricos (Kit/preço/palavra comum) — se o orçamento acabar
    # no meio, morre primeiro a cauda mais ruidosa. Não muda o teto, só a ORDEM.
    priority_terms = cfg.get("discovery", {}).get("prioridade", [])

    def _priority_key(kw):
        try:
            return (0, priority_terms.index(kw.termo))
        except ValueError:
            return (1 if kw.mercado == "keyword_livre" else 2, 0)

    keywords = sorted(active_kws, key=_priority_key)

    ks_cfg = cfg.get("discovery", {}).get("keyword_search", {})
    ks_recency_days = ks_cfg.get("recency_days", 15)
    ks_max_pages = ks_cfg.get("max_pages", 10)      # teto MÁXIMO por palavra
    ks_max_items = ks_cfg.get("max_items", 9999)
    ks_min_comments = ks_cfg.get("abs_min_comments", 100)
    ks_min_novos_pagina = ks_cfg.get("min_novos_por_pagina", 1)  # paginação por rendimento
    ks_sort_modes = ks_cfg.get("sort_modes", ["relevance"])  # mesmo termo, listas diferentes
    keyword_cfg = {**cfg, "thresholds": {**cfg["thresholds"], "abs_min_comments": ks_min_comments}}
    # Teto ÚNICO de créditos pro run inteiro (busca + leitura de comentário somadas) —
    # sem isso, com o pool de 100+ palavras, o run não teria fim natural num dia ruim.
    orcamento_total = cfg.get("discovery", {}).get("orcamento_total", 1000)
    if max_comment_fetches:  # override legado (CLI) — ainda serve pra ajustar o teto
        orcamento_total = max_comment_fetches

    termos_negativos = _termos_negativos(session, "tiktok")

    total_seen = 0
    lang_dropped = 0
    fisico_dropped = 0
    velho_dropped = 0
    highticket_dropped = 0
    nao_digital_dropped = 0  # bateu o termo mas não confirma ser digital
    negativo_dropped = 0  # bateu um termo negativo cadastrado (curadoria manual/feedback)
    vistos_pulados = 0
    n0_by_id: dict[str, Any] = {}  # dedup por id (mesmo post surge em várias buscas)
    watchlist_registrados: set = set()  # evita reprocessar o mesmo item em outra ordenação/página
    thr = cfg["thresholds"]["intent_threshold"]
    pular_vistos = cfg.get("discovery", {}).get("pular_vistos", False)
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
    termos_tentados = 0
    segunda_chances = [0, 0]   # [páginas-2 lidas, quase-aprovados salvos por elas]
    ranked_ids: set = set()    # já passou pelo autor-dedup (evita duplicar/re-contar autor)
    all_candidates: list = []  # fila ordenada por views, cresce a cada palavra
    fetch_idx = 0               # até onde já tentamos ler comentário em all_candidates

    def _gasto_total() -> int:
        """Créditos gastos até agora (busca + leitura) — medido real via
        credits_remaining quando disponível, senão soma de requests (~1 crédito/req)."""
        c = cost.total_credits()
        return c if c is not None else sum(cost.counts.values())

    def _collect_termo(kw) -> None:
        nonlocal total_seen, lang_dropped, fisico_dropped, highticket_dropped
        nonlocal nao_digital_dropped, velho_dropped, vistos_pulados, negativo_dropped
        items_this_kw = 0
        # o MESMO termo em cada ordenação (relevance/most-liked/date-posted) devolve
        # listas diferentes — oferta extra sem vocabulário novo; dedup via n0_by_id
        for sort_by in ks_sort_modes:
            cursor = None
            for _page in range(ks_max_pages):
                if _gasto_total() >= orcamento_total or items_this_kw >= ks_max_items:
                    return
                try:
                    items, cursor = client.search_top(kw.termo, cfg, cursor, sort_by=sort_by)
                except Exception as e:  # falha de coleta não derruba o pipeline
                    LOG.error("Busca falhou para %r (%s): %s", kw.termo, sort_by, e)
                    break
                total_seen += len(items)
                items_this_kw += len(items)
                if require_pt:
                    kept = [it for it in items if lang_allowed(it.desc)]
                    lang_dropped += len(items) - len(kept)
                    items = kept
                novos_na_pagina = 0
                # abaixo do piso de comentário/curtida — não necessariamente RUIM, pode só
                # estar bombando ainda. Se passa em tudo mais, vira candidato de maturação
                # em vez de perdido pra sempre (video_info reconfirma sem re-buscar depois).
                for it in items:
                    if passes_level0_abs(it, keyword_cfg) or not it.id:
                        continue  # esse já vai pelo caminho normal (select_level0_relative)
                    if it.id in n0_by_id or it.id in watchlist_registrados:
                        continue
                    if is_fisico(it.desc) or is_high_ticket(it.desc, cfg):
                        continue
                    if contains_termo_negativo(it.desc, termos_negativos):
                        continue
                    confia_no_termo = kw.mercado == "keyword_livre"
                    if not confia_no_termo and not is_digital_confirmado(it.desc, cfg):
                        continue  # termo genérico sem confirmação — não dá pra ler comentário aqui
                    if ks_recency_days:
                        ct = it.ct_int()
                        if ct and (now - float(ct)) > ks_recency_days * 86400:
                            continue  # já velho — não vai "rejuvenescer", não watchlist
                    it.market = kw.mercado
                    it.termo_origem = kw.termo
                    upsert_post(session, it, it.market)
                    session.flush()  # garante o post gravado ANTES do FK de maturação
                                     # apontar pra ele (achado real: Postgres rejeitava,
                                     # SQLite não pegava — autoflush=False não ordena sozinho)
                    _registrar_maturacao(session, it.id, "tiktok", "comentarios_insuficientes")
                    watchlist_registrados.add(it.id)
                for it in select_level0_relative(items, keyword_cfg):
                    if not it.id or it.id in n0_by_id:
                        continue  # mantém a 1ª ocorrência (inclui achado em outra ordenação)
                    if is_fisico(it.desc):  # backstop anti-físico (só digital)
                        fisico_dropped += 1
                        continue
                    if is_high_ticket(it.desc, cfg):  # queremos low-ticket
                        highticket_dropped += 1
                        continue
                    if contains_termo_negativo(it.desc, termos_negativos):
                        negativo_dropped += 1
                        continue
                    # termo de mercado digital curado dispensa confirmação — o próprio
                    # termo já prova o nicho. Termo genérico (Kit/preço/palavra comum
                    # tipo "colecao") sozinho não prova nada: se a legenda não confirma
                    # ser digital, NÃO descarta ainda — adia a decisão pra leitura de
                    # comentário (N1), onde os comentários (já pagos) podem confirmar
                    # ("quero o pdf", "tá no canva?"). Vendedor de legenda vazia se salva.
                    it.confirmar_digital = (
                        kw.mercado != "keyword_livre" and not is_digital_confirmado(it.desc, cfg)
                    )
                    if ks_recency_days:  # recência: foco em produto ativo agora
                        ct = it.ct_int()
                        if ct and (now - float(ct)) > ks_recency_days * 86400:
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
                    novos_na_pagina += 1
                if not cursor:
                    break  # sem próxima página nesta ordenação — tenta a próxima
                if novos_na_pagina < ks_min_novos_pagina:
                    break  # rendimento caiu — troca de ordenação/palavra em vez de cavar

    def _rank_new_candidates() -> None:
        """Autor-dedup + ranking por views dos itens NOVOS de n0_by_id (desde a
        última chamada) — soma na lista de candidatos sem mexer nos que já estão
        lá (um item ranqueado mas ainda não lido continua na fila pra próxima palavra)."""
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
        nonlocal comment_fetches, survivors, novos, fetch_idx, nao_digital_dropped
        # continua de onde parou (fetch_idx) — não relê quem já foi lido antes, e um
        # item ranqueado mas não lido por falta de orçamento fica na fila pra próxima palavra
        while fetch_idx < len(all_candidates):
            if survivors >= target or _gasto_total() >= orcamento_total:
                break
            it = all_candidates[fetch_idx]
            fetch_idx += 1
            cap = caption_seller_score(it.desc, cfg)
            try:
                comments, next_cursor = client.video_comments(it.url)
            except Exception as e:
                LOG.error("Comentários falharam p/ %s: %s", it.url, e)
                continue
            comment_fetches += 1
            texts = [c.text for c in comments if c.text]
            # decisão adiada do N0: termo genérico sem confirmação na legenda — os
            # comentários (já pagos) são a última chance de provar que é digital
            if it.confirmar_digital and not any(is_digital_confirmado(t, cfg) for t in texts):
                nao_digital_dropped += 1
                continue
            intent = intent_score(texts, it.desc, cfg)
            combined = round(intent["score"] + cap["score"], 2)
            sinal = classify_signal(intent, cap, cfg)

            # SEGUNDA CHANCE pro quase-aprovado: ficou entre o piso (2) e o mínimo (4)
            # de comentários secos na 1ª página — os "eu quero" podem estar na página
            # seguinte. Lê MAIS UMA página (+1 crédito) antes de descartar.
            min_demand = cfg["weights"].get("min_intent_comments_for_demand", 2)
            sc_min = cfg["weights"].get("segunda_chance_min_intencao")
            if (sinal != "demanda_confirmada" and sc_min is not None
                    and next_cursor is not None
                    and sc_min <= intent["n_comentarios_intencao"] < min_demand
                    and _gasto_total() < orcamento_total):
                try:
                    mais, _ = client.video_comments(it.url, cursor=next_cursor)
                    comment_fetches += 1
                    segunda_chances[0] += 1
                except Exception as e:
                    LOG.error("Segunda página de comentários falhou p/ %s: %s", it.url, e)
                    mais = []
                if mais:
                    # páginas do TikTok se sobrepõem — dedup por cid, senão o mesmo
                    # comentário conta 2x no score e vira INSERT duplicado no banco
                    ja_vistos = {c.cid for c in comments if c.cid}
                    comments = comments + [c for c in mais if not c.cid or c.cid not in ja_vistos]
                    texts = [c.text for c in comments if c.text]
                    intent = intent_score(texts, it.desc, cfg)
                    combined = round(intent["score"] + cap["score"], 2)
                    sinal = classify_signal(intent, cap, cfg)
                    if sinal == "demanda_confirmada":
                        segunda_chances[1] += 1

            # persiste comentários (dedup por cid) marcando os de intenção
            intent_set = set(intent["matched_comments"])
            persistidos: set = set()  # dedup DENTRO do lote — session.get não enxerga
            for c in comments:        # objeto pendente ainda não gravado (pkey violada)
                if not c.cid or c.cid in persistidos:
                    continue
                persistidos.add(c.cid)
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

    def _snapshot() -> dict[str, Any]:
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
            "negativo_dropados": negativo_dropped,
            "vistos_pulados": vistos_pulados,
            "n0_posts": len(all_candidates),
            "comment_fetches": comment_fetches,
            "novos": novos,
            "sobreviventes": survivors,
            "termos_tentados": termos_tentados,
            "termos_disponiveis": len(keywords),
            "segunda_chance_lidas": segunda_chances[0],
            "segunda_chance_salvos": segunda_chances[1],
            "orcamento_usado": _gasto_total(),
            "orcamento_total": orcamento_total,
            "breadth": breadth,
            "creditos_gastos": cost.total_credits(),
            "requests": dict(cost.counts),
            "maturacao_resgatados": maturacao_stats["resgatados"],
            "maturacao_tentados": maturacao_stats["tentados"],
        }

    try:
        # reavalia a fila de maturação ANTES da busca nova — são posts que já
        # passaram por todos os outros filtros antes, só faltava comentário/curtida
        maturacao_stats = _reavaliar_maturacao_tiktok(session, client, cfg, run_id)
        for kw in keywords:
            if survivors >= target or _gasto_total() >= orcamento_total:
                break
            termos_tentados += 1
            LOG.info("Busca keyword-livre | %s/%s | %r", kw.mercado, kw.sinal_esperado, kw.termo)
            _collect_termo(kw)
            for it in n0_by_id.values():
                upsert_post(session, it, it.market)
            session.commit()
            _rank_new_candidates()
            _evaluate()
            session.commit()
            if on_progress:
                try:
                    on_progress({**_snapshot(), "termo_atual": kw.termo})
                except Exception:
                    LOG.exception("on_progress falhou — não interrompe a varredura")
    finally:
        client.close()

    _colher_termos_vencedores(session, "tiktok", run_id)
    session.commit()
    return _snapshot()


# --------------------------------------------------------------------------- #
# Varredura Meta Ads (Facebook Ad Library) — fonte separada, sem comentário.
# Sinal de demanda: TEMPO DE VEICULAÇÃO (doc do operador — anúncio que sobrevive
# ao teste do mercado), não intenção em comentário.
def _sem_tz(dt):
    return dt.replace(tzinfo=None) if dt and dt.tzinfo else dt


def _reavaliar_maturacao_meta(session, client, cfg: dict, run_id: Optional[int]) -> dict[str, int]:
    """Anúncio que caiu só por dias_ativos < mínimo (não por motivo permanente) fica
    numa fila de reavaliação — dias_ativos só cresce enquanto o anúncio continuar
    ativo, então uma nova checagem (1 crédito, ad_details, sem re-buscar) pode achar
    que ele já amadureceu. Teto de tentativas/prazo evita fila infinita (custo)."""
    mat_cfg = cfg.get("discovery", {}).get("maturacao", {})
    max_tentativas = mat_cfg.get("max_tentativas", 3)
    prazo_dias = mat_cfg.get("prazo_dias", 14)
    orcamento_extra = mat_cfg.get("orcamento_extra", 50)
    m = cfg.get("meta_ads", {})
    dias_min = m.get("dias_ativos_min", 15)
    dias_max = m.get("dias_ativos_max")

    limite = datetime.utcnow() - timedelta(days=prazo_dias)
    candidatos = session.execute(
        select(CandidatoMaturacao).where(
            CandidatoMaturacao.fonte == "meta",
            CandidatoMaturacao.ativo == True,  # noqa: E712
            CandidatoMaturacao.tentativas < max_tentativas,
        ).order_by(CandidatoMaturacao.primeira_vez_visto.asc())  # ver comentário no lado tiktok
    ).scalars().all()

    resgatados = 0
    tentados = 0
    for cm in candidatos:
        if tentados >= orcamento_extra:
            break
        post = session.get(Post, cm.post_id)
        if post is None:  # nunca deveria acontecer (FK), mas não trava a fila por isso
            cm.ativo = False
            continue
        try:
            ad = client.ad_details(post.url)
        except Exception as e:
            LOG.error("Maturação Meta: ad_details falhou p/ %s: %s", post.id, e)
            continue
        tentados += 1
        cm.tentativas += 1
        cm.ultima_tentativa = datetime.utcnow()
        item = ad_details_to_item(ad or {})
        maturou = item.dias_ativos >= dias_min and (not dias_max or item.dias_ativos <= dias_max)
        if maturou:
            cap = caption_seller_score(post.descricao, cfg)
            sinal = classify_signal_meta(item.dias_ativos, cap, cfg)
            score_val = meta_final_score(item.dias_ativos, item.collation_count, cap["score"], cfg)
            post.total_active_time = item.dias_ativos
            post.is_active = item.is_active
            post.processed_at = datetime.now(timezone.utc)
            upsert_score_meta(session, post.id, cap, item.dias_ativos, score_val, sinal)
            if sinal != "sem_sinal":
                upsert_produto(session, post, score_val, sinal, extract_price(post.descricao),
                               run_id, novo=False)
                resgatados += 1
            cm.ativo = False  # já teve avaliação completa (virou produto ou não) — sai da fila
            LOG.info("  MATURAÇÃO Meta [%s] resgatado após %sx, dias_ativos=%s",
                     post.id, cm.tentativas, item.dias_ativos)
        elif cm.tentativas >= max_tentativas or _sem_tz(cm.primeira_vez_visto) < _sem_tz(limite):
            cm.ativo = False  # esgotou tentativas/prazo — perdido (tradeoff aceito, evita custo infinito)
        session.commit()
    return {"resgatados": resgatados, "tentados": tentados}


# --------------------------------------------------------------------------- #
def run_sweep_meta(session, cfg: dict, live: bool, run_id: Optional[int] = None,
                   on_progress: Optional[Callable[[dict], None]] = None) -> dict[str, Any]:
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

    termos_negativos = _termos_negativos(session, "meta")

    total_seen = 0
    sem_texto_dropped = 0  # sem desc extraída: não dá pra avaliar, não "aprova por padrão"
    fisico_dropped = 0
    servico_local_dropped = 0  # clínica/procedimento estético/hotel — ruído da keyword genérica
    highticket_dropped = 0
    nao_digital_dropped = 0  # bateu a keyword (preço/"Kit") mas não confirma ser digital
    negativo_dropped = 0  # bateu um termo negativo cadastrado (curadoria manual/feedback)
    curto_dropped = 0  # dias_ativos < dias_ativos_min
    longo_dropped = 0  # dias_ativos > dias_ativos_max (banda travada, pedido do operador)
    curto_dias: list[int] = []  # distribuição dos descartados (diagnóstico: threshold certo?)
    vistos_pulados = 0
    instituicao_dropped = 0  # anunciante grande demais (SENAI, IBPIS...) — não é o
                             # vendedor pequeno que o garimpo busca, mesmo sendo "digital"
    n0_by_id: dict[str, Any] = {}
    existing_ids = {r[0] for r in session.execute(select(Post.id)).all()}
    survivors = 0
    novos = 0
    candidates: list = []
    maturacao_stats = {"resgatados": 0, "tentados": 0}

    def _snapshot(termo_atual: str = "") -> dict[str, Any]:
        breadth: dict[str, int] = {}
        for pr in session.execute(select(Produto)).scalars().all():
            breadth[pr.mercado] = breadth.get(pr.mercado, 0) + 1
        curto_dias_stats: dict[str, int] = {}
        if curto_dias:
            s = sorted(curto_dias)
            curto_dias_stats = {"min": s[0], "mediana": s[len(s) // 2], "max": s[-1]}
        return {
            "modo": "live" if live else "dry-run",
            "fonte": "meta",
            "total_buscado": total_seen,
            "sem_texto_dropados": sem_texto_dropped,
            "fisico_dropados": fisico_dropped,
            "servico_local_dropados": servico_local_dropped,
            "highticket_dropados": highticket_dropped,
            "nao_digital_dropados": nao_digital_dropped,
            "negativo_dropados": negativo_dropped,
            "curto_dropados": curto_dropped,
            "longo_dropados": longo_dropped,
            "curto_dias_stats": curto_dias_stats,
            "vistos_pulados": vistos_pulados,
            "instituicao_dropados": instituicao_dropped,
            "n0_posts": len(candidates) or len(n0_by_id),
            "novos": novos,
            "sobreviventes": survivors,
            "breadth": breadth,
            "creditos_gastos": cost.total_credits(),
            "requests": dict(cost.counts),
            "termo_atual": termo_atual,
            "maturacao_resgatados": maturacao_stats["resgatados"],
            "maturacao_tentados": maturacao_stats["tentados"],
        }

    try:
        # reavalia a fila de maturação ANTES da busca nova — são anúncios que já
        # passaram por todos os outros filtros antes, só faltava tempo
        maturacao_stats = _reavaliar_maturacao_meta(session, client, cfg, run_id)
        if on_progress and maturacao_stats["tentados"]:
            try:
                on_progress(_snapshot("reavaliando maturação"))
            except Exception:
                LOG.exception("on_progress falhou — não interrompe a varredura")

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
                    if contains_termo_negativo(it.desc, termos_negativos):
                        negativo_dropped += 1
                        continue
                    # a partir daqui, já passou por TODO filtro permanente (conteúdo) —
                    # só falta motivo temporal (tempo de veiculação), que pode mudar
                    it.market = kw.mercado
                    it.sinal_esperado = kw.sinal_esperado
                    it.termo_origem = kw.termo
                    if it.dias_ativos < dias_min:  # não sobreviveu ao teste do mercado AINDA
                        curto_dropped += 1
                        curto_dias.append(it.dias_ativos)
                        upsert_post_meta(session, it, it.market)
                        session.flush()  # garante o post gravado ANTES do FK de maturação
                                         # apontar pra ele (mesmo bug do lado TikTok)
                        _registrar_maturacao(session, it.id, "meta", "dias_ativos_curto")
                        continue
                    if dias_max and it.dias_ativos > dias_max:  # banda travada: velho demais
                        longo_dropped += 1  # só cresce a partir daqui — não entra na maturação
                        continue
                    it.novo = it.id not in existing_ids
                    if pular_vistos and not it.novo:
                        vistos_pulados += 1
                        continue
                    n0_by_id[it.id] = it
                if not cursor:
                    break
            if on_progress:
                try:
                    on_progress(_snapshot(kw.termo))
                except Exception:
                    LOG.exception("on_progress falhou — não interrompe a varredura")

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
        max_anuncios_anunciante = m.get("max_anuncios_anunciante", 20)
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
                # total de anúncios ativos do anunciante — já pagávamos essa chamada só
                # pros que viram produto; agora também decide se é instituição grande
                # demais (contagem "opção completa": 1 crédito extra por anunciante,
                # deduplicado por página dentro do run)
                if it.page_id not in ads_count_cache:
                    try:
                        ads_count_cache[it.page_id] = client.company_ads_count(it.page_id, cfg)
                    except Exception as e:
                        LOG.error("Contagem de anúncios falhou p/ %s: %s", it.page_id, e)
                        ads_count_cache[it.page_id] = (None, None)
                total_ads, tem_mais_ads = ads_count_cache[it.page_id]
                post.anunciante_total_ads = total_ads
                post.anunciante_tem_mais_ads = tem_mais_ads
                # tem_mais_ads=True = mais anúncios que cabem numa página só (piso, não
                # exato) — sinal forte de operação grande por si só, não precisa do teto
                eh_instituicao_grande = tem_mais_ads or (total_ads and total_ads >= max_anuncios_anunciante)
                if eh_instituicao_grande:
                    instituicao_dropped += 1
                else:
                    upsert_produto(session, post, score_val, sinal, extract_price(it.desc),
                                   run_id, novo=it.novo)
                    survivors += 1
                    if it.novo:
                        novos += 1
            LOG.info("  META [%s] %s%s dias_ativos=%s score=%.1f | %s",
                     it.market, sinal, " NOVO" if it.novo else "",
                     it.dias_ativos, score_val, it.desc[:40])
            if on_progress:
                try:
                    on_progress(_snapshot("avaliando anúncios"))
                except Exception:
                    LOG.exception("on_progress falhou — não interrompe a varredura")
        session.commit()
    finally:
        client.close()

    _colher_termos_vencedores(session, "meta", run_id)
    session.commit()
    resultado = _snapshot()
    del resultado["termo_atual"]  # só faz sentido em snapshot parcial, não no final
    return resultado


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
