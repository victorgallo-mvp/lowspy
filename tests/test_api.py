from fastapi.testclient import TestClient

from app.api import app
from app.auth import hash_senha
from app.config import load_config
from app.models import Keyword, Usuario
from app.pipeline import run_sweep

CFG = load_config()
client = TestClient(app)  # anônimo — só pra rotas públicas (/health, /auth/*)


def _admin_client(session) -> TestClient:
    """Cliente próprio, logado como admin — evita cookie vazar entre testes que
    compartilham o `client` anônimo (cada teste tem DB isolado via fixture)."""
    session.add(Usuario(email="admin@teste.com", senha_hash=hash_senha("senhaadmin123"),
                        is_admin=True, ativo=True))
    session.commit()
    c = TestClient(app)
    r = c.post("/auth/login", json={"email": "admin@teste.com", "senha": "senhaadmin123"})
    assert r.status_code == 200
    # header CSRF fixo no client — todo POST/DELETE dele já sai protegido, igual o
    # frontend faz lendo o cookie legível e ecoando no header (ver lib/api.ts)
    c.headers["X-CSRF-Token"] = c.cookies["lowspy_csrf"]
    return c


def _seed_and_sweep(session):
    session.add(Keyword(termo="achadinhos", tipo="top", mercado="fisico_revenda",
                        sinal_esperado="demanda", ativo=True))
    session.commit()
    run_sweep(session, CFG, live=False)


def test_health():
    assert client.get("/health").json() == {"ok": True}


def test_reverso_tiktok_extrai_hashtags_preco_e_intencao(session):
    c = _admin_client(session)
    r = c.get("/reverso/tiktok?url=https://tiktok.com/@x/video/123&dry=true")
    assert r.status_code == 200
    body = r.json()
    assert "apostila" in body["hashtags_encontradas"]
    assert body["preco_detectado"]
    assert body["n_comentarios_intencao"] >= 1
    assert body["creditos_gastos"] >= 1
    # LGPD: comentário só com texto, sem nickname/uid
    assert isinstance(body["comentarios_intencao"], list)


def test_reverso_tiktok_exige_login_admin(session):
    assert client.get("/reverso/tiktok?url=https://tiktok.com/@x/video/1&dry=true").status_code == 401


def test_reverso_tiktok_exige_url(session):
    c = _admin_client(session)
    r = c.get("/reverso/tiktok?url=  &dry=true")
    assert r.status_code == 400


def test_reverso_tiktok_grava_e_lista_historico(session):
    c = _admin_client(session)
    r = c.get("/reverso/tiktok?url=https://tiktok.com/@x/video/123&dry=true")
    hid = r.json()["id"]

    hist = c.get("/reverso/historico").json()["historico"]
    assert any(h["id"] == hid and h["fonte"] == "tiktok" and "apostila" in h["hashtags_encontradas"]
              for h in hist)

    assert c.delete(f"/reverso/historico/{hid}").status_code == 200
    hist2 = c.get("/reverso/historico").json()["historico"]
    assert not any(h["id"] == hid for h in hist2)


def test_reverso_meta_extrai_e_grava_historico(session):
    c = _admin_client(session)
    r = c.get("/reverso/meta?url=https://facebook.com/ads/library/?id=123&dry=true")
    assert r.status_code == 200
    body = r.json()
    assert body["fonte"] == "meta"
    assert "moldes" in [h.lower() for h in body["hashtags_encontradas"]] or body["preco_detectado"]
    assert body["dias_ativos"] == 24
    assert body["ativo"] is True
    assert body["digital_confirmado"] is True

    hist = c.get("/reverso/historico?fonte=meta").json()["historico"]
    assert any(h["id"] == body["id"] and h["fonte"] == "meta" for h in hist)
    # filtro por fonte não mistura com tiktok
    assert all(h["fonte"] == "meta" for h in hist)


def test_reverso_meta_exige_url(session):
    c = _admin_client(session)
    r = c.get("/reverso/meta?url=  &dry=true")
    assert r.status_code == 400


def test_reverso_tiktok_erro_na_busca_nao_quebra_o_servidor(session, monkeypatch):
    import app.scrapecreators as sc

    def _boom(self, url):
        raise RuntimeError("scrapecreators fora do ar")

    monkeypatch.setattr(sc.DryRunClient, "video_info", _boom)
    c = _admin_client(session)
    r = c.get("/reverso/tiktok?url=https://tiktok.com/@x/video/1&dry=true")
    assert r.status_code == 502
    assert "detail" in r.json()


