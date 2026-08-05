from fastapi.testclient import TestClient

from app.api import app
from app.auth import (
    _decode_token,
    criar_token,
    criar_token_reset_senha,
    hash_senha,
    sub_do_token,
    verificar_senha,
    verificar_token_reset_senha,
)

client = TestClient(app)


def test_hash_senha_gera_hash_diferente_da_senha_e_verifica():
    h = hash_senha("minhasenha123")
    assert h != "minhasenha123"
    assert verificar_senha("minhasenha123", h) is True
    assert verificar_senha("senhaerrada", h) is False


def test_hash_senha_gera_salt_diferente_a_cada_chamada():
    # duas chamadas com a mesma senha geram hashes diferentes (salt aleatório) —
    # sem isso, dois usuários com a mesma senha teriam o mesmo hash no banco
    h1 = hash_senha("senha12345")
    h2 = hash_senha("senha12345")
    assert h1 != h2
    assert verificar_senha("senha12345", h1) and verificar_senha("senha12345", h2)


def test_verificar_senha_tolera_hash_malformado():
    # nunca deveria acontecer, mas não pode derrubar o login com exceção
    assert verificar_senha("qualquer", "hash-invalido-nao-e-bcrypt") is False


def test_criar_e_decodificar_token():
    tok = criar_token(42)
    assert _decode_token(tok) == 42


def test_decode_token_rejeita_lixo_ou_assinatura_invalida():
    assert _decode_token("nao-e-um-jwt") is None
    # token válido de estrutura mas assinado com outra chave
    from jose import jwt as jose_jwt
    tok_outra_chave = jose_jwt.encode({"sub": "1"}, "outra-chave-secreta", algorithm="HS256")
    assert _decode_token(tok_outra_chave) is None


