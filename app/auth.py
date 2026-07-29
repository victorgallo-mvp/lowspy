"""Autenticação própria (sem provedor gerenciado): senha com bcrypt, sessão via JWT
em cookie httpOnly. Cobre usuário pagante (área /app) e admin (área /admin, mesma
tabela, is_admin=True).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Cookie, Depends, HTTPException
from jose import JWTError, jwt
from sqlalchemy import select

from . import config
from .db import get_db
from .models import Usuario

COOKIE_NAME = "lowspy_session"


def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verificar_senha(senha: str, senha_hash: str) -> bool:
    try:
        return bcrypt.checkpw(senha.encode("utf-8"), senha_hash.encode("utf-8"))
    except ValueError:  # hash malformado — nunca deveria acontecer, mas não derruba
        return False


def criar_token(usuario_id: int) -> str:
    expira = datetime.now(timezone.utc) + timedelta(minutes=config.JWT_EXPIRE_MINUTES)
    payload = {"sub": str(usuario_id), "exp": expira}
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def _decode_token(token: str) -> Optional[int]:
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
        return int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        return None


def get_current_user_optional(
    db=Depends(get_db), lowspy_session: Optional[str] = Cookie(default=None)
) -> Optional[Usuario]:
    """Não levanta — retorna None se não autenticado. Uso em rotas públicas que se
    comportam diferente se o visitante estiver logado."""
    if not lowspy_session:
        return None
    usuario_id = _decode_token(lowspy_session)
    if usuario_id is None:
        return None
    usuario = db.get(Usuario, usuario_id)
    if usuario is None or not usuario.ativo:
        return None
    return usuario


def get_current_user(usuario: Optional[Usuario] = Depends(get_current_user_optional)) -> Usuario:
    if usuario is None:
        raise HTTPException(status_code=401, detail="não autenticado")
    return usuario


def require_admin(usuario: Usuario = Depends(get_current_user)) -> Usuario:
    if not usuario.is_admin:
        raise HTTPException(status_code=403, detail="acesso restrito ao admin")
    return usuario
