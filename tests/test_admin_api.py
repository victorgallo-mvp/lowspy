from fastapi.testclient import TestClient

from app.api import app
from app.auth import hash_senha
from app.config import load_config
from app.models import Keyword, Run, Usuario
from app.pipeline import run_sweep

CFG = load_config()
client = TestClient(app)


def _seed_and_sweep(session):
    session.add(Keyword(termo="achadinhos", tipo="top", mercado="fisico_revenda",
                        sinal_esperado="demanda", ativo=True))
    session.commit()
    run_sweep(session, CFG, live=False)


def _admin_client(session) -> TestClient:
    session.add(Usuario(email="admin@teste.com", senha_hash=hash_senha("senhaadmin123"),
                        is_admin=True, ativo=True))
    session.commit()
    c = TestClient(app)
    r = c.post("/auth/login", json={"email": "admin@teste.com", "senha": "senhaadmin123"})
    assert r.status_code == 200
    c.headers["X-CSRF-Token"] = c.cookies["lowspy_csrf"]
    return c


def test_admin_usuarios_exige_login_admin(session):
    assert client.get("/admin/usuarios").status_code in (401, 403)


def test_admin_usuarios_lista_todos(session):
    c = _admin_client(session)
    session.add(Usuario(email="cliente1@teste.com", senha_hash=hash_senha("senha12345"),
                        is_admin=False, ativo=True, plano="pro"))
    session.add(Usuario(email="cliente2@teste.com", senha_hash=hash_senha("senha12345"),
                        is_admin=False, ativo=False, plano="free"))
    session.commit()
    body = c.get("/admin/usuarios").json()
    emails = {u["email"] for u in body["usuarios"]}
    assert {"admin@teste.com", "cliente1@teste.com", "cliente2@teste.com"} <= emails
    pro = next(u for u in body["usuarios"] if u["email"] == "cliente1@teste.com")
    assert pro["plano"] == "pro"
    assert pro["ativo"] is True
    inativo = next(u for u in body["usuarios"] if u["email"] == "cliente2@teste.com")
    assert inativo["ativo"] is False


def test_admin_usuarios_nao_expoe_senha_hash(session):
    c = _admin_client(session)
    body = c.get("/admin/usuarios").json()
    assert "senha_hash" not in body["usuarios"][0]


def test_admin_overview_exige_login_admin(session):
    assert client.get("/admin/overview").status_code in (401, 403)


def test_admin_overview_agrega_produtos_e_usuarios(session):
    _seed_and_sweep(session)
    c = _admin_client(session)
    session.add(Usuario(email="cliente1@teste.com", senha_hash=hash_senha("senha12345"),
                        is_admin=False, ativo=True, plano="pro"))
    session.add(Run(status="done", mode="live", fonte="tiktok"))
    session.commit()

    body = c.get("/admin/overview").json()
    assert body["total_produtos"] >= 1
    assert body["total_posts"] >= body["total_produtos"]
    assert body["produtos_por_fonte"].get("tiktok", 0) >= 1
    assert sum(body["breadth_mercado"].values()) == body["total_produtos"]
    assert body["total_usuarios"] >= 2  # admin (ativo) + cliente1
    assert body["usuarios_por_plano"].get("pro") == 1
    # crescimento_14d: lista de [data, contagem] — o produto criado agora aparece
    assert any(qtd >= 1 for _dia, qtd in body["crescimento_14d"])
    assert body["ultimas_varreduras"].get("tiktok", {}).get("status") == "done"
