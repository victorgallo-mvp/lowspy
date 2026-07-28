from app.scrapecreators import _parse_ads


def test_parse_ads_pula_item_malformado_sem_derrubar_os_outros():
    # regressão: 1 anúncio com campo inesperado não pode derrubar a página inteira
    # (achado real: era a causa dos runs Meta "vazios" achados como instabilidade)
    raw = [
        {"ad_archive_id": "1", "snapshot": {"title": "Apostila Digital"}},
        {"ad_archive_id": "2", "snapshot": None, "collation_count": "não é número"},  # malformado
        {"ad_archive_id": "3", "snapshot": {"title": "Planilha Financeira"}},
    ]
    items = _parse_ads(raw)
    assert [it.ad_archive_id for it in items] == ["1", "3"]
