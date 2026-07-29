"""Página administrativa — visão geral do banco de mineração e lista de usuários.
Tudo aqui exige require_admin (não é superfície de cliente pagante)."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from .auth import require_admin
from .db import get_db
from .models import Post, Produto, Run, Usuario

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/usuarios")
def listar_usuarios(db=Depends(get_db), _admin: Usuario = Depends(require_admin)):
    usuarios = db.execute(select(Usuario).order_by(Usuario.created_at.desc())).scalars().all()
    return {
        "usuarios": [
            {
                "id": u.id,
                "email": u.email,
                "plano": u.plano,
                "is_admin": u.is_admin,
                "ativo": u.ativo,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in usuarios
        ]
    }


@router.get("/overview")
def overview(db=Depends(get_db), _admin: Usuario = Depends(require_admin)):
    total_posts = db.execute(select(func.count(Post.id))).scalar() or 0
    posts_por_fonte = dict(db.execute(select(Post.fonte, func.count()).group_by(Post.fonte)).all())

    total_produtos = db.execute(select(func.count(Produto.id))).scalar() or 0
    produtos_por_fonte = dict(db.execute(
        select(Post.fonte, func.count(Produto.id))
        .join(Post, Produto.post_id == Post.id)
        .group_by(Post.fonte)
    ).all())
    breadth_mercado = dict(db.execute(
        select(Produto.mercado, func.count()).group_by(Produto.mercado)
    ).all())

    # crescimento de produtos novos, últimos 14 dias (corte por data, não hora)
    limite = datetime.utcnow() - timedelta(days=14)
    rows = db.execute(
        select(Produto.created_at).where(Produto.created_at >= limite)
    ).all()
    por_dia: dict[str, int] = {}
    for (created_at,) in rows:
        dia = created_at.date().isoformat() if created_at else "sem_data"
        por_dia[dia] = por_dia.get(dia, 0) + 1
    crescimento_14d = sorted(por_dia.items())

    usuarios_por_plano = dict(db.execute(
        select(Usuario.plano, func.count()).where(Usuario.ativo == True)  # noqa: E712
        .group_by(Usuario.plano)
    ).all())
    total_usuarios = sum(usuarios_por_plano.values())

    ultimas_varreduras = {}
    for fonte in ("tiktok", "meta"):
        run = db.execute(
            select(Run).where(Run.fonte == fonte).order_by(Run.id.desc())
        ).scalars().first()
        if run:
            ultimas_varreduras[fonte] = {
                "id": run.id,
                "status": run.status,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            }

    return {
        "total_posts": total_posts,
        "posts_por_fonte": posts_por_fonte,
        "total_produtos": total_produtos,
        "produtos_por_fonte": produtos_por_fonte,
        "breadth_mercado": breadth_mercado,
        "crescimento_14d": crescimento_14d,
        "usuarios_por_plano": usuarios_por_plano,
        "total_usuarios": total_usuarios,
        "ultimas_varreduras": ultimas_varreduras,
    }
