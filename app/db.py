from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import DATABASE_URL

Base = declarative_base()

_connect_args = (
    {"check_same_thread": False, "timeout": 30}  # timeout: varredura roda em thread
    if DATABASE_URL.startswith("sqlite")
    else {}
)
engine = create_engine(DATABASE_URL, future=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def _ensure_columns() -> None:
    """Migração leve idempotente: create_all NÃO altera tabela existente, então
    adicionamos colunas novas via ALTER (portável SQLite/Postgres)."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    tables = insp.get_table_names()
    checks = [
        ("posts", "cover_url", "TEXT"),
        ("produtos", "run_id", "INTEGER"),
        ("produtos", "novo", "BOOLEAN"),
        ("scores", "engaj_score", "REAL"),
        ("posts", "fonte", "VARCHAR(10) DEFAULT 'tiktok'"),
        ("posts", "total_active_time", "INTEGER"),
        ("posts", "collation_count", "INTEGER"),
        ("posts", "is_active", "BOOLEAN"),
        ("scores", "dias_ativos", "INTEGER"),
        ("runs", "fonte", "VARCHAR(10) DEFAULT 'tiktok'"),
        ("posts", "idioma", "VARCHAR(8) DEFAULT 'pt'"),
    ]
    for table, col, coltype in checks:
        if table in tables:
            existing = {c["name"] for c in insp.get_columns(table)}
            if col not in existing:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}"))


def init_db() -> None:
    from . import models  # noqa: F401  (registra as tabelas)

    Base.metadata.create_all(engine)
    _ensure_columns()
