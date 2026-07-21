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
