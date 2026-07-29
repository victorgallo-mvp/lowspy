"""Área do assinante — feed diário. Versão simplificada dos produtos, sem os
campos operacionais do admin (score_componentes, termo_origem, mercado interno).

Desenho confirmado (v1, antes do Stripe/planos de verdade): pool = os 50
produtos mais recentes (mesma janela que os pipelines já usam via
caps.target_produtos), sem filtro por usuário. Cada usuário logado sorteia uma
amostra do tamanho do limite do plano — repetir entre usuários é aceitável (não
tem problema dois usuários verem o mesmo produto no mesmo dia); a randomização
é só pra não todo mundo ver exatamente a mesma ordem.
"""
from __future__ import annotations

import random

from fastapi import APIRouter, Depends
from sqlalchemy import select

from .auth import get_current_user
from .db import get_db
from .models import Post, Produto, Usuario

router = APIRouter(prefix="/feed", tags=["feed"])

PLANO_LIMITES = {"free": 5, "pro": 30, "business": 50}
POOL_MAXIMO = 50


def _serialize_consumidor(pr: Produto, post: Post) -> dict:
    return {
        "post_id": post.id,
        "fonte": post.fonte,
        "produto": pr.produto or (post.descricao or "")[:120],
        "preco": pr.preco,
        "nicho": pr.nicho,
        "url": post.url,
        "cover_url": post.cover_url,
        "sinal": pr.sinal,
        "novo": bool(pr.novo),
    }


@router.get("")
def feed(db=Depends(get_db), usuario: Usuario = Depends(get_current_user)):
    limite = PLANO_LIMITES.get(usuario.plano, PLANO_LIMITES["free"])
    pool = db.execute(
        select(Produto, Post)
        .join(Post, Produto.post_id == Post.id)
        .order_by(Produto.created_at.desc())
        .limit(POOL_MAXIMO)
    ).all()
    amostra = random.sample(pool, min(limite, len(pool)))
    return {
        "plano": usuario.plano,
        "limite": limite,
        "total_pool": len(pool),
        "produtos": [_serialize_consumidor(pr, post) for pr, post in amostra],
    }
