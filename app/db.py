from __future__ import annotations

import time

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError, ProgrammingError
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
    adicionamos colunas novas via ALTER (portável SQLite/Postgres).

    Em deploy rolling no Postgres, ALTER TABLE pede lock exclusivo (AccessExclusiveLock)
    — se a réplica anterior ainda segura conexões abertas na tabela, o ALTER fica
    esperando o lock indefinidamente, o healthcheck do Railway estoura antes do lock
    liberar, e o processo nunca fica pronto. Por isso: lock_timeout curto por tentativa
    + retry com backoff, em vez de bloquear pra sempre. Também tolera coluna já criada
    por outra réplica que subiu em paralelo (mesma corrida de deploy)."""
    is_sqlite = DATABASE_URL.startswith("sqlite")
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
        ("posts", "termo_origem", "VARCHAR(120) DEFAULT ''"),
        ("posts", "anunciante_total_ads", "INTEGER"),
        ("posts", "anunciante_tem_mais_ads", "BOOLEAN"),
        ("reverso_historico", "fonte", "VARCHAR(10) DEFAULT 'tiktok'"),
        ("reverso_historico", "dias_ativos", "INTEGER"),
        ("reverso_historico", "ativo", "BOOLEAN"),
    ]
    lock_timeout_ms = 5000  # por tentativa — nunca fica preso esperando o lock pra sempre
    max_attempts = 6

    for table, col, coltype in checks:
        if table not in tables:
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        if col in existing:
            continue
        for attempt in range(1, max_attempts + 1):
            try:
                with engine.begin() as conn:
                    if not is_sqlite:
                        conn.execute(text(f"SET LOCAL lock_timeout = '{lock_timeout_ms}ms'"))
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}"))
                break
            except ProgrammingError:
                # coluna já existe: outra réplica ganhou a corrida e já criou
                break
            except OperationalError:
                if is_sqlite or attempt == max_attempts:
                    raise
                time.sleep(min(2 ** attempt, 15))


def init_db() -> None:
    from . import models  # noqa: F401  (registra as tabelas)

    Base.metadata.create_all(engine)
    _ensure_columns()
