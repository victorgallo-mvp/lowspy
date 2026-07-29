from fastapi.testclient import TestClient

from app.api import app
from app.auth import hash_senha
from app.config import load_config
from app.models import Keyword, Usuario
from app.pipeline import run_sweep

CFG = load_config()
client = TestClient(app)


def _seed_and_sweep(session):
    session.add(Keyword(termo="achadinhos", tipo="top", mercado="fisico_revenda",
                        sinal_esperado="demanda", ativo=True))
    session.commit()
    run_sweep(session, CFG, live=False)


def _cliente_logado(session, plano="free") -> TestClient:
    session.add(Usuario(email="cliente@teste.com", senha_hash=hash_senha("senha12345"),
                        is_admin=False, ativo=True, plano=plano))
    session.commit()
    c = TestClient(app)
    r = c.post("/auth/login", json={"email": "cliente@teste.com", "senha": "senha12345"})
    assert r.status_code == 200
    return c


def test_feed_exige_login(session):
    assert client.get("/feed").status_code == 401


def test_feed_nao_exige_ser_admin(session):
    _seed_and_sweep(session)
    c = _cliente_logado(session, plano="free")
    r = c.get("/feed")
    assert r.status_code == 200  # usuário comum acessa — só admin fica de fora do /produtos


def test_feed_respeita_limite_do_plano(session):
    _seed_and_sweep(session)
    c = _cliente_logado(session, plano="free")
    body = c.get("/feed").json()
    assert body["plano"] == "free"
    assert body["limite"] == 5
    assert len(body["produtos"]) <= 5  # nunca passa do limite, mesmo se o pool for maior


def test_feed_business_ve_mais_que_free(session):
    _seed_and_sweep(session)
    c_free = _cliente_logado(session, plano="free")
    body_free = c_free.get("/feed").json()
    assert body_free["limite"] == 5

    session.add(Usuario(email="biz@teste.com", senha_hash=hash_senha("senha12345"),
                        is_admin=False, ativo=True, plano="business"))
    session.commit()
    c_biz = TestClient(app)
    c_biz.post("/auth/login", json={"email": "biz@teste.com", "senha": "senha12345"})
    body_biz = c_biz.get("/feed").json()
    assert body_biz["limite"] == 50
    # business vê tudo que existe no pool (pool pequeno no teste, cabe inteiro)
    assert len(body_biz["produtos"]) == body_biz["total_pool"]


def test_feed_nao_vaza_campos_internos(session):
    _seed_and_sweep(session)
    c = _cliente_logado(session)
    produtos = c.get("/feed").json()["produtos"]
    assert produtos
    campos = set(produtos[0].keys())
    # campos operacionais do admin não devem vazar pro cliente pagante
    assert "score" not in campos
    assert "score_componentes" not in campos
    assert "termo_origem" not in campos
    assert "mercado" not in campos
    assert "comentarios_intencao" not in campos
    # campos esperados do consumidor
    assert {"post_id", "fonte", "produto", "preco", "nicho", "url", "cover_url", "sinal", "novo"} <= campos
