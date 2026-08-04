import httpx
import pytest

from app import config
from app.email_service import NoOpEmailService, ResendEmailService, get_email_service


def test_noop_email_service_nao_lanca(caplog):
    caplog.set_level("INFO")
    NoOpEmailService().enviar("x@teste.com", "boas_vindas", {"email": "x@teste.com"})
    assert "email não enviado" in caplog.text


def test_get_email_service_resolve_pelo_provider(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "resend")
    assert isinstance(get_email_service(), ResendEmailService)
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "algo-nao-configurado")
    assert isinstance(get_email_service(), NoOpEmailService)  # fallback seguro


def test_resend_monta_payload_e_header_corretos(monkeypatch):
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_fake_key")
    monkeypatch.setattr(config, "EMAIL_FROM", "LowSpy <onboarding@resend.dev>")
    chamadas = []

    def _fake_post(url, headers=None, json=None, timeout=None):
        chamadas.append({"url": url, "headers": headers, "json": json})
        return httpx.Response(200, json={"id": "abc"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", _fake_post)

    ResendEmailService().enviar("dest@teste.com", "boas_vindas", {"email": "dest@teste.com"})

    assert len(chamadas) == 1
    c = chamadas[0]
    assert c["url"] == "https://api.resend.com/emails"
    assert c["headers"]["Authorization"] == "Bearer re_fake_key"
    assert c["json"]["to"] == ["dest@teste.com"]
    assert c["json"]["from"] == "LowSpy <onboarding@resend.dev>"
    assert "criada com sucesso" in c["json"]["html"]


def test_resend_template_varredura_falhou_inclui_erro_no_corpo(monkeypatch):
    chamadas = []
    monkeypatch.setattr(
        httpx, "post",
        lambda url, headers=None, json=None, timeout=None: chamadas.append(json)
        or httpx.Response(200, json={}, request=httpx.Request("POST", url)),
    )
    ResendEmailService().enviar("admin@teste.com", "varredura_falhou",
                                {"run_id": 51, "fonte": "tiktok", "erro": "FK violation"})
    assert "51" in chamadas[0]["subject"]
    assert "FK violation" in chamadas[0]["html"]


def test_resend_propaga_erro_http(monkeypatch):
    def _fake_post(url, headers=None, json=None, timeout=None):
        return httpx.Response(500, json={"message": "invalid key"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", _fake_post)
    with pytest.raises(httpx.HTTPStatusError):
        ResendEmailService().enviar("dest@teste.com", "boas_vindas", {"email": "dest@teste.com"})
