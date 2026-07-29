from app import scheduler


def test_iniciar_agenda_1x_tiktok_1x_meta_por_dia():
    scheduler.iniciar()
    try:
        ids = {j.id for j in scheduler._scheduler.get_jobs()}
        assert ids == {"cron_tiktok", "cron_meta"}
    finally:
        scheduler.parar()


def test_iniciar_e_idempotente():
    scheduler.iniciar()
    try:
        scheduler.iniciar()  # não deveria duplicar job nem levantar erro
        assert len(scheduler._scheduler.get_jobs()) == 2
    finally:
        scheduler.parar()


def test_disparar_chama_start_sweep_live(monkeypatch, session):
    chamadas = []
    monkeypatch.setattr(
        scheduler.jobs, "start_sweep",
        lambda session, live=True, fonte="tiktok": chamadas.append((live, fonte)) or 1,
    )
    scheduler._disparar("meta")
    assert chamadas == [(True, "meta")]  # cron sempre dispara live=True, nunca dry-run
