"""Fase 2 — Nível 2: Haiku 4.5 via Batch API (−50%) preenche produto/preço/nicho.

Idempotente: só processa Produto sem `produto`. O passo Anthropic é injetável
(`extractor`) pra testar sem gastar API. Custo de token vai pra cost_log.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable, Optional

from sqlalchemy import select

from . import config
from .db import SessionLocal, init_db
from .models import CostLog, Post, Produto

LOG = logging.getLogger("extract")

# extractor(prompts, model) -> (results_por_custom_id, input_tokens, output_tokens)
Extractor = Callable[[list, str], "tuple[dict, int, int]"]

_SCHEMA = (
    "Extraia do post do TikTok um JSON com as chaves exatas: "
    '"produto" (string curta), "preco" (string ou null), '
    '"nicho" (string curta). Responda SÓ com o JSON, sem texto extra.'
)


def build_prompt(desc: str, intent_comments: list[str]) -> str:
    comments = "\n".join("- " + c for c in intent_comments[:15])
    return f"{_SCHEMA}\n\nLEGENDA:\n{desc}\n\nCOMENTÁRIOS DE INTENÇÃO:\n{comments}"


def _anthropic_extractor(prompts: list, model: str) -> "tuple[dict, int, int]":
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = anthropic.Anthropic()
    reqs = [
        Request(
            custom_id=cid,
            params=MessageCreateParamsNonStreaming(
                model=model, max_tokens=400, messages=[{"role": "user", "content": prompt}]
            ),
        )
        for cid, prompt in prompts
    ]
    batch = client.messages.batches.create(requests=reqs)
    while True:
        b = client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        LOG.info("batch %s: %s", b.id, b.processing_status)
        time.sleep(10)

    out: dict[str, Any] = {}
    itok = otok = 0
    for res in client.messages.batches.results(batch.id):
        if res.result.type == "succeeded":
            m = res.result.message
            itok += m.usage.input_tokens
            otok += m.usage.output_tokens
            text = next((bl.text for bl in m.content if bl.type == "text"), "")
            try:
                out[res.custom_id] = json.loads(text)
            except json.JSONDecodeError:
                out[res.custom_id] = {"raw": text}
    return out, itok, otok


def run_extraction(session, cfg: dict, limit: int = 100,
                   extractor: Optional[Extractor] = None) -> dict[str, Any]:
    model = os.getenv("MODEL_TIER2") or cfg["pricing"]["model_tier2"]
    pending = session.execute(
        select(Produto).where(Produto.produto.is_(None)).limit(limit)
    ).scalars().all()
    if not pending:
        return {"pending": 0, "extracted": 0}

    prompts = []
    for pr in pending:
        post = session.get(Post, pr.post_id)
        intent = [c.texto for c in post.comentarios if c.is_intent]
        prompts.append((pr.post_id, build_prompt(post.descricao, intent)))

    extractor = extractor or _anthropic_extractor
    results, itok, otok = extractor(prompts, model)

    extracted = 0
    for pr in pending:
        r = results.get(pr.post_id) or {}
        produto = str(r.get("produto") or "").strip()[:200]
        if produto:
            pr.produto = produto
            extracted += 1
        if r.get("preco"):
            pr.preco = str(r["preco"])[:40]
        nicho = str(r.get("nicho") or "").strip()[:80]
        if nicho:
            pr.nicho = nicho

    p = cfg["pricing"]
    usd = (itok / 1e6) * p["haiku_batch_input_usd_per_mtok"] + (otok / 1e6) * p[
        "haiku_batch_output_usd_per_mtok"
    ]
    session.add(
        CostLog(
            endpoint="haiku_batch",
            params={"model": model, "input_tokens": itok, "output_tokens": otok,
                    "usd": round(usd, 6)},
            credits_remaining=None,
        )
    )
    session.commit()
    return {
        "pending": len(pending),
        "extracted": extracted,
        "input_tokens": itok,
        "output_tokens": otok,
        "cost_usd": round(usd, 6),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_db()
    session = SessionLocal()
    try:
        summary = run_extraction(session, config.load_config())
        print(summary)
    finally:
        session.close()


if __name__ == "__main__":
    main()
