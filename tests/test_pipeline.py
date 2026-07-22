from collections import Counter

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


def test_run_sweep_usa_search_top_para_keyword_livre(session):
    session.add(Keyword(termo="planilha", tipo="top", mercado="keyword_livre",
                        sinal_esperado="vendedor", ativo=True))
    session.commit()
    r = run_sweep(session, CFG, live=False)
    assert r["requests"].get("search_top") == 1
    assert r["requests"].get("search_hashtag") is None  # só a keyword_livre estava ativa
    # fixture tem 4 itens (8/87/132/214 comentários); o piso de keyword_search é 100 ->
    # só os 2 com >=100 comentários entram no funil
    assert r["n0_posts"] == 2


def test_run_sweep_ignora_keyword_meta_query(session):
    # meta_query é do pipeline do Meta Ads — não pode vazar pro TikTok mesmo se ativa
    session.add(Keyword(termo="Apenas R$14,90", tipo="meta_query", mercado="meta_precificacao",
                        sinal_esperado="vendedor", ativo=True))
    session.commit()
    r = run_sweep(session, CFG, live=False)
    assert r["total_buscado"] == 0
    assert "search_facebook_ads" not in r["requests"]


def test_run_sweep_respeita_max_keywords_da_keyword_livre(session):
    import copy
    cfg = copy.deepcopy(CFG)
    cfg["discovery"]["keyword_search"]["max_keywords"] = 2
    for termo in ["planilha", "molde", "apostila"]:
        session.add(Keyword(termo=termo, tipo="top", mercado="keyword_livre",
                            sinal_esperado="vendedor", ativo=True))
    session.commit()
    r = run_sweep(session, cfg, live=False)
    assert r["requests"]["search_top"] == 2  # das 3 ativas, só 2 (teto) foram buscadas


def test_run_sweep_grava_termo_origem(session):
    session.add(Keyword(termo="apostila", tipo="hashtag", mercado="formato_digital",
                        sinal_esperado="vendedor", ativo=True))
    session.commit()
    run_sweep(session, CFG, live=False)
    produtos = session.query(Produto).all()
    assert produtos
    posts = [session.get(Post, p.post_id) for p in produtos]
    assert all(post.termo_origem == "apostila" for post in posts)


def test_run_sweep_meta_usa_dias_ativos_como_demanda(session):
    _seed_keyword_meta(session)
    r = run_sweep_meta(session, CFG, live=False)
    # fixture: itens 7/8 (20/22d, página "Ateliê Digital Moldes") + item 3 (21d, outra
    # página) ficam dentro da banda 10-25; item 1 (27d) agora é dropado por passar do
    # teto; item 2 (4d) é curto; item 4 é físico; item 5 é serviço local; item 6 sem texto
    assert r["fonte"] == "meta"
    assert r["curto_dropados"] >= 1
    assert r["longo_dropados"] >= 1  # item de 27 dias — acima do teto da banda (25)
    assert r["fisico_dropados"] >= 1
    assert r["servico_local_dropados"] >= 1
    assert r["sem_texto_dropados"] >= 1
    # distribuição dos descartados por tempo curto (diagnóstico): fixture tem 1 anúncio
    # de 4 dias ativos, então min == mediana == max == 4
    assert r["curto_dias_stats"] == {"min": 4, "mediana": 4, "max": 4}
    assert r["sobreviventes"] == 3
    produtos = session.query(Produto).filter(Produto.mercado.like("meta_%")).all()
    assert len(produtos) == 3
    posts = {p.post_id: session.get(Post, p.post_id) for p in produtos}
    assert all(post.fonte == "meta" for post in posts.values())
    assert all(10 <= post.total_active_time <= 25 for post in posts.values())


def test_run_sweep_meta_conta_anuncios_do_anunciante(session):
    _seed_keyword_meta(session)
    r = run_sweep_meta(session, CFG, live=False)
    produtos = session.query(Produto).filter(Produto.mercado.like("meta_%")).all()
    posts = [session.get(Post, p.post_id) for p in produtos]
    # todos os sobreviventes ganham a contagem (opção completa: 1 request por anunciante)
    assert all(post.anunciante_total_ads is not None for post in posts)
    # 2 sobreviventes são da mesma página ("Ateliê Digital Moldes") — só 1 request, cacheado
    assert r["requests"]["company_ads_count"] == 2  # 2 páginas distintas entre os 3 sobreviventes


def test_run_sweep_meta_nao_repete_pagina_alem_do_limite(session):
    _seed_keyword_meta(session)
    run_sweep_meta(session, CFG, live=False)
    produtos = session.query(Produto).filter(Produto.mercado.like("meta_%")).all()
    posts = [session.get(Post, p.post_id) for p in produtos]
    por_pagina = Counter(p.author_id for p in posts)  # author_id carrega page_id no Meta
    assert por_pagina["610000000000001"] == 2  # "Ateliê Digital Moldes" tinha 3 válidos, capado em 2
    assert max(por_pagina.values()) <= 2


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