def test_reverso_meta_erro_na_busca_nao_quebra_o_servidor(session, monkeypatch):
    import app.scrapecreators as sc

    def _boom(self, url):
        raise RuntimeError("anúncio não existe mais")

    monkeypatch.setattr(sc.DryRunClient, "ad_details", _boom)
    c = _admin_client(session)
    r = c.get("/reverso/meta?url=https://facebook.com/ads/library/?id=1&dry=true")
    assert r.status_code == 502
    assert "detail" in r.json()


def test_termos_sugeridos_cria_lista_e_apaga(session):
    c = _admin_client(session)
    r = c.post("/termos-sugeridos", json={"termo": "moldes de tricô", "fonte": "tiktok",
                                          "nota": "vi um produto parecido validado"})
    assert r.status_code == 200
    tid = r.json()["id"]

    lst = c.get("/termos-sugeridos").json()["termos"]
    assert any(t["id"] == tid and t["termo"] == "moldes de tricô" for t in lst)

    assert c.delete(f"/termos-sugeridos/{tid}").status_code == 200
    lst2 = c.get("/termos-sugeridos").json()["termos"]
    assert not any(t["id"] == tid for t in lst2)


def test_termos_sugeridos_exige_termo_e_valida_fonte(session):
    c = _admin_client(session)
    assert c.post("/termos-sugeridos", json={"termo": "  "}).status_code == 400
    assert c.post("/termos-sugeridos", json={"termo": "x", "fonte": "invalida"}).status_code == 400
    # sem "fonte" cai no default "geral"
    r = c.post("/termos-sugeridos", json={"termo": "papelaria vintage"})
    assert r.status_code == 200 and r.json()["fonte"] == "geral"


def test_termos_negativos_cria_lista_e_apaga(session):
    c = _admin_client(session)
    r = c.post("/termos-negativos", json={"termo": "especialista", "fonte": "meta"})
    assert r.status_code == 200
    body = r.json()
    tid = body["id"]
    assert body["origem"] == "manual"
    assert body["ativo"] is True

    lst = c.get("/termos-negativos").json()["termos"]
    assert any(t["id"] == tid and t["termo"] == "especialista" for t in lst)

    assert c.delete(f"/termos-negativos/{tid}").status_code == 200
    lst2 = c.get("/termos-negativos").json()["termos"]
    assert not any(t["id"] == tid for t in lst2)


def test_termos_negativos_exige_termo_e_valida_fonte(session):
    c = _admin_client(session)
    assert c.post("/termos-negativos", json={"termo": "  "}).status_code == 400
    assert c.post("/termos-negativos", json={"termo": "x", "fonte": "invalida"}).status_code == 400
    # sem "fonte" cai no default "todas"
    r = c.post("/termos-negativos", json={"termo": "professor doutor"})
    assert r.status_code == 200 and r.json()["fonte"] == "todas"


def test_termos_negativos_reenviar_reativa_em_vez_de_duplicar(session):
    from app.models import TermoNegativo

    c = _admin_client(session)
    tid = c.post("/termos-negativos", json={"termo": "especialista", "fonte": "meta"}).json()["id"]
    session.execute(
        TermoNegativo.__table__.update().where(TermoNegativo.id == tid).values(ativo=False)
    )
    session.commit()
    # cadastrar o mesmo termo+fonte de novo reativa em vez de duplicar (unique termo+fonte)
    r = c.post("/termos-negativos", json={"termo": "especialista", "fonte": "meta"})
    assert r.status_code == 200
    assert r.json()["id"] == tid
    assert r.json()["ativo"] is True
    assert len(c.get("/termos-negativos").json()["termos"]) == 1


def test_listar_produtos_ordena_por_views(session):
    _seed_and_sweep(session)
    c = _admin_client(session)
    r = c.get("/produtos?limit=10")  # default sort=views (viralização)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    views = [p["engajamento"]["views"] for p in body["produtos"]]
    assert views == sorted(views, reverse=True)  # mais viral primeiro
    # sort=score volta a ordenar por score
    scores = [p["score"] for p in c.get("/produtos?sort=score").json()["produtos"]]
    assert scores == sorted(scores, reverse=True)
    # LGPD: sem nick nos comentários
    assert "comentarios_intencao" in body["produtos"][0]


