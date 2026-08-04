from fastapi.testclient import TestClient

from app.api import app
from app.auth import _decode_token, criar_token, hash_senha, verificar_senha

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
