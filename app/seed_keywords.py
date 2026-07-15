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
    saíram do config. Assim um redeploy com AUTO_SEED reflete o config de verdade."""
    cfg = load_config()
    disc = cfg.get("discovery", {})
    enabled = set(disc.get("enabled_markets", []))
    own = session is None
    session = session or SessionLocal()
    inserted = updated = deactivated = 0
    try:
        desired: dict[str, tuple] = {}
        for market, tags in disc.get("markets", {}).items():
            sinal = "vendedor" if market in _VENDEDOR_MARKETS else "demanda"
            for termo in tags:
                desired[termo] = (market, sinal, market in enabled)

        for termo, (market, sinal, ativo) in desired.items():
            kw = session.query(Keyword).filter_by(termo=termo, tipo="hashtag").first()
            if kw is None:
                session.add(Keyword(termo=termo, tipo="hashtag", mercado=market,
                                    sinal_esperado=sinal, ativo=ativo))
                inserted += 1
            elif (kw.mercado, kw.sinal_esperado, kw.ativo) != (market, sinal, ativo):
                kw.mercado, kw.sinal_esperado, kw.ativo = market, sinal, ativo
                updated += 1

        # desativa (preserva histórico) as que não estão mais no config
        for kw in session.query(Keyword).filter_by(tipo="hashtag").all():
            if kw.termo not in desired and kw.ativo:
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
