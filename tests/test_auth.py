from app.auth import _decode_token, criar_token, hash_senha, verificar_senha


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
