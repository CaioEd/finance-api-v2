"""Hash de senha e access token, sem banco e sem HTTP."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from core.errors import InvalidTokenError, TokenExpiredError
from core.security import (
    PasswordHasher,
    TokenCodec,
    fingerprint,
    generate_opaque_token,
)

# Instante real, não fixo: quem valida `iat`/`exp` na decodificação é o PyJWT,
# contra o relógio do sistema. Uma data fixa no futuro faria todo token nascer
# recusado como "not yet valid"; no passado, expirado.
NOW = datetime.now(UTC)
SECRET = "chave-de-teste-com-comprimento-mais-que-suficiente"


@pytest.fixture
def hasher() -> PasswordHasher:
    from argon2 import PasswordHasher as Argon2PasswordHasher

    return PasswordHasher(Argon2PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))


@pytest.fixture
def codec() -> TokenCodec:
    return TokenCodec(secret=SECRET, algorithm="HS256", access_ttl=timedelta(minutes=15))


# ------------------------------------------------------------------ senha


def test_hash_is_never_the_password(hasher: PasswordHasher) -> None:
    hashed = hasher.hash("senha-bem-comprida")

    assert "senha-bem-comprida" not in hashed
    assert hashed.startswith("$argon2id$")


def test_same_password_hashes_differently_each_time(hasher: PasswordHasher) -> None:
    """Salt aleatório: dois cadastros com a mesma senha não se denunciam."""
    assert hasher.hash("senha-bem-comprida") != hasher.hash("senha-bem-comprida")


def test_verify_accepts_the_right_password(hasher: PasswordHasher) -> None:
    assert hasher.verify(hasher.hash("senha-bem-comprida"), "senha-bem-comprida")


def test_verify_rejects_the_wrong_password(hasher: PasswordHasher) -> None:
    assert not hasher.verify(hasher.hash("senha-bem-comprida"), "outra-senha-boa")


def test_verify_rejects_garbage_instead_of_exploding(hasher: PasswordHasher) -> None:
    """Hash corrompido no banco vira 401, não 500."""
    assert not hasher.verify("nao-e-um-hash", "senha-bem-comprida")


def test_needs_rehash_when_cost_went_up(hasher: PasswordHasher) -> None:
    from argon2 import PasswordHasher as Argon2PasswordHasher

    weak = PasswordHasher(Argon2PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))
    stronger = PasswordHasher(Argon2PasswordHasher(time_cost=3, memory_cost=64, parallelism=1))

    assert stronger.needs_rehash(weak.hash("senha-bem-comprida"))
    assert not weak.needs_rehash(weak.hash("senha-bem-comprida"))


# ------------------------------------------------------------------ access token


def test_access_token_round_trips(codec: TokenCodec) -> None:
    subject = uuid4()

    issued = codec.issue_access(subject=subject, role="user", issued_at=NOW)
    claims = codec.decode_access(issued.token)

    assert claims.subject == subject
    assert claims.role == "user"
    assert issued.expires_in == 900


def test_expired_token_is_rejected(codec: TokenCodec) -> None:
    issued = codec.issue_access(subject=uuid4(), role="user", issued_at=NOW - timedelta(hours=2))

    with pytest.raises(TokenExpiredError):
        codec.decode_access(issued.token)


def test_token_signed_with_another_key_is_rejected(codec: TokenCodec) -> None:
    other = TokenCodec(
        secret="outra-chave-igualmente-comprida-e-diferente",
        algorithm="HS256",
        access_ttl=timedelta(minutes=15),
    )
    issued = other.issue_access(subject=uuid4(), role="admin", issued_at=NOW)

    with pytest.raises(InvalidTokenError):
        codec.decode_access(issued.token)


def test_token_of_another_type_is_rejected(codec: TokenCodec) -> None:
    """Um token nosso, assinado por nós, mas que não é access."""
    forged = jwt.encode(
        {
            "sub": str(uuid4()),
            "jti": str(uuid4()),
            "typ": "refresh",
            "exp": int((NOW + timedelta(days=1)).timestamp()),
        },
        SECRET,
        algorithm="HS256",
    )

    with pytest.raises(InvalidTokenError):
        codec.decode_access(forged)


def test_unsigned_token_is_rejected(codec: TokenCodec) -> None:
    """`alg: none` é o ataque clássico contra JWT."""
    forged = jwt.encode({"sub": str(uuid4()), "typ": "access"}, key="", algorithm="none")

    with pytest.raises(InvalidTokenError):
        codec.decode_access(forged)


def test_tampered_token_is_rejected(codec: TokenCodec) -> None:
    issued = codec.issue_access(subject=uuid4(), role="user", issued_at=NOW)
    header, payload, signature = issued.token.split(".")

    with pytest.raises(InvalidTokenError):
        codec.decode_access(f"{header}.{payload}x.{signature}")


# ------------------------------------------------------------------ token opaco


def test_opaque_tokens_are_unique_and_long() -> None:
    tokens = {generate_opaque_token() for _ in range(100)}

    assert len(tokens) == 100
    assert all(len(token) >= 40 for token in tokens)


def test_fingerprint_is_stable_and_hides_the_token() -> None:
    token = generate_opaque_token()

    assert fingerprint(token) == fingerprint(token)
    assert token not in fingerprint(token)
    assert len(fingerprint(token)) == 64
    assert fingerprint(token) != fingerprint(generate_opaque_token())
