import time

from app.rate_limit import bloqueado, limpar, registrar_falha


def test_bloqueia_apos_max_tentativas():
    for _ in range(3):
        assert bloqueado("x@teste.com", max_tentativas=3, janela_segundos=60) is False
        registrar_falha("x@teste.com")
    assert bloqueado("x@teste.com", max_tentativas=3, janela_segundos=60) is True


def test_chaves_diferentes_nao_se_afetam():
    for _ in range(5):
        registrar_falha("vitima@teste.com")
    assert bloqueado("vitima@teste.com", max_tentativas=5, janela_segundos=60) is True
    assert bloqueado("outro@teste.com", max_tentativas=5, janela_segundos=60) is False


def test_limpar_reseta_o_contador():
    for _ in range(5):
        registrar_falha("x@teste.com")
    assert bloqueado("x@teste.com", max_tentativas=5, janela_segundos=60) is True
    limpar("x@teste.com")
    assert bloqueado("x@teste.com", max_tentativas=5, janela_segundos=60) is False


def test_tentativa_fora_da_janela_nao_conta():
    # simula tentativa "velha" injetando timestamp direto (evita sleep real no teste)
    import app.rate_limit as mod
    mod._tentativas["x@teste.com"] = [time.time() - 3600]  # 1h atrás
    assert bloqueado("x@teste.com", max_tentativas=1, janela_segundos=60) is False
