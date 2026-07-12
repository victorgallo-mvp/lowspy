"""Runner assíncrono da varredura (disparada pelo dashboard).

Roda `run_sweep` numa thread (não trava o request — pode levar minutos), com trava
de 1-por-vez (evita dois scans concorrentes queimando crédito) e status persistido
na tabela `runs`. Em Postgres (prod) a concorrência é tranquila; SQLite usa timeout.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from . import config
from .db import SessionLocal
from .models import Run
from .pipeline import run_sweep

LOG = logging.getLogger("jobs")

_lock = threading.Lock()
_state = {"active": False}


def is_running() -> bool:
    return _state["active"]


def _now():
    return datetime.now(timezone.utc)


def _worker(run_id: int, live: bool, max_hashtags: Optional[int],
            max_comment_fetches: Optional[int]) -> None:
    session = SessionLocal()
    try:
        run = session.get(Run, run_id)
        run.status = "running"
        run.started_at = _now()
        session.commit()

        cfg = config.load_config()
        summary = run_sweep(session, cfg, live, max_hashtags, max_comment_fetches)

        run = session.get(Run, run_id)
        run.status = "done"
        run.summary = summary
        run.finished_at = _now()
        session.commit()
        LOG.info("varredura %s concluída: %s", run_id, summary)
    except Exception as e:  # não deixa o job travado
        LOG.exception("varredura %s falhou", run_id)
        session.rollback()
        run = session.get(Run, run_id)
        if run:
            run.status = "error"
            run.error = str(e)[:1000]
            run.finished_at = _now()
            session.commit()
    finally:
        _state["active"] = False
        session.close()


def start_sweep(session, live: bool = True, max_hashtags: Optional[int] = None,
                max_comment_fetches: Optional[int] = None) -> Optional[int]:
    """Cria o Run e dispara a thread. Retorna run_id, ou None se já há uma rodando."""
    with _lock:
        if _state["active"]:
            return None
        run = Run(status="queued", mode="live" if live else "dry-run")
        session.add(run)
        session.commit()
        run_id = run.id
        _state["active"] = True
    t = threading.Thread(
        target=_worker, args=(run_id, live, max_hashtags, max_comment_fetches), daemon=True
    )
    t.start()
    return run_id


def reset_stale() -> None:
    """No boot, marca runs presos (restart no meio) como interrompidos."""
    session = SessionLocal()
    try:
        for r in session.query(Run).filter(Run.status.in_(["queued", "running"])).all():
            r.status = "interrupted"
        session.commit()
    finally:
        session.close()
