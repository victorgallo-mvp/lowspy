"""Rate limit em memória (sem Redis) — mesmo padrão do resto do projeto (cron/
jobs já rodam tudo em-processo, sem infra externa). Não sobrevive a restart/
deploy nem funciona com múltiplas réplicas — tradeoff aceito pro volume atual;
trocar por Redis se algum dia escalar horizontalmente."""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_tentativas: dict[str, list[float]] = {}


def registrar_falha(chave: str) -> None:
    with _lock:
        _tentativas.setdefault(chave, []).append(time.time())


def limpar(chave: str) -> None:
    with _lock:
        _tentativas.pop(chave, None)


def bloqueado(chave: str, max_tentativas: int, janela_segundos: int) -> bool:
    """True se `chave` já esgotou as tentativas dentro da janela. Também poda
    tentativas velhas (evita o dict crescer pra sempre com chave já expirada)."""
    limite = time.time() - janela_segundos
    with _lock:
        vistos = [t for t in _tentativas.get(chave, []) if t >= limite]
        if vistos:
            _tentativas[chave] = vistos
        else:
            _tentativas.pop(chave, None)
        return len(vistos) >= max_tentativas
