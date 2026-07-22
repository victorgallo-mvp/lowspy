from app.config import load_config
from app.models import Keyword
from app.seed_keywords import seed

CFG = load_config()


def test_seed_deriva_keyword_livre_das_hashtags_e_do_meta(session):
    r = seed(session)
    assert r["inserted"] > 0

    tops = session.query(Keyword).filter_by(tipo="top", ativo=True).all()
    termos_top = {k.termo for k in tops}

    # reaproveita palavra de hashtag de mercado ativo (formato_digital) e termo exato do Meta
    assert "apostila" in termos_top
    assert "Apenas R$14,90" in termos_top
    assert all(k.mercado == "keyword_livre" for k in tops)

    # mercado desativado (fisico_revenda) não entra na keyword livre
    assert "achadinhos" not in termos_top


def test_seed_desativa_keyword_livre_quando_keyword_search_desliga(session):
    import copy
    cfg_on = copy.deepcopy(CFG)
    seed_com_cfg(session, cfg_on)
    assert session.query(Keyword).filter_by(tipo="top", ativo=True).count() > 0

    cfg_off = copy.deepcopy(CFG)
    cfg_off["discovery"]["keyword_search"]["enabled"] = False
    seed_com_cfg(session, cfg_off)
    assert session.query(Keyword).filter_by(tipo="top", ativo=True).count() == 0


def seed_com_cfg(session, cfg):
    """Roda seed() com um cfg customizado (monkeypatch simples via load_config)."""
    import app.seed_keywords as mod
    original = mod.load_config
    mod.load_config = lambda: cfg
    try:
        return seed(session)
    finally:
        mod.load_config = original
