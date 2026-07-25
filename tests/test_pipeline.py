from collections import Counter

from app.config import load_config
from app.models import Comment, CostLog, Keyword, Post, Produto, Run, Score
from app.pipeline import ranked_products, run_sweep, run_sweep_meta

CFG = load_config()


def _seed_keyword(session):
    session.add(
        Keyword(termo="achadinhos", tipo="top", mercado="fisico_revenda",
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


def test_run_sweep_dropa_nao_digital(session):
    # fixture tem um post de carro usado (sem nenhum termo de confirmacao_digital na
    # legenda NEM nos comentários) — termo genérico ("Kit"/preço/palavra comum) não
    # prova nada sozinho: a decisão é adiada pro N1 e cai se nada confirmar digital
    session.add(Keyword(termo="kit", tipo="top", mercado="keyword_livre_generico",
                        sinal_esperado="vendedor", ativo=True))
    session.commit()
    r = run_sweep(session, CFG, live=False)
    assert r["nao_digital_dropados"] >= 1
    produtos = session.query(Produto).all()
    posts = [session.get(Post, p.post_id) for p in produtos]
    assert all("corsa" not in (p.descricao or "").lower() for p in posts)


def test_run_sweep_termo_generico_resgata_pelo_comentario(session):
    # termo genérico + legenda sem confirmação NÃO morre no N0: a decisão é adiada
    # pra leitura de comentário. Se um comentário confirmar digital ("tem o pdf?"),
    # o post se salva — vendedor de legenda vazia é comum no nicho
    import json, pathlib, tempfile, shutil
    src = pathlib.Path("fixtures")
    tmp = pathlib.Path(tempfile.mkdtemp())
    try:
        for f in src.glob("*.json"):
            shutil.copy(f, tmp / f.name)
        com = json.loads((tmp / "comments.json").read_text())
        com["comments"][3]["text"] = "tem o pdf? quero"  # confirma digital no comentário
        (tmp / "comments.json").write_text(json.dumps(com))

        import app.pipeline as mod
        from app.scrapecreators import DryRunClient
        original = mod.DryRunClient
        mod.DryRunClient = lambda cb: DryRunClient(cb, fixtures=tmp)
        try:
            session.add(Keyword(termo="kit", tipo="top", mercado="keyword_livre_generico",
                                sinal_esperado="vendedor", ativo=True))
            session.commit()
            r = run_sweep(session, CFG, live=False)
        finally:
            mod.DryRunClient = original
        # o post do Corsa (legenda sem confirmação) é resgatado pelo comentário
        assert r["nao_digital_dropados"] == 0
    finally:
        shutil.rmtree(tmp)


def test_run_sweep_confia_no_termo_de_mercado_digital_curado(session):
    # termo de mercado digital curado (era hashtag confiável) dispensa confirmação
    # na legenda — mesmo o post do "Corsa" (sem palavra de confirmacao_digital)
    # passa, porque o termo de busca já prova o nicho, igual valia pra hashtag
    session.add(Keyword(termo="planilha", tipo="top", mercado="keyword_livre",
                        sinal_esperado="vendedor", ativo=True))
    session.commit()
    r = run_sweep(session, CFG, live=False)
    assert r["nao_digital_dropados"] == 0


def test_run_sweep_prioriza_termos_da_lista_prioridade(session):
    import copy
    cfg = copy.deepcopy(CFG)
    cfg["discovery"]["prioridade"] = ["zzz_prioritario"]
    cfg["discovery"]["orcamento_total"] = 1  # só dá pra 1 request — só o termo prioritário entra
    # "existente" foi cadastrado primeiro (id menor); "zzz_prioritario" depois, mas
    # está na lista de prioridade — precisa vencer mesmo entrando por último no banco
    session.add(Keyword(termo="existente", tipo="top", mercado="formato_digital",
                        sinal_esperado="vendedor", ativo=True))
    session.commit()
    session.add(Keyword(termo="zzz_prioritario", tipo="top", mercado="formato_digital",
                        sinal_esperado="vendedor", ativo=True))
    session.commit()
    r = run_sweep(session, cfg, live=False)
    assert r["requests"]["search_top"] == 1  # teto de orçamento bateu logo após o 1º termo
    # a única CostLog de busca deve ser do termo prioritário, não do "existente"
    log = session.query(CostLog).filter_by(endpoint="search_top").first()
    assert log.params["query"] == "zzz_prioritario"


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
    # 1 termo x 3 ordenações (relevance/most-liked/date-posted) = 3 buscas — o mesmo
    # termo em cada ordenação devolve listas diferentes (multiplicador de oferta)
    assert r["requests"].get("search_top") == 3
    assert r["requests"].get("search_hashtag") is None  # canal de hashtag foi aposentado
    # fixture tem 5 itens (8/87/132/150/214 comentários); o piso de keyword_search é
    # 30 -> os 4 com >=30 comentários entram no funil (mercado curado dispensa
    # confirmação na legenda, então até o post do "Corsa" passa aqui); as 3 ordenações
    # devolvem os MESMOS itens no dry-run -> dedup segura em 4
    assert r["n0_posts"] == 4


def test_run_sweep_ignora_keyword_meta_query(session):
    # meta_query é do pipeline do Meta Ads — não pode vazar pro TikTok mesmo se ativa
    session.add(Keyword(termo="Apenas R$14,90", tipo="meta_query", mercado="meta_precificacao",
                        sinal_esperado="vendedor", ativo=True))
    session.commit()
    r = run_sweep(session, CFG, live=False)
    assert r["total_buscado"] == 0
    assert "search_facebook_ads" not in r["requests"]


def test_run_sweep_para_no_orcamento_total(session):
    import copy
    cfg = copy.deepcopy(CFG)
    cfg["discovery"]["orcamento_total"] = 2  # só dá pra 1 busca + 1 leitura de comentário
    cfg["discovery"]["keyword_search"]["sort_modes"] = ["relevance"]  # isola o teto de gasto
    for termo in ["kw1", "kw2", "kw3"]:
        session.add(Keyword(termo=termo, tipo="top", mercado="keyword_livre",
                            sinal_esperado="vendedor", ativo=True))
    session.commit()
    r = run_sweep(session, cfg, live=False)
    assert r["termos_tentados"] == 1  # teto bateu logo depois do 1º termo — kw2/kw3 nunca tentados
    assert r["requests"]["search_top"] == 1
    assert r["orcamento_usado"] <= 2


def test_run_sweep_busca_todos_os_termos_ativos_quando_ha_orcamento(session):
    import copy
    cfg = copy.deepcopy(CFG)
    for termo in ["planilha", "molde", "apostila"]:
        session.add(Keyword(termo=termo, tipo="top", mercado="keyword_livre",
                            sinal_esperado="vendedor", ativo=True))
    session.commit()
    r = run_sweep(session, cfg, live=False)
    # com orçamento de sobra (padrão 1000), busca é sempre por keyword-livre e cobre
    # TODOS os termos ativos — sem teto de "max_keywords" cortando a fila
    assert r["termos_tentados"] == 3
    assert r["termos_disponiveis"] == 3


def test_run_sweep_segunda_chance_le_mais_uma_pagina(session):
    # quase-aprovado: 1ª página rende 2-3 comentários secos (mínimo é 4) — em vez de
    # descartar, lê MAIS UMA página de comentários (+1 crédito); se os "eu quero" que
    # faltavam estiverem lá, o post se salva
    from app.scrapecreators import DryRunClient
    from app.schemas import CommentSchema

    class DoisPaginasClient(DryRunClient):
        def video_comments(self, url, cursor=None):
            self._spend("video_comments", {"url": url, "cursor": cursor})
            if cursor is None:  # página 1: só 2 secos — não bate o mínimo de 4
                return ([CommentSchema(text="eu quero", cid=f"p1a-{url[-4:]}"),
                         CommentSchema(text="quero!", cid=f"p1b-{url[-4:]}")], 20)
            # página 2: os que faltavam
            return ([CommentSchema(text="Eu quero", cid=f"p2a-{url[-4:]}"),
                     CommentSchema(text="quero pfv", cid=f"p2b-{url[-4:]}")], None)

    import app.pipeline as mod
    original = mod.DryRunClient
    mod.DryRunClient = DoisPaginasClient
    try:
        session.add(Keyword(termo="planilha", tipo="top", mercado="keyword_livre",
                            sinal_esperado="vendedor", ativo=True))
        session.commit()
        r = run_sweep(session, CFG, live=False)
    finally:
        mod.DryRunClient = original
    assert r["segunda_chance_lidas"] >= 1
    assert r["segunda_chance_salvos"] >= 1  # 2+2=4 secos -> demanda confirmada
    assert r["sobreviventes"] >= 1


def test_run_sweep_grava_termo_origem(session):
    session.add(Keyword(termo="apostila", tipo="top", mercado="formato_digital",
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
