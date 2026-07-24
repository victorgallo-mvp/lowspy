from app.config import load_config
from app.extract import run_extraction
from app.models import CostLog, Keyword, Produto
from app.pipeline import run_sweep

CFG = load_config()


def _seed_and_sweep(session):
    session.add(Keyword(termo="achadinhos", tipo="top", mercado="fisico_revenda",
                        sinal_esperado="demanda", ativo=True))
    session.commit()
    run_sweep(session, CFG, live=False)


def _fake_extractor(prompts, model):
    # simula o Haiku Batch: devolve JSON estruturado por custom_id
    results = {cid: {"produto": "Planilha financeira", "preco": "R$10", "nicho": "financas"}
               for cid, _ in prompts}
    return results, 1200, 300  # tokens fake


def test_extraction_fills_produto_and_logs_cost(session):
    _seed_and_sweep(session)
    pend_before = session.query(Produto).filter(Produto.produto.is_(None)).count()
    assert pend_before >= 1

    summary = run_extraction(session, CFG, extractor=_fake_extractor)
    assert summary["extracted"] >= 1
    assert session.query(Produto).filter(Produto.produto.is_(None)).count() == 0
    # nicho/preço preenchidos
    pr = session.query(Produto).first()
    assert pr.produto and pr.nicho == "financas"
    # custo Haiku logado
    assert session.query(CostLog).filter_by(endpoint="haiku_batch").count() == 1
    assert summary["cost_usd"] > 0


def test_extraction_idempotent(session):
    _seed_and_sweep(session)
    run_extraction(session, CFG, extractor=_fake_extractor)
    # re-run: nada pendente → não chama o extractor de novo
    def _boom(prompts, model):
        raise AssertionError("não deveria reprocessar")
    summary = run_extraction(session, CFG, extractor=_boom)
    assert summary["pending"] == 0
