import time

from app.schemas import facebook_ad_to_item


def test_dias_ativos_usa_total_active_time_quando_positivo():
    it = facebook_ad_to_item({"ad_archive_id": "1", "total_active_time": 27, "start_date": None})
    assert it.dias_ativos == 27


def test_dias_ativos_cai_pra_start_date_quando_total_active_time_e_none():
    # confirmado em live: /adLibrary/search/ads sempre retorna total_active_time=None
    now = time.time()
    inicio = now - 40 * 86400  # 40 dias atrás
    it = facebook_ad_to_item({
        "ad_archive_id": "2", "total_active_time": None,
        "start_date": inicio, "is_active": True,
    })
    assert 39 <= it.dias_ativos <= 41


def test_dias_ativos_usa_end_date_se_anuncio_inativo():
    inicio = 1000000000
    fim = inicio + 10 * 86400  # rodou 10 dias e parou
    it = facebook_ad_to_item({
        "ad_archive_id": "3", "total_active_time": None,
        "start_date": inicio, "end_date": fim, "is_active": False,
    })
    assert it.dias_ativos == 10


def test_desc_cai_pro_body_do_card_em_anuncio_carrossel():
    it = facebook_ad_to_item({
        "ad_archive_id": "4",
        "snapshot": {"body": {"text": ""}, "cards": [{"body": "Kit com 250 moldes, apenas R$14,90"}]},
    })
    assert "Kit com 250 moldes" in it.desc


def test_cover_url_cai_pro_card_em_anuncio_carrossel():
    it = facebook_ad_to_item({
        "ad_archive_id": "5",
        "snapshot": {"images": [], "videos": [], "cards": [{"original_image_url": "https://x.test/img.jpg"}]},
    })
    assert it.cover_url == "https://x.test/img.jpg"