def test_produtos_exige_login_admin(session):
    _seed_and_sweep(session)
    assert client.get("/produtos").status_code == 401


def test_produtos_filtra_por_idioma_default_pt(session):
    _seed_and_sweep(session)
    c = _admin_client(session)
    # todos os posts da fixture dry-run são pt: default idioma=pt não esconde nada
    body = c.get("/produtos").json()
    assert body["total"] >= 1
    # marca um produto (não um post qualquer — maturação pode ter deixado post sem
    # Score/Produto no banco, que nunca apareceria em /produtos de qualquer jeito)
    # como es_en manualmente e confirma que o filtro default o esconde
    alvo_id = body["produtos"][0]["post_id"]
    from app.db import SessionLocal
    from app.models import Post
    s2 = SessionLocal()
    post = s2.get(Post, alvo_id)
    post.idioma = "es_en"
    s2.commit()
    s2.close()
    ids_pt = {p["post_id"] for p in c.get("/produtos?idioma=pt").json()["produtos"]}
    ids_all = {p["post_id"] for p in c.get("/produtos?idioma=all").json()["produtos"]}
    assert alvo_id not in ids_pt
    assert alvo_id in ids_all


def test_detalhe_404(session):
    _seed_and_sweep(session)
    c = _admin_client(session)
    assert c.get("/produtos/nao_existe").status_code == 404


def test_feedback_cria_atualiza_e_aparece_em_produtos(session):
    _seed_and_sweep(session)
    c = _admin_client(session)
    post_id = c.get("/produtos").json()["produtos"][0]["post_id"]

    r = c.post(f"/produtos/{post_id}/feedback",
              json={"avaliacao": "negativo", "comentario": "curso com especialista"})
    assert r.status_code == 200
    assert r.json() == {"post_id": post_id, "avaliacao": "negativo",
                        "comentario": "curso com especialista"}

    # aparece embutido na listagem (voto do admin logado)
    item = next(p for p in c.get("/produtos").json()["produtos"] if p["post_id"] == post_id)
    assert item["feedback"] == {"avaliacao": "negativo", "comentario": "curso com especialista"}
    # e no detalhe
    assert c.get(f"/produtos/{post_id}").json()["feedback"]["avaliacao"] == "negativo"

    # reenviar pro mesmo post ATUALIZA o voto, não duplica
    r2 = c.post(f"/produtos/{post_id}/feedback", json={"avaliacao": "positivo"})
    assert r2.status_code == 200
    assert r2.json()["avaliacao"] == "positivo"
    assert r2.json()["comentario"] is None
    from app.models import Feedback
    assert session.query(Feedback).filter_by(post_id=post_id).count() == 1


def test_feedback_valida_avaliacao_e_produto_existente(session):
    _seed_and_sweep(session)
    c = _admin_client(session)
    post_id = c.get("/produtos").json()["produtos"][0]["post_id"]
    assert c.post(f"/produtos/{post_id}/feedback", json={"avaliacao": "meh"}).status_code == 400
    assert c.post("/produtos/nao_existe/feedback", json={"avaliacao": "positivo"}).status_code == 404


def test_feedback_apagar_remove_voto(session):
    _seed_and_sweep(session)
    c = _admin_client(session)
    post_id = c.get("/produtos").json()["produtos"][0]["post_id"]
    c.post(f"/produtos/{post_id}/feedback", json={"avaliacao": "positivo"})
    assert c.delete(f"/produtos/{post_id}/feedback").status_code == 200
    item = next(p for p in c.get("/produtos").json()["produtos"] if p["post_id"] == post_id)
    assert item["feedback"] is None


def test_feedback_exige_login_admin(session):
    _seed_and_sweep(session)
    # anônimo (sem cookie/header CSRF) barra em 403 antes mesmo do require_admin
    r = client.post("/produtos/x/feedback", json={"avaliacao": "positivo"})
    assert r.status_code == 403


def test_custo_dia(session):
    _seed_and_sweep(session)
    c = _admin_client(session)
    body = c.get("/custo/dia").json()
    assert "dias" in body and "credit_usd" in body
    assert body["dias"]  # pelo menos 1 dia com requests de coleta


