from app.config import load_config
from app.models import Keyword
from app.seed_keywords import seed

CFG = load_config()


def test_seed_deriva_keyword_livre_so_das_hashtags_nativas(session):
    r = seed(session)
    assert r["inserted"] > 0

    tops = session.query(Keyword).filter_by(tipo="top", ativo=True).all()
    by_termo = {k.termo: k for k in tops}

    # reaproveita palavra de hashtag de mercado ativo (formato_digital) -> curada,
    # dispensa confirmação extra na legenda (o termo já prova o nicho, era hashtag)
    assert by_termo["apostila"].mercado == "keyword_livre"
    # ambíguo declarado em termos_genericos -> genérico mesmo vindo de mercado curado
    assert by_termo["colecao"].mercado == "keyword_livre_generico"

    # termo do Meta Ads NÃO entra mais no pool do TikTok (achado real: são frases de
    # copy de anúncio, renderam zero produto como busca de vídeo — cada fonte agora
    # tem vocabulário próprio)
    assert "Apenas R$14,90" not in by_termo

    # mercado desativado (fisico_revenda) não entra na keyword livre
    assert "achadinhos" not in by_termo


def test_seed_meta_query_continua_so_pro_meta_ads(session):
    # o termo do Meta Ads continua existindo — só não vira mais keyword-livre do
    # TikTok. tipo="meta_query" é o pipeline de anúncio (run_sweep_meta), inalterado.
    seed(session)
    meta_kw = session.query(Keyword).filter_by(termo="Apenas R$14,90", tipo="meta_query").first()
    assert meta_kw is not None
    assert meta_kw.ativo is True


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
