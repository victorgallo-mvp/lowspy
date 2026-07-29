"""E-mail transacional (verificação, reset de senha, futura digest diária).

Interface pronta, sem provedor plugado — troca EMAIL_PROVIDER (env) por
"resend"/"sendgrid"/etc. e implementa a classe correspondente quando decidir o
fornecedor. Nada no resto do código muda: todo mundo chama get_email_service().
"""
from __future__ import annotations

import logging
from typing import Protocol

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


_PROVIDERS: dict[str, type] = {
    "none": NoOpEmailService,
    # "resend": ResendEmailService,   # implementar quando o provedor for escolhido
    # "sendgrid": SendgridEmailService,
}


def get_email_service() -> EmailService:
    cls = _PROVIDERS.get(config.EMAIL_PROVIDER, NoOpEmailService)
    return cls()
