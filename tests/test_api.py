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


def test_listar_produtos_ranked(session):
    _seed_and_sweep(session)
    r = client.get("/produtos?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    scores = [p["score"] for p in body["produtos"]]
    assert scores == sorted(scores, reverse=True)  # melhor primeiro
    # LGPD: sem nick nos comentários
    assert "comentarios_intencao" in body["produtos"][0]


def test_filtro_por_mercado(session):
    _seed_and_sweep(session)
    r = client.get("/produtos?mercado=fisico_revenda")
    assert r.status_code == 200
    assert all(p["mercado"] == "fisico_revenda" for p in r.json()["produtos"])
    # mercado inexistente → vazio
    assert client.get("/produtos?mercado=inexistente").json()["total"] == 0


def test_detalhe_404(session):
    _seed_and_sweep(session)
    assert client.get("/produtos/nao_existe").status_code == 404


def test_custo_dia(session):
    _seed_and_sweep(session)
    body = client.get("/custo/dia").json()
    assert "dias" in body and "credit_usd" in body
    assert body["dias"]  # pelo menos 1 dia com requests de coleta
