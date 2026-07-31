from app.config import load_config
from app.schemas import SearchItem, SearchStats
from app.signals import (
    caption_seller_score,
    classify_cta,
    classify_signal,
    classify_signal_meta,
    contains_termo_negativo,
    detect_idioma,
    engagement_norm,
    extract_hashtags,
    extract_price,
    final_score,
    intent_score,
    is_digital_confirmado,
    is_fisico,
    is_high_ticket,
    is_servico_local,
    lang_allowed,
    meta_ativo_norm,
    meta_final_score,
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


def test_extract_hashtags():
    assert extract_hashtags("Apostila #digital #molde2024 legal") == ["digital", "molde2024"]
    assert extract_hashtags("sem hashtag nenhuma aqui") == []
    assert extract_hashtags("") == []


def test_lang_allowed_pt_es_en():
    assert lang_allowed("planilha completa acesse o link na bio") is True
    assert lang_allowed("plantilla editable, el link en la bio") is True   # espanhol OK
    assert lang_allowed("editable canva template, link in bio") is True    # inglês OK
    assert lang_allowed("hi semua template terbaru murah untuk kalian") is False  # indonésio
    assert lang_allowed("бесплатно скачать шаблон") is False  # não-latino


def test_detect_idioma():
    assert detect_idioma("Planilha completa, você acesse o link na bio, compre agora") == "pt"
    assert detect_idioma("Plantilla editable, tienes que ganar dinero, aquí está el link") == "es_en"
    assert detect_idioma("The best template, link in bio, how to get your money") == "es_en"
    assert detect_idioma("") == "pt"  # sem sinal claro: default pt (maioria do pool)


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


def test_caption_seller_score_detects_gancho_de_quantidade():
    # padrão achado na engenharia reversa (11 exemplos validados manualmente):
    # "+N item" é gancho comum de pack/apostila vendido por volume
    r = caption_seller_score("Apostila com +250 dinâmicas prontas pra usar", CFG)
    assert r["score"] > 0
    assert any("250" in h for h in r["hits"])
    assert caption_seller_score("só um vídeo aleatório sem número nenhum", CFG)["score"] == 0.0


def test_classify_signal():
    # demanda confirmada: >= min_intent_comments_for_demand (4) comentários de intenção
    intent = {"n_comentarios_intencao": 4, "score": 5.0}
    assert classify_signal(intent, {"score": 0.0}, CFG) == "demanda_confirmada"
    # 3 comentários não basta mais (era 2; run 33: ruins tinham 2-4, bons 7-16) —
    # com sinal presente vira "vendedor", não demanda
    intent = {"n_comentarios_intencao": 3, "score": 4.0}
    assert classify_signal(intent, {"score": 0.0}, CFG) == "vendedor_off_platform"
    # vendedor: sem intenção no comentário mas CTA na legenda
    intent = {"n_comentarios_intencao": 0, "score": 0.0}
    assert classify_signal(intent, {"score": 1.5}, CFG) == "vendedor_off_platform"
    # sem sinal
    assert classify_signal(intent, {"score": 0.0}, CFG) == "sem_sinal"


def test_intent_score_so_conta_quero_em_comentario_seco():
    # "quero" seco (o pedido É o comentário) conta; "quero" enterrado em frase longa
    # sobre outra coisa (fã de influencer, conversa) não conta — achado do run 33
    secos = ["eu quero", "Quero pfv", "quero o link"]
    longos = [
        "nossa quero ficar igual vc diva meu corpo tá muito feio",
        "Quero ser amiga deles, sou viciada em jogos de tabuleiro",
        "beleza vou fzr agr as 23:36 e já quero resultado amanhã ta",
    ]
    r_secos = intent_score(secos, "legenda", CFG)
    r_longos = intent_score(longos, "legenda", CFG)
    assert r_secos["n_comentarios_intencao"] == 3
    assert r_longos["n_comentarios_intencao"] == 0
    # sinal forte (checkout/venda) vale mesmo em comentário longo
    r_forte = intent_score(["gente comprei ontem pela hotmart e chegou na hora, super recomendo"], "l", CFG)
    assert r_forte["n_comentarios_intencao"] == 1


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


def test_is_fisico_dropa_molde_fisico_por_material():
    # "molde"/"moldes" sozinho é ambíguo (físico de silicone/bolo vs digital pra
    # imprimir/cortar) — material físico desambigua mesmo sem falar de frete/envio
    assert is_fisico("molde de silicone pra resina, várias formas 😍") is True
    assert is_fisico("forma de bolo em gesso, super detalhada") is True
    assert is_fisico("moldes digitais pra cortar em EVA, arquivo em PDF") is False


def test_is_fisico_dropa_molde_industrial():
    # 3ª acepção de "molde" achada em produção (run real, anúncio do SENAI): molde de
    # manufatura/injeção plástica — nada a ver com molde de artesanato/costura digital
    assert is_fisico("Por trás de cada peça plástica de alta qualidade existe um "
                     "projeto de molde desenvolvido com precisão") is True
    assert is_fisico("Domine a tecnologia industrial que dá forma à inovação") is True
    assert is_fisico("moldes digitais pra costura, arquivo em PDF pronto pra imprimir") is False


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


def test_meta_ativo_norm_e_final_score():
    curto = meta_ativo_norm(4, CFG)
    longo = meta_ativo_norm(30, CFG)
    assert 0 <= curto < longo <= 100
    cap = caption_seller_score("Baixe agora, acesso imediato", CFG)
    f = meta_final_score(27, collation_count=6, cap_score=cap["score"], cfg=CFG)
    assert 0 <= f <= 100
    assert f > meta_ativo_norm(27, CFG)  # bônus de colation+CTA soma em cima do tempo ativo


def test_meta_ativo_norm_nao_favorece_conta_antiga_sem_limite():
    # antes do fix: conta de 5000+ dias sempre saturava em 100 e empatava com todo
    # mundo. Satura no teto ideal do doc (30d), não em "quanto mais velho, melhor".
    ideal = meta_ativo_norm(30, CFG)
    anos = meta_ativo_norm(5638, CFG)
    assert ideal == anos == 80.0  # satura, não continua subindo
    # deixa headroom (score < 100) pro bônus de CTA/collation diferenciar candidatos
    f = meta_final_score(5638, collation_count=0, cap_score=0.0, cfg=CFG)
    assert f < 100.0


def test_is_servico_local():
    assert is_servico_local("Protocolo de Harmonização Facial com Botox", CFG) is True
    assert is_servico_local("Diária no hotel com café da manhã incluso", CFG) is True
    assert is_servico_local("apostila em PDF, acesso imediato no link", CFG) is False


def test_is_digital_confirmado():
    assert is_digital_confirmado("Kit com 250 Moldes Prontos, arquivo digital por e-mail", CFG) is True
    assert is_digital_confirmado("Projetos Prontos em PDF, baixe agora", CFG) is True
    # bateu a keyword de preço/formato mas não confirma ser digital (ruído do Meta)
    assert is_digital_confirmado("Kit completo apenas R$998,00, agende sua avaliação", CFG) is False


def test_classify_signal_meta():
    cap_zero = {"score": 0.0}
    assert classify_signal_meta(20, cap_zero, CFG) == "anuncio_confirmado"  # >=15 dias
    assert classify_signal_meta(5, {"score": 1.5}, CFG) == "vendedor_off_platform"  # curto mas c/ CTA
    assert classify_signal_meta(5, cap_zero, CFG) == "sem_sinal"


def test_contains_termo_negativo():
    # caso concreto (SENAI): curso com especialista/instrutor não é produto info
    assert contains_termo_negativo(
        "Curso com especialista certificado, aulas com professor doutor", ["especialista"]
    ) == "especialista"
    assert contains_termo_negativo("apostila digital em PDF", ["especialista"]) is None
    assert contains_termo_negativo("qualquer coisa", []) is None
    # case-insensitive
    assert contains_termo_negativo("Fale com nosso ESPECIALISTA", ["especialista"]) == "especialista"


def test_classify_cta():
    assert classify_cta("WHATSAPP_MESSAGE", None) == "whatsapp"
    assert classify_cta("SHOP_NOW", "https://wa.me/5511999999999") == "whatsapp"
    assert classify_cta(None, "https://api.whatsapp.com/send?phone=123") == "whatsapp"
    assert classify_cta("SHOP_NOW", "https://minhaloja.com/checkout") == "site"
    assert classify_cta("LEARN_MORE", "https://exemplo.com") == "site"
    assert classify_cta(None, None) is None
