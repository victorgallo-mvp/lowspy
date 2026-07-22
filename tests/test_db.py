from unittest.mock import MagicMock

from sqlalchemy.exc import OperationalError, ProgrammingError

import app.db as dbmod


def _op_err():
    return OperationalError("ALTER TABLE", {}, Exception("canceling statement due to lock timeout"))


def _prog_err():
    return ProgrammingError("ALTER TABLE", {}, Exception('column "idioma" of relation "posts" already exists'))


def _fake_inspector(missing_col="idioma"):
    insp = MagicMock()
    insp.get_table_names.return_value = ["posts", "produtos", "scores", "runs"]
    all_cols = {
        "posts": ["id", "cover_url", "fonte", "total_active_time", "collation_count", "is_active", "idioma"],
        "produtos": ["id", "run_id", "novo"],
        "scores": ["id", "engaj_score", "dias_ativos"],
        "runs": ["id", "fonte"],
    }

    def get_columns(table):
        names = [c for c in all_cols[table] if c != missing_col or table != "posts"]
        return [{"name": n} for n in names]

    insp.get_columns.side_effect = get_columns
    return insp


def _fake_begin_ctx(execute_side_effect):
    conn = MagicMock()
    conn.execute.side_effect = execute_side_effect
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx


def test_ensure_columns_retry_lock_timeout_entao_sucesso(monkeypatch):
    monkeypatch.setattr(dbmod, "DATABASE_URL", "postgresql+psycopg://fake/db")
    monkeypatch.setattr(dbmod, "inspect", lambda _engine: _fake_inspector())
    sleeps: list[float] = []
    monkeypatch.setattr(dbmod.time, "sleep", lambda s: sleeps.append(s))

    # só a chamada do ALTER TABLE conta como "tentativa" — o SET LOCAL sempre passa
    attempts = {"n": 0}

    def execute(clause, *_a, **_k):
        if "ALTER TABLE" not in str(clause):
            return
        attempts["n"] += 1
        if attempts["n"] <= 2:  # falha 2x por lock timeout, sucede na 3ª
            raise _op_err()

    monkeypatch.setattr(dbmod.engine, "begin", lambda: _fake_begin_ctx(execute))

    dbmod._ensure_columns()  # não deve levantar — retry absorveu as 2 falhas

    assert attempts["n"] == 3
    assert len(sleeps) == 2  # 1 sleep entre cada tentativa que falhou


def test_ensure_columns_esgota_tentativas_e_propaga_erro(monkeypatch):
    monkeypatch.setattr(dbmod, "DATABASE_URL", "postgresql+psycopg://fake/db")
    monkeypatch.setattr(dbmod, "inspect", lambda _engine: _fake_inspector())
    monkeypatch.setattr(dbmod.time, "sleep", lambda *_: None)

    def execute(clause, *_a, **_k):
        if "ALTER TABLE" in str(clause):
            raise _op_err()  # nunca libera o lock

    monkeypatch.setattr(dbmod.engine, "begin", lambda: _fake_begin_ctx(execute))

    try:
        dbmod._ensure_columns()
        assert False, "deveria ter propagado OperationalError após esgotar as tentativas"
    except OperationalError:
        pass


def test_ensure_columns_tolera_coluna_criada_por_replica_concorrente(monkeypatch):
    monkeypatch.setattr(dbmod, "DATABASE_URL", "postgresql+psycopg://fake/db")
    monkeypatch.setattr(dbmod, "inspect", lambda _engine: _fake_inspector())
    monkeypatch.setattr(dbmod.time, "sleep", lambda *_: None)

    def execute(clause, *_a, **_k):
        if "ALTER TABLE" in str(clause):
            raise _prog_err()  # outra réplica já criou a coluna nesse meio-tempo

    monkeypatch.setattr(dbmod.engine, "begin", lambda: _fake_begin_ctx(execute))

    dbmod._ensure_columns()  # não deve levantar — trata como já-feito


def test_ensure_columns_sqlite_nao_seta_lock_timeout(monkeypatch):
    # is_sqlite=True (DATABASE_URL real de teste já é sqlite via conftest) — confirma
    # que a query SET LOCAL lock_timeout nunca é emitida nesse caminho.
    monkeypatch.setattr(dbmod, "inspect", lambda _engine: _fake_inspector())
    executed = []

    def execute(clause, *a, **k):
        executed.append(str(clause))

    monkeypatch.setattr(dbmod.engine, "begin", lambda: _fake_begin_ctx(execute))

    dbmod._ensure_columns()

    assert not any("lock_timeout" in q for q in executed)
    assert any("ALTER TABLE posts ADD COLUMN idioma" in q for q in executed)
