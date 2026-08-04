"""E-mail transacional (verificação, reset de senha, futura digest diária).

Interface pronta, sem provedor plugado — troca EMAIL_PROVIDER (env) por
"resend"/"sendgrid"/etc. e implementa a classe correspondente quando decidir o
fornecedor. Nada no resto do código muda: todo mundo chama get_email_service().
"""
from __future__ import annotations

import logging
from typing import Protocol

import httpx

from . import config

LOG = logging.getLogger("email")


class EmailService(Protocol):
    def enviar(self, destinatario: str, template: str, contexto: dict) -> None: ...


class NoOpEmailService:
    """Default sem provedor: loga em vez de enviar. Fluxos de auth funcionam
    (o link/código fica no log) até um provedor real ser plugado."""

    def enviar(self, destinatario: str, template: str, contexto: dict) -> None:
        LOG.info("[email não enviado — EMAIL_PROVIDER=none] para=%s template=%s contexto=%s",
                 destinatario, template, contexto)


def _render(template: str, contexto: dict) -> tuple[str, str]:
    """(assunto, html) por template. Chave nova? Cai no fallback genérico —
    nunca derruba o fluxo (registro/cron) só porque faltou template dedicado."""
    if template == "boas_vindas":
        return (
            "Bem-vindo ao LowSpy",
            f"<p>Olá!</p><p>Sua conta <b>{contexto.get('email', '')}</b> foi criada com sucesso "
            f"no LowSpy.</p>",
        )
    if template == "varredura_falhou":
        return (
            f"[LowSpy] Varredura {contexto.get('fonte', '')} falhou (run #{contexto.get('run_id', '')})",
            f"<p>A varredura <b>{contexto.get('fonte', '')}</b> (run #{contexto.get('run_id', '')}) "
            f"falhou:</p><pre>{contexto.get('erro', '')}</pre>",
        )
    return (f"[LowSpy] {template}", f"<pre>{contexto}</pre>")


class ResendEmailService:
    """Provedor via API REST do Resend (sem SDK — mesmo padrão do resto do
    projeto, que já usa httpx direto pro ScrapeCreators).

    Sem domínio verificado, EMAIL_FROM (onboarding@resend.dev) só entrega pro
    e-mail da PRÓPRIA conta Resend — pra mandar pra qualquer usuário, precisa
    verificar um domínio (DNS SPF/DKIM) em resend.com/domains e trocar EMAIL_FROM."""

    _API_URL = "https://api.resend.com/emails"

    def enviar(self, destinatario: str, template: str, contexto: dict) -> None:
        assunto, html = _render(template, contexto)
        r = httpx.post(
            self._API_URL,
            headers={"Authorization": f"Bearer {config.RESEND_API_KEY}"},
            json={"from": config.EMAIL_FROM, "to": [destinatario], "subject": assunto, "html": html},
            timeout=10,
        )
        r.raise_for_status()


_PROVIDERS: dict[str, type] = {
    "none": NoOpEmailService,
    "resend": ResendEmailService,
    # "sendgrid": SendgridEmailService,
}


def get_email_service() -> EmailService:
    cls = _PROVIDERS.get(config.EMAIL_PROVIDER, NoOpEmailService)
    return cls()
