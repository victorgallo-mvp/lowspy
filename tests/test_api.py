from fastapi.testclient import TestClient

from app.api import app
from app.config import load_config
from app.models import Keyword
from app.pipeline import run_sweep

CFG = load_config()
client = TestClient(app)


def _seed_and_sweep(session):
    session.add(Keyword(termo="achadinhos", tipo="hashtag", mercado="fisico_revenda",
                        sinal_esperado="demanda", ativo=True))
    session.commit()
    run_sweep(session, CFG, live=False)


def test_health():
    assert client.get("/health").json() == {"ok": True}


def test_reverso_tiktok_extrai_hashtags_preco_e_intencao(session):
    r = client.get("/reverso/tiktok?url=https://tiktok.com/@x/video/123&dry=true")
    assert r.status_code == 200
    body = r.json()
    assert "apostila" in body["hashtags_encontradas"]
    assert body["preco_detectado"]
    assert body["n_comentarios_intencao"] >= 1
    assert body["creditos_gastos"] >= 1
    # LGPD: comentário só com texto, sem nickname/uid
    assert isinstance(body["comentarios_intencao"], list)


def test_reverso_tiktok_exige_url(session):
    r = client.get("/reverso/tiktok?url=  &dry=true")
    assert r.status_code == 400


def test_reverso_tiktok_grava_e_lista_historico(session):
    r = client.get("/reverso/tiktok?url=https://tiktok.com/@x/video/123&dry=true")
    hid = r.json()["id"]

    hist = client.get("/reverso/historico").json()["historico"]
    assert any(h["id"] == hid and h["fonte"] == "tiktok" and "apostila" in h["hashtags_encontradas"]
              for h in hist)

    assert client.delete(f"/reverso/historico/{hid}").status_code == 200
    hist2 = client.get("/reverso/historico").json()["historico"]
    assert not any(h["id"] == hid for h in hist2)


def test_reverso_meta_extrai_e_grava_historico(session):
    r = client.get("/reverso/meta?url=https://facebook.com/ads/library/?id=123&dry=true")
    assert r.status_code == 200
    body = r.json()
    assert body["fonte"] == "meta"
    assert "moldes" in [h.lower() for h in body["hashtags_encontradas"]] or body["preco_detectado"]
    assert body["dias_ativos"] == 24
    assert body["ativo"] is True
    assert body["digital_confirmado"] is True

    hist = client.get("/reverso/historico?fonte=meta").json()["historico"]
    assert any(h["id"] == body["id"] and h["fonte"] == "meta" for h in hist)
    # filtro por fonte não mistura com tiktok
    assert all(h["fonte"] == "meta" for h in hist)


def test_reverso_meta_exige_url(session):
    r = client.get("/reverso/meta?url=  &dry=true")
    assert r.status_code == 400


def test_reverso_tiktok_erro_na_busca_nao_quebra_o_servidor(session, monkeypatch):
    import app.scrapecreators as sc

    def _boom(self, url):
        raise RuntimeError("scrapecreators fora do ar")

    monkeypatch.setattr(sc.DryRunClient, "video_info", _boom)
    r = client.get("/reverso/tiktok?url=https://tiktok.com/@x/video/1&dry=true")
    assert r.status_code == 502
    assert "detail" in r.json()


def test_reverso_meta_erro_na_busca_nao_quebra_o_servidor(session, monkeypatch):
    import app.scrapecreators as sc

    def _boom(self, url):
        raise RuntimeError("anúncio não existe mais")

    monkeypatch.setattr(sc.DryRunClient, "ad_details", _boom)
    r = client.get("/reverso/meta?url=https://facebook.com/ads/library/?id=1&dry=true")
    assert r.status_code == 502
    assert "detail" in r.json()


def test_termos_sugeridos_cria_lista_e_apaga(session):
    r = client.post("/termos-sugeridos", json={"termo": "moldes de tricô", "fonte": "tiktok",
                                                "nota": "vi um produto parecido validado"})
    assert r.status_code == 200
    tid = r.json()["id"]

    lst = client.get("/termos-sugeridos").json()["termos"]
    assert any(t["id"] == tid and t["termo"] == "moldes de tricô" for t in lst)

    assert client.delete(f"/termos-sugeridos/{tid}").status_code == 200
    lst2 = client.get("/termos-sugeridos").json()["termos"]
    assert not any(t["id"] == tid for t in lst2)