def _wait_run(c, run_id, tries=60):
    import time
    for _ in range(tries):
        st = c.get(f"/varredura/{run_id}").json()
        if st["status"] in ("done", "error", "interrupted"):
            return st
        time.sleep(0.1)
    return c.get(f"/varredura/{run_id}").json()


def test_varredura_dispara_assincrono(session):
    c = _admin_client(session)
    session.add(Keyword(termo="achadinhos", tipo="top", mercado="fisico_revenda",
                        sinal_esperado="demanda", ativo=True))
    session.commit()
    r = c.post("/varredura?dry=true")  # dry = gasto zero
    assert r.status_code == 200
    st = _wait_run(c, r.json()["run_id"])
    assert st["status"] == "done"
    assert st["summary"]["sobreviventes"] >= 1
    assert c.get("/produtos").json()["total"] >= 1  # populou o dashboard


def test_varreduras_lista_e_filtro_por_run(session):
    c = _admin_client(session)
    session.add(Keyword(termo="achadinhos", tipo="top", mercado="fisico_revenda",
                        sinal_esperado="demanda", ativo=True))
    session.commit()
    r = c.post("/varredura?dry=true")
    rid = r.json()["run_id"]
    _wait_run(c, rid)
    # a varredura aparece na lista com contagem de produtos
    vs = c.get("/varreduras").json()["varreduras"]
    assert any(v["id"] == rid and v["n_produtos"] >= 1 for v in vs)
    # /produtos?run=<id> traz só os daquela varredura
    assert c.get(f"/produtos?run={rid}").json()["total"] >= 1
    # latest = a última varredura
    assert c.get("/produtos?run=latest").json()["total"] >= 1


def test_varredura_exige_login_admin(session):
    # middleware de CSRF roda antes da checagem de login — sem cookie/header CSRF
    # nenhum (visitante anônimo), barra em 403 antes mesmo de chegar no require_admin
    assert client.post("/varredura?dry=true").status_code == 403


def test_csrf_bloqueia_post_sem_header_mesmo_logado(session):
    # admin logado (cookie de sessão válido) mas SEM o header X-CSRF-Token —
    # front e back são domínios diferentes (Vercel/Railway), então o cookie de
    # sessão sozinho não basta: um site atacante também conseguiria mandar o
    # cookie (SameSite=None), só não consegue LER o cookie CSRF pra ecoar no header
    session.add(Usuario(email="admin2@teste.com", senha_hash=hash_senha("senhaadmin123"),
                        is_admin=True, ativo=True))
    session.commit()
    c = TestClient(app)
    r = c.post("/auth/login", json={"email": "admin2@teste.com", "senha": "senhaadmin123"})
    assert r.status_code == 200
    assert "lowspy_csrf" in r.cookies  # cookie CSRF veio junto do login

    # sem header algum -> barrado
    assert c.post("/varredura?dry=true").status_code == 403
    # header com valor errado (não bate com o cookie) -> barrado também
    r2 = c.post("/varredura?dry=true", headers={"X-CSRF-Token": "valor-forjado-qualquer"})
    assert r2.status_code == 403
    # header certo (ecoando o cookie) -> passa
    c.headers["X-CSRF-Token"] = c.cookies["lowspy_csrf"]
    assert c.post("/varredura?dry=true").status_code == 200


def test_csrf_nao_exigido_em_get(session):
    c = _admin_client(session)
    del c.headers["X-CSRF-Token"]  # GET não muda estado — não deveria precisar
    assert c.get("/produtos").status_code == 200


def test_varredura_exige_role_admin_nao_so_login(session):
    # usuário comum logado (não-admin) não pode disparar varredura — 403, não 401
    session.add(Usuario(email="cliente@teste.com", senha_hash=hash_senha("senhacliente123"),
                        is_admin=False, ativo=True))
    session.commit()
    c = TestClient(app)
    r = c.post("/auth/login", json={"email": "cliente@teste.com", "senha": "senhacliente123"})
    assert r.status_code == 200
    c.headers["X-CSRF-Token"] = c.cookies["lowspy_csrf"]  # senão barra no CSRF antes de chegar no role
    assert c.post("/varredura?dry=true").status_code == 403
