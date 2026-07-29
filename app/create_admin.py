"""Cria (ou promove) o admin — uso único, via terminal (Railway: `railway run`).

    python -m app.create_admin email@exemplo.com "senha forte aqui"
"""
from __future__ import annotations

import sys

from .auth import hash_senha
from .db import SessionLocal, init_db
from .models import Usuario


def create_admin(email: str, senha: str) -> None:
    email = email.strip().lower()
    if len(senha) < 8:
        raise SystemExit("senha precisa de pelo menos 8 caracteres")

    init_db()
    session = SessionLocal()
    try:
        usuario = session.query(Usuario).filter_by(email=email).first()
        if usuario is None:
            usuario = Usuario(email=email, senha_hash=hash_senha(senha), is_admin=True, ativo=True)
            session.add(usuario)
            print(f"admin criado: {email}")
        else:
            usuario.senha_hash = hash_senha(senha)
            usuario.is_admin = True
            usuario.ativo = True
            print(f"admin atualizado (senha resetada, is_admin=True): {email}")
        session.commit()
    finally:
        session.close()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("uso: python -m app.create_admin email@exemplo.com 'senha forte'")
    create_admin(sys.argv[1], sys.argv[2])
