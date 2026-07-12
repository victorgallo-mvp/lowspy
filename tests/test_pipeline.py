from app.config import load_config
from app.models import Comment, CostLog, Keyword, Post, Produto, Score
from app.pipeline import ranked_products, run_sweep

CFG = load_config()


def _seed_keyword(session):
    session.add(
        Keyword(termo="achadinhos", tipo="hashtag", mercado="fisico_revenda",
                sinal_esperado="demanda", ativo=True)
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


def test_ranked_products_orders_by_score(session):
    _seed_keyword(session)
    run_sweep(session, CFG, live=False)
    ranked = ranked_products(session, limit=10)
    assert ranked
    scores = [p["score"] for p in ranked]
    assert scores == sorted(scores, reverse=True)  # melhor primeiro
    # LGPD: comentários de intenção sem nickname/uid
    assert "comentarios_intencao" in ranked[0]
