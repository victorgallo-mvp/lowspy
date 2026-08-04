import os
import tempfile

# DB de teste isolado (SQLite temp) ANTES de importar app.* — engine binda no import.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db")
# TestClient roda sobre http (não https) — cookie Secure=True nunca voltaria no request
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("JWT_SECRET", "segredo-de-teste-nao-usar-em-producao")
# TestClient dispara o lifespan (inclusive o scheduler) — nunca deixa o cron real
# ligado em teste, senão um teste que demorar até 09h/15h UTC dispararia live=True
os.environ.setdefault("CRON_ENABLED", "0")

import pytest  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402


@pytest.fixture
def session():
    Base.metadata.create_all(engine)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
        Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _limpa_rate_limit():
    # estado global em memória (não é por-DB-de-teste) — sem isso, um teste de
    # login falho vaza tentativas pro próximo teste que usar o mesmo e-mail
    from app.rate_limit import _tentativas
    _tentativas.clear()
    yield
    _tentativas.clear()
