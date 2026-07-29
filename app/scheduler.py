"""Cron interno de mineração — mesmo padrão já usado nos outros projetos do
operador (node-cron rodando dentro do próprio processo, sem depender de infra
externa do Railway). Pra ajustar horário, edita direto os CronTrigger abaixo.

Railway roda em UTC — os comentários já trazem o horário BRT equivalente (-3h).
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import jobs
from .db import SessionLocal

LOG = logging.getLogger("scheduler")

_scheduler = BackgroundScheduler(timezone="UTC")


def _disparar(fonte: str) -> None:
    session = SessionLocal()
    try:
        run_id = jobs.start_sweep(session, live=True, fonte=fonte)
        if run_id is None:
            LOG.warning("cron %s: já tinha varredura em andamento — pulou essa rodada", fonte)
        else:
            LOG.info("cron %s: varredura %s disparada", fonte, run_id)
    except Exception:
        LOG.exception("cron %s: falhou ao disparar a varredura", fonte)
    finally:
        session.close()


def iniciar() -> None:
    if _scheduler.running:
        return
    # 1x TikTok + 1x Meta/dia — orçamento do ScrapeCreators não comporta mais que
    # isso (ver diagnóstico de custo). Horários espaçados de propósito: rodar os
    # dois juntos desperdiça orçamento em "já visto" (achado real, runs 32-37).
    _scheduler.add_job(_disparar, CronTrigger(hour=9, minute=0), args=["tiktok"],
                       id="cron_tiktok", replace_existing=True)
    _scheduler.add_job(_disparar, CronTrigger(hour=15, minute=0), args=["meta"],
                       id="cron_meta", replace_existing=True)
    _scheduler.start()
    LOG.info("scheduler iniciado: tiktok 09:00 UTC (06h BRT), meta 15:00 UTC (12h BRT)")


def parar() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
