"""Rotas de autenticação — cadastro/login/logout via cookie httpOnly (sessão JWT)."""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select

from . import config
from .auth import (
    COOKIE_NAME,
    CSRF_COOKIE_NAME,
    criar_token,
    gerar_csrf_token,
    get_current_user_optional,
    hash_senha,
    verificar_senha,
)
from .db import get_db
from .email_service import get_email_service
from .models import Usuario

LOG = logging.getLogger("auth")
router = APIRouter(prefix="/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _set_session_cookies(response: Response, usuario_id: int) -> None:
    # o frontend fala com o backend via proxy same-origin (next.config.js rewrite
    # /api/* -> Railway) — o navegador só vê o domínio do próprio Vercel, nunca
    # cross-site, então SameSite=Lax basta (e evita o bloqueio de cookie de
    # terceiro do Safari/ITP, que barrava mesmo com SameSite=None antes do proxy).
    response.set_cookie(
        key=COOKIE_NAME,
        value=criar_token(usuario_id),
        max_age=config.JWT_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite="lax",
    )
    # cookie CSRF: de propósito NÃO httpOnly — o frontend lê e ecoa no header
    # X-CSRF-Token em toda chamada que muda estado (ver middleware em api.py)
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=gerar_csrf_token(),
        max_age=config.JWT_EXPIRE_MINUTES * 60,
        httponly=False,
        secure=config.COOKIE_SECURE,
        samesite="lax",
    )


def _usuario_publico(u: Usuario) -> dict:
    return {"id": u.id, "email": u.email, "is_admin": u.is_admin}


@router.post("/registro")
def registro(payload: dict, response: Response, db=Depends(get_db)):
    email = (payload.get("email") or "").strip().lower()
    senha = payload.get("senha") or ""
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="e-mail inválido")
    if len(senha) < 8:
        raise HTTPException(status_code=400, detail="senha precisa de pelo menos 8 caracteres")
    if db.execute(select(Usuario).where(Usuario.email == email)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="e-mail já cadastrado")

    usuario = Usuario(email=email, senha_hash=hash_senha(senha), is_admin=False, ativo=True)
    db.add(usuario)
    db.commit()

    get_email_service().enviar(email, "boas_vindas", {"email": email})
    _set_session_cookies(response, usuario.id)
    return _usuario_publico(usuario)


@router.post("/login")
def login(payload: dict, response: Response, db=Depends(get_db)):
    email = (payload.get("email") or "").strip().lower()
    senha = payload.get("senha") or ""
    usuario = db.execute(select(Usuario).where(Usuario.email == email)).scalar_one_or_none()
    if usuario is None or not verificar_senha(senha, usuario.senha_hash):
        raise HTTPException(status_code=401, detail="e-mail ou senha incorretos")
    if not usuario.ativo:
        raise HTTPException(status_code=403, detail="conta desativada")

    _set_session_cookies(response, usuario.id)
    return _usuario_publico(usuario)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    response.delete_cookie(CSRF_COOKIE_NAME)
    return {"ok": True}


@router.get("/eu")
def eu(usuario: Usuario = Depends(get_current_user_optional)):
    return _usuario_publico(usuario) if usuario else None