def test_decode_token_rejeita_token_expirado():
    from datetime import datetime, timedelta, timezone
    from jose import jwt as jose_jwt
    from app import config
    expirado = jose_jwt.encode(
        {"sub": "1", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        config.JWT_SECRET, algorithm=config.JWT_ALGORITHM,
    )
    assert _decode_token(expirado) is None


def test_registro_sobrevive_a_falha_no_provedor_de_email(session, monkeypatch):
    # provedor de e-mail fora do ar (Resend caiu, chave inválida etc) não pode
    # transformar um cadastro bem-sucedido em erro 500 — a conta já foi commitada
    import app.auth_api as auth_api_mod

    class QuebraEmailService:
        def enviar(self, *a, **k):
            raise RuntimeError("resend fora do ar")

    monkeypatch.setattr(auth_api_mod, "get_email_service", lambda: QuebraEmailService())

    r = client.post("/auth/registro", json={"email": "novo@teste.com", "senha": "senha12345"})
    assert r.status_code == 200
    from app.models import Usuario
    assert session.query(Usuario).filter_by(email="novo@teste.com").count() == 1


def _cria_usuario(session, email="alvo@teste.com", senha="senhacerta123"):
    from app.models import Usuario
    session.add(Usuario(email=email, senha_hash=hash_senha(senha), is_admin=False, ativo=True))
    session.commit()


def test_login_bloqueia_apos_5_tentativas_erradas(session):
    _cria_usuario(session)
    for _ in range(5):
        r = client.post("/auth/login", json={"email": "alvo@teste.com", "senha": "errada"})
        assert r.status_code == 401
    # 6ª tentativa, mesmo com a senha CERTA, já bloqueia (o dano do brute-force
    # já teria acontecido antes dessa tentativa se fosse a certa)
    r = client.post("/auth/login", json={"email": "alvo@teste.com", "senha": "senhacerta123"})
    assert r.status_code == 429


def test_login_bloqueio_e_por_email_nao_afeta_outras_contas(session):
    _cria_usuario(session, email="vitima@teste.com")
    _cria_usuario(session, email="outra@teste.com", senha="outrasenha123")
    for _ in range(5):
        client.post("/auth/login", json={"email": "vitima@teste.com", "senha": "errada"})
    assert client.post("/auth/login", json={"email": "vitima@teste.com",
                                            "senha": "senhacerta123"}).status_code == 429
    # outra conta não é afetada pelo bloqueio da primeira
    r = client.post("/auth/login", json={"email": "outra@teste.com", "senha": "outrasenha123"})
    assert r.status_code == 200


def test_login_sucesso_limpa_o_contador_de_falhas(session):
    _cria_usuario(session)
    for _ in range(3):
        client.post("/auth/login", json={"email": "alvo@teste.com", "senha": "errada"})
    r = client.post("/auth/login", json={"email": "alvo@teste.com", "senha": "senhacerta123"})
    assert r.status_code == 200
    # login certo resetou o contador — 3 erradas de novo não bloqueia (só bloquearia na 5ª)
    for _ in range(3):
        r = client.post("/auth/login", json={"email": "alvo@teste.com", "senha": "errada"})
        assert r.status_code == 401


class _UsuarioFake:
    def __init__(self, id, senha_hash):
        self.id = id
        self.senha_hash = senha_hash


def test_token_reset_senha_valido_pro_usuario_certo():
    u = _UsuarioFake(id=7, senha_hash="hash-atual")
    tok = criar_token_reset_senha(u)
    assert sub_do_token(tok) == 7
    assert verificar_token_reset_senha(tok, u) is True


def test_token_reset_senha_invalida_sozinho_depois_de_trocar_a_senha():
    # o "uso único" vem de comparar contra o senha_hash ATUAL — depois que a
    # senha muda, qualquer cópia antiga do token (link reencaminhado, replay)
    # deixa de bater
    u = _UsuarioFake(id=7, senha_hash="hash-antigo")
    tok = criar_token_reset_senha(u)
    u.senha_hash = "hash-novo"  # simula o reset já ter acontecido
    assert verificar_token_reset_senha(tok, u) is False


def test_token_reset_senha_rejeita_usuario_errado():
    dono = _UsuarioFake(id=7, senha_hash="hash-x")
    outro = _UsuarioFake(id=8, senha_hash="hash-x")
    tok = criar_token_reset_senha(dono)
    assert verificar_token_reset_senha(tok, outro) is False


def test_token_reset_senha_rejeita_token_de_login_comum():
    # token normal de sessão (sem "purpose": "reset_senha") não pode ser
    # reaproveitado pra resetar senha
    u = _UsuarioFake(id=7, senha_hash="hash-x")
    tok_login = criar_token(7)
    assert verificar_token_reset_senha(tok_login, u) is False


def test_esqueci_senha_sempre_responde_ok_mesmo_sem_conta(session):
    r = client.post("/auth/esqueci-senha", json={"email": "nao-existe@teste.com"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_esqueci_senha_manda_email_com_link_de_reset(session, monkeypatch):
    _cria_usuario(session, email="dono@teste.com")
    enviados = []
    import app.auth_api as auth_api_mod

    class CapturaEmailService:
        def enviar(self, destinatario, template, contexto):
            enviados.append((destinatario, template, contexto))

    monkeypatch.setattr(auth_api_mod, "get_email_service", lambda: CapturaEmailService())

    r = client.post("/auth/esqueci-senha", json={"email": "dono@teste.com"})
    assert r.status_code == 200
    assert len(enviados) == 1
    destinatario, template, contexto = enviados[0]
    assert destinatario == "dono@teste.com"
    assert template == "resetar_senha"
    assert "/resetar-senha?token=" in contexto["link"]


def test_esqueci_senha_bloqueia_apos_3_pedidos(session, monkeypatch):
    _cria_usuario(session, email="dono@teste.com")
    import app.auth_api as auth_api_mod
    monkeypatch.setattr(auth_api_mod, "get_email_service", lambda: type(
        "S", (), {"enviar": lambda self, *a, **k: None})())

    for _ in range(3):
        assert client.post("/auth/esqueci-senha", json={"email": "dono@teste.com"}).status_code == 200
    # 4º pedido ainda responde 200 (não vaza o bloqueio) mas não manda e-mail de verdade
    enviados = []
    monkeypatch.setattr(auth_api_mod, "get_email_service", lambda: type(
        "S", (), {"enviar": lambda self, *a, **k: enviados.append(a)})())
    r = client.post("/auth/esqueci-senha", json={"email": "dono@teste.com"})
    assert r.status_code == 200
    assert enviados == []


def test_resetar_senha_fluxo_completo(session):
    from app.models import Usuario
    _cria_usuario(session, email="dono@teste.com", senha="senhavelha123")
    usuario = session.query(Usuario).filter_by(email="dono@teste.com").one()
    tok = criar_token_reset_senha(usuario)

    r = client.post("/auth/resetar-senha", json={"token": tok, "nova_senha": "senhanova456"})
    assert r.status_code == 200

    # senha antiga não funciona mais, a nova sim
    assert client.post("/auth/login",
                       json={"email": "dono@teste.com", "senha": "senhavelha123"}).status_code == 401
    assert client.post("/auth/login",
                       json={"email": "dono@teste.com", "senha": "senhanova456"}).status_code == 200


def test_resetar_senha_rejeita_reuso_do_mesmo_token(session):
    from app.models import Usuario
    _cria_usuario(session, email="dono@teste.com", senha="senhavelha123")
    usuario = session.query(Usuario).filter_by(email="dono@teste.com").one()
    tok = criar_token_reset_senha(usuario)

    assert client.post("/auth/resetar-senha",
                       json={"token": tok, "nova_senha": "primeiranova1"}).status_code == 200
    # reenviar o MESMO token uma 2ª vez — já foi consumido (senha_hash mudou)
    r = client.post("/auth/resetar-senha", json={"token": tok, "nova_senha": "outraqualquer1"})
    assert r.status_code == 400


def test_resetar_senha_rejeita_token_invalido_ou_curto_demais(session):
    _cria_usuario(session, email="dono@teste.com")
    assert client.post("/auth/resetar-senha",
                       json={"token": "lixo-nao-e-jwt", "nova_senha": "senhanova456"}).status_code == 400
    from app.models import Usuario
    usuario = session.query(Usuario).filter_by(email="dono@teste.com").one()
    tok = criar_token_reset_senha(usuario)
    r = client.post("/auth/resetar-senha", json={"token": tok, "nova_senha": "curta"})
    assert r.status_code == 400
