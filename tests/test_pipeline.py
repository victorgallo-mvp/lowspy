from app.config import load_config
from app.models import Comment, CostLog, Keyword, Post, Produto, Run, Score
from app.pipeline import ranked_products, run_sweep, run_sweep_meta

CFG = load_config()


def _seed_keyword(session):
    session.add(
        Keyword(termo="achadinhos", tipo="hashtag", mercado="fisico_revenda",
                sinal_esperado="demanda", ativo=True)
    )
    session.commit()


def _seed_keyword_meta(session):
    session.add(
        Keyword(termo="Apenas R$14,90", tipo="meta_query", mercado="meta_precificacao",
                sinal_esperado="vendedor", ativo=True)
    )
    session.commit()


def test_sweep_dry_run_persists_and_scores(session):
    _seed_keyword(session)
    summary = run_sweep(session, CFG, live=False)

    assert summary["modo"] == "dry-run"
    assert session.query(Post).count() > 0
    assert session.query(Comment).count() > 0
    assert session.query(Score).count() > 0
    assert session.query(CostLog).count() > 0  # log de custo por chamada
    # a fixture tem comentários de intenção → pelo menos 1 produto sobrevivente
    assert session.query(Produto).count() >= 1
    assert summary["sobreviventes"] >= 1


def test_sweep_is_idempotent(session):
    _seed_keyword(session)
    run_sweep(session, CFG, live=False)
    posts_1 = session.query(Post).count()
    scores_1 = session.query(Score).count()

    run_sweep(session, CFG, live=False)  # re-varredura
    assert session.query(Post).count() == posts_1  # não duplica post
    assert session.query(Score).count() == scores_1  # 1 score por post


def test_run_id_separa_por_varredura(session):
    import copy
    cfg = copy.deepcopy(CFG)
    cfg["discovery"]["pular_vistos"] = False  # aqui testamos a migração no re-find
    _seed_keyword(session)
    r1 = Run(status="running", mode="dry-run")
    session.add(r1); session.commit()
    run_sweep(session, cfg, live=False, run_id=r1.id)
    assert session.query(Produto).count() >= 1
    assert all(p.run_id == r1.id for p in session.query(Produto).all())

    # nova varredura re-acha os mesmos posts → produtos migram pro run atual
    r2 = Run(status="running", mode="dry-run")
    session.add(r2); session.commit()
    run_sweep(session, cfg, live=False, run_id=r2.id)
    assert all(p.run_id == r2.id for p in session.query(Produto).all())


def test_pular_vistos_novidade(session):
    _seed_keyword(session)
    run_sweep(session, CFG, live=False)  # 1ª: tudo novo
    prod_1 = session.query(Produto).count()
    assert prod_1 >= 1
    # 2ª com pular_vistos (default): re-acha os mesmos → pula → não cria novos
    r = run_sweep(session, CFG, live=False)
    assert r["vistos_pulados"] >= 1
    assert session.query(Produto).count() == prod_1  # não duplicou


def test_run_sweep_meta_usa_dias_ativos_como_demanda(session):
    _seed_keyword_meta(session)
    r = run_sweep_meta(session, CFG, live=False)
    # fixture: 2 anúncios digitais >=15 dias ativos sobrevivem; 1 curto (4d) e
    # 1 físico (frete/correios) são dropados mesmo com 18 dias ativos
    assert r["fonte"] == "meta"
    assert r["curto_dropados"] >= 1
    assert r["fisico_dropados"] >= 1
    # distribuição dos descartados por tempo curto (diagnóstico): fixture tem 1 anúncio
    # de 4 dias ativos, então min == mediana == max == 4
    assert r["curto_dias_stats"] == {"min": 4, "mediana": 4, "max": 4}
    assert r["sobreviventes"] == 2
    produtos = session.query(Produto).filter(Produto.mercado.like("meta_%")).all()
    assert len(produtos) == 2
    posts = {p.post_id: session.get(Post, p.post_id) for p in produtos}
    assert all(post.fonte == "meta" for post in posts.values())
    assert all(post.total_active_time >= 15 for post in posts.values())


def test_run_sweep_meta_idempotente(session):
    _seed_keyword_meta(session)
    run_sweep_meta(session, CFG, live=False)
    n1 = session.query(Produto).count()
    run_sweep_meta(session, CFG, live=False)  # re-varredura: pular_vistos evita duplicar
    assert session.query(Produto).count() == n1


def test_ranked_products_orders_by_score(session):
    _seed_keyword(session)
    run_sweep(session, CFG, live=False)
    ranked = ranked_products(session, limit=10)
    assert ranked
    scores = [p["score"] for p in ranked]
    assert scores == sorted(scores, reverse=True)  # melhor primeiro
    # LGPD: comentários de intenção sem nickname/uid
    assert "comentarios_intencao" in ranked[0]
