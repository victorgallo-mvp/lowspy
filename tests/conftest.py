import os
import tempfile

# DB de teste isolado (SQLite temp) ANTES de importar app.* — engine binda no import.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db")

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
