from app.config import load_config
from app.schemas import SearchItem, SearchStats
from app.signals import (
    caption_seller_score,
    classify_signal,
    engagement_norm,
    extract_price,
    final_score,
    intent_score,
    is_fisico,
    is_high_ticket,
    lang_allowed,
    normalize_score,
    select_level0_relative,
)

CFG = load_config()


def _item(comments, likes):
    return SearchItem(id="x", url="u", statistics=SearchStats(comment_count=comments, digg_count=likes))


def test_extract_price():
    assert extract_price("por apenas R$10") == "R$10"
    assert extract_price("só 10 reais hoje") == "10 reais"
    assert extract_price("sem preço aqui") is None


def test_lang_allowed_pt_es_en():
    assert lang_allowed("planilha completa acesse o link na bio") is True
    assert lang_allowed("plantilla editable, el link en la bio") is True   # espanhol OK
    assert lang_allowed("editable canva template, link in bio") is True    # inglês OK
    assert lang_allowed("hi semua template terbaru murah untuk kalian") is False  # indonésio
    assert lang_allowed("бесплатно скачать шаблон") is False  # não-latino


def test_intent_score_weights_and_density():
    comments = ["quanto custa? quero comprar", "manda o link", "que vídeo lindo"]
    r = intent_score(comments, "legenda qualquer", CFG)
    assert r["n_comentarios_intencao"] == 2
    assert r["score"] > 0
    assert 0 < r["densidade_intencao"] <= 1


def test_caption_seller_score_detects_cta():
    r = caption_seller_score("Para adquirir acesse meu perfil no instagram", CFG)
    assert r["score"] > 0
    assert r["hits"]
    assert caption_seller_score("só um vídeo aleatório", CFG)["score"] == 0.0


def test_classify_signal():
    # demanda confirmada: >=2 comentários de intenção
    intent = {"n_comentarios_intencao": 3, "score": 4.0}
    assert classify_signal(intent, {"score": 0.0}, CFG) == "demanda_confirmada"
    # vendedor: sem intenção no comentário mas CTA na legenda
    intent = {"n_comentarios_intencao": 0, "score": 0.0}
    assert classify_signal(intent, {"score": 1.5}, CFG) == "vendedor_off_platform"
    # sem sinal
    assert classify_signal(intent, {"score": 0.0}, CFG) == "sem_sinal"


def test_select_level0_relative_preserves_niche():
    # nicho de baixo engajamento: ainda contribui com seus melhores
    items = [_item(30, 200), _item(20, 150), _item(12, 100), _item(3, 10)]
    kept = select_level0_relative(items, CFG)
    assert len(kept) >= 1
    # o de 3 comentários (abaixo do piso abs_min_comments=5) é dropado
    assert all(it.statistics.comment_count >= CFG["thresholds"]["abs_min_comments"] for it in kept)


def test_is_fisico_dropa_envio():
    assert is_fisico("frete grátis, enviamos pelos Correios") is True
    assert is_fisico("compre na Shopee, pronta entrega") is True
    assert is_fisico("apostila em PDF, acesso imediato no link") is False
    assert is_fisico("editáveis no Canva, link na bio") is False


def test_is_high_ticket():
    assert is_high_ticket("Mentoria completa de tráfego pago", CFG) is True
    assert is_high_ticket("12x de R$97 na formação", CFG) is True
    assert is_high_ticket("apostila em PDF por 10 reais", CFG) is False


def test_engagement_e_final_score():
    alto = engagement_norm(1_000_000, 100_000, 5_000, CFG)
    baixo = engagement_norm(500, 50, 5, CFG)
    assert 0 <= baixo < alto <= 100  # log: mais engajamento → maior, sem estourar
    # demanda domina: mesmo com engajamento baixo, demanda alta puxa o final
    f, eng = final_score(80.0, 500, 100, 20, CFG)
    assert 0 <= f <= 100 and f > eng


def test_normalize_score_bounded():
    assert 0 <= normalize_score(5.0, CFG) <= 100
    assert normalize_score(9999.0, CFG) == 100.0
