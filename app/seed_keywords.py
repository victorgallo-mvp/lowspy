"""Semeia o DB de keywords {termo, mercado, sinal} a partir da taxonomia do yaml.

Sinal esperado por mercado (achado do piloto):
  físico/revenda -> demanda (intenção no comentário é forte)
  digital/criativo/nicho -> vendedor (CTA de legenda; demanda é off-platform)
"""
from __future__ import annotations

from .config import load_config
from .db import SessionLocal, init_db
from .models import Keyword

# Mercados cujo sinal primário é o vendedor (legenda/entrega), não a demanda no
# comentário — produto digital vende off-platform (metodologia do operador).
_VENDEDOR_MARKETS = {
    "formato_digital", "formato_criativo", "formato_es_en",
    "digital_info", "criativo", "nicho",
}


def seed(session=None) -> dict:
    """Resync idempotente do DB de keywords com a taxonomia do config:
    insere as novas, atualiza mercado/sinal/ativo, e DESATIVA (não apaga) as que
    saíram do config. Assim um redeploy com AUTO_SEED reflete o config de verdade.

    Cobre dois tipos: "hashtag" (TikTok, discovery.markets) e "meta_query"
    (Facebook Ad Library, meta_ads.keywords — termos EXATOS do doc do operador)."""
    cfg = load_config()
    disc = cfg.get("discovery", {})
    enabled = set(disc.get("enabled_markets", []))
    meta = cfg.get("meta_ads", {})
    meta_enabled = bool(meta.get("enabled", False))
    own = session is None
    session = session or SessionLocal()
    inserted = updated = deactivated = 0
    try:
        # (termo, tipo) -> (mercado, sinal, ativo)
        desired: dict[tuple[str, str], tuple] = {}
        for market, tags in disc.get("markets", {}).items():
            sinal = "vendedor" if market in _VENDEDOR_MARKETS else "demanda"
            for termo in tags:
                desired[(termo, "hashtag")] = (market, sinal, market in enabled)

        for grupo, tags in meta.get("keywords", {}).items():
            market = f"meta_{grupo}"
            for termo in tags:
                desired[(termo, "meta_query")] = (market, "vendedor", meta_enabled)

        # Keyword livre no TikTok (/search/top, tipo "top"): usa só o vocabulário
        # NATIVO do TikTok (discovery.markets) — não mais os termos do Meta Ads.
        #
        # Achado real (run 32-49): os termos do Meta Ads (frases de copy de anúncio,
        # tipo "Download imediato", "Apenas R$10", "1000 Atividades") são ótimos pra
        # busca de TEXTO do Facebook Ad Library, mas renderam ZERO produto no TikTok
        # em 100% dos casos testados — TikTok é busca de vídeo/nicho, não copy de
        # anúncio. Cada fonte agora tem vocabulário próprio, sem mistura.
        #
        # Origem importa pro pipeline: termos de mercado digital curado (mesmo
        # vocabulário que já era hashtag confiável) dispensam confirmação extra na
        # legenda — o próprio termo já prova o nicho. Termos ambíguos (palavra comum
        # do dia a dia, tipo "colecao"/"exercícios") sozinhos não provam nada, então
        # ainda exigem a legenda confirmar ser digital (mercado "_generico").
        ks = disc.get("keyword_search", {})
        if ks.get("enabled", False):
            ambiguos = {t.lower() for t in disc.get("termos_genericos", [])}
            termos_curados: set[str] = set()
            for market in enabled:
                termos_curados.update(disc.get("markets", {}).get(market, []))
            termos_genericos: set[str] = {t for t in termos_curados if t.lower() in ambiguos}
            termos_curados -= termos_genericos

            for termo in termos_curados:
                desired[(termo, "top")] = ("keyword_livre", "vendedor", True)
            for termo in termos_genericos:
                desired[(termo, "top")] = ("keyword_livre_generico", "vendedor", True)

        for (termo, tipo), (market, sinal, ativo) in desired.items():
            kw = session.query(Keyword).filter_by(termo=termo, tipo=tipo).first()
            if kw is None:
                session.add(Keyword(termo=termo, tipo=tipo, mercado=market,
                                    sinal_esperado=sinal, ativo=ativo))
                inserted += 1
            elif (kw.mercado, kw.sinal_esperado, kw.ativo) != (market, sinal, ativo):
                kw.mercado, kw.sinal_esperado, kw.ativo = market, sinal, ativo
                updated += 1

        # desativa (preserva histórico) as que não estão mais no config
        for kw in session.query(Keyword).filter(Keyword.tipo.in_(["hashtag", "meta_query", "top"])).all():
            if (kw.termo, kw.tipo) not in desired and kw.ativo:
                kw.ativo = False
                deactivated += 1

        session.commit()
    finally:
        if own:
            session.close()
    return {"inserted": inserted, "updated": updated, "deactivated": deactivated}


if __name__ == "__main__":
    init_db()
    print("Resync de keywords:", seed())