def test_termos_sugeridos_exige_termo_e_valida_fonte(session):
    assert client.post("/termos-sugeridos", json={"termo": "  "}).status_code == 400
    assert client.post("/termos-sugeridos", json={"termo": "x", "fonte": "invalida"}).status_code == 400
    # sem "fonte" cai no default "geral"
    r = client.post("/termos-sugeridos", json={"termo": "papelaria vintage"})
    assert r.status_code == 200 and r.json()["fonte"] == "geral"


def test_listar_produtos_ordena_por_views(session):
    _seed_and_sweep(session)
    r = client.get("/produtos?limit=10")  # default sort=views (viralização)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    views = [p["engajamento"]["views"] for p in body["produtos"]]
    assert views == sorted(views, reverse=True)  # mais viral primeiro
    # sort=score volta a ordenar por score
    scores = [p["score"] for p in client.get("/produtos?sort=score").json()["produtos"]]
    assert scores == sorted(scores, reverse=True)
    # LGPD: sem nick nos comentários
    assert "comentarios_intencao" in body["produtos"][0]


def test_produtos_filtra_por_idioma_default_pt(session):
    _seed_and_sweep(session)
    # todos os posts da fixture dry-run são pt: default idioma=pt não esconde nada
    assert client.get("/produtos").json()["total"] >= 1
    # marca um post como es_en manualmente e confirma que o filtro default o esconde
    from app.db import SessionLocal
    from app.models import Post
    s2 = SessionLocal()
    post = s2.query(Post).first()
    post.idioma = "es_en"
    s2.commit()
    s2.close()
    ids_pt = {p["post_id"] for p in client.get("/produtos?idioma=pt").json()["produtos"]}
    ids_all = {p["post_id"] for p in client.get("/produtos?idioma=all").json()["produtos"]}
    assert post.id not in ids_pt
    assert post.id in ids_all


def test_detalhe_404(session):
    _seed_and_sweep(session)
    assert client.get("/produtos/nao_existe").status_code == 404


def test_custo_dia(session):
    _seed_and_sweep(session)
    body = client.get("/custo/dia").json()
    assert "dias" in body and "credit_usd" in body
    assert body["dias"]  # pelo menos 1 dia com requests de coleta


def _wait_run(run_id, tries=60):
    import time
    for _ in range(tries):
        st = client.get(f"/varredura/{run_id}").json()
        if st["status"] in ("done", "error", "interrupted"):
            return st
        time.sleep(0.1)
    return client.get(f"/varredura/{run_id}").json()


def test_varredura_dispara_assincrono(session):
    session.add(Keyword(termo="achadinhos", tipo="hashtag", mercado="fisico_revenda",
                        sinal_esperado="demanda", ativo=True))
    session.commit()
    r = client.post("/varredura?dry=true")  # dry = gasto zero
    assert r.status_code == 200
    st = _wait_run(r.json()["run_id"])
    assert st["status"] == "done"
    assert st["summary"]["sobreviventes"] >= 1
    assert client.get("/produtos").json()["total"] >= 1  # populou o dashboard


def test_varreduras_lista_e_filtro_por_run(session):
    session.add(Keyword(termo="achadinhos", tipo="hashtag", mercado="fisico_revenda",
                        sinal_esperado="demanda", ativo=True))
    session.commit()
    r = client.post("/varredura?dry=true")
    rid = r.json()["run_id"]
    _wait_run(rid)
    # a varredura aparece na lista com contagem de produtos
    vs = client.get("/varreduras").json()["varreduras"]
    assert any(v["id"] == rid and v["n_produtos"] >= 1 for v in vs)
    # /produtos?run=<id> traz só os daquela varredura
    assert client.get(f"/produtos?run={rid}").json()["total"] >= 1
    # latest = a última varredura
    assert client.get("/produtos?run=latest").json()["total"] >= 1


def test_varredura_token_protege(session, monkeypatch):
    from app import config as cfg
    monkeypatch.setattr(cfg, "TRIGGER_TOKEN", "segredo")
    assert client.post("/varredura?dry=true").status_code == 401  # sem token
    r = client.post("/varredura?dry=true", headers={"X-API-Token": "segredo"})
    assert r.status_code == 200
    _wait_run(r.json()["run_id"])  # drena a thread antes do teardown
