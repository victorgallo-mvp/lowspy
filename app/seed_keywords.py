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
    "formato_digital", "formato_criativo", "digital_info", "criativo", "nicho",
}


def seed(session=None) -> int:
    cfg = load_config()
    disc = cfg.get("discovery", {})
    own = session is None
    session = session or SessionLocal()
    inserted = 0
    try:
        for market, tags in disc.get("markets", {}).items():
            sinal = "vendedor" if market in _VENDEDOR_MARKETS else "demanda"
            for termo in tags:
                exists = (
                    session.query(Keyword)
                    .filter_by(termo=termo, tipo="hashtag")
                    .first()
                )
                if exists:
                    continue
                session.add(
                    Keyword(
                        termo=termo,
                        tipo="hashtag",
                        mercado=market,
                        sinal_esperado=sinal,
                        ativo=(market in disc.get("enabled_markets", [])),
                    )
                )
                inserted += 1
        session.commit()
    finally:
        if own:
            session.close()
    return inserted


if __name__ == "__main__":
    init_db()
    n = seed()
    print(f"Seed concluído: {n} keywords inseridas.")
