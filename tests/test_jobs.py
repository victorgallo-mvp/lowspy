import time

from app import jobs
from app.auth import hash_senha
from app.models import Usuario


def _wait_not_running(tries=50):
    for _ in range(tries):
        if not jobs.is_running():
            return
        time.sleep(0.05)


def test_alerta_de_falha_notifica_admins_ativos(session, monkeypatch):
    session.add(Usuario(email="admin1@teste.com", senha_hash=hash_senha("senha12345"),
                        is_admin=True, ativo=True))
    session.add(Usuario(email="admin2@teste.com", senha_hash=hash_senha("senha12345"),
                        is_admin=True, ativo=False))  # desativado — não recebe
    session.add(Usuario(email="cliente@teste.com", senha_hash=hash_senha("senha12345"),
                        is_admin=False, ativo=True))  # não-admin — não recebe
    session.commit()

    def _boom(*a, **k):
        raise RuntimeError("scrapecreators fora do ar")

    monkeypatch.setattr(jobs, "run_sweep", _boom)

    enviados = []

    class FakeEmailService:
        def enviar(self, destinatario, template, contexto):
            enviados.append((destinatario, template, contexto))

    monkeypatch.setattr(jobs, "get_email_service", lambda: FakeEmailService())

    run_id = jobs.start_sweep(session, live=False, fonte="tiktok")
    assert run_id is not None
    _wait_not_running()

    destinatarios = {d for d, _, _ in enviados}
    assert destinatarios == {"admin1@teste.com"}  # só o admin ativo
    _, template, contexto = enviados[0]
    assert template == "varredura_falhou"
    assert contexto["run_id"] == run_id
    assert "scrapecreators fora do ar" in contexto["erro"]
