"""O limite de tentativas dentro do `AuthService.login`, com dublês.

O resto do serviço (registro, rotação, logout) é contrato de domínio e mora em
`tests/integration/test_auth.py`, contra Postgres. O que fica aqui é a regra do
limite, que não depende de banco: o que conta, em que ordem, com que chave — e
o que a tentativa barrada deixa de fazer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from core.clock import Clock
from core.errors import TooManyAttemptsError
from core.rate_limit import ALLOWED, LoginRateLimits, RateLimit, RateLimitResult
from core.security import TokenCodec, fingerprint
from models.user import Role, User
from services.auth_service import AuthService

PASSWORD = "senha-bem-comprida"
CLIENT_IP = "203.0.113.9"
LIMITS = LoginRateLimits(
    per_ip=RateLimit(attempts=5, window_seconds=300),
    per_email=RateLimit(attempts=10, window_seconds=600),
)


class RecordingRateLimiter:
    """Anota cada tentativa consumida e recusa as chaves que começam com `refuse`."""

    def __init__(self, *, refuse: str | None = None) -> None:
        self.hits: list[tuple[RateLimit, str]] = []
        self._refuse = refuse

    async def hit(self, limit: RateLimit, key: str) -> RateLimitResult:
        self.hits.append((limit, key))
        if self._refuse is not None and key.startswith(self._refuse):
            return RateLimitResult(allowed=False, retry_after_seconds=42)
        return ALLOWED


class SpyHasher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def hash(self, password: str) -> str:
        self.calls.append("hash")
        return "hash"

    def verify(self, password_hash: str, password: str) -> bool:
        self.calls.append("verify")
        return password == PASSWORD

    def needs_rehash(self, password_hash: str) -> bool:
        return False

    def dummy_verify(self) -> None:
        self.calls.append("dummy_verify")


class FakeUsers:
    def __init__(self, *users: User) -> None:
        self._by_email = {user.email: user for user in users}
        self.lookups: list[str] = []

    async def get_by_email(self, email: str) -> User | None:
        self.lookups.append(email)
        return self._by_email.get(email)


class FakeTokens:
    def add(self, record: Any) -> None:
        pass


class FakeSession:
    async def commit(self) -> None:
        pass


@dataclass
class Harness:
    limiter: RecordingRateLimiter
    users: FakeUsers = field(default_factory=FakeUsers)
    hasher: SpyHasher = field(default_factory=SpyHasher)

    @property
    def service(self) -> AuthService:
        return AuthService(
            session=FakeSession(),  # type: ignore[arg-type]
            users=self.users,  # type: ignore[arg-type]
            tokens=FakeTokens(),  # type: ignore[arg-type]
            hasher=self.hasher,  # type: ignore[arg-type]
            codec=TokenCodec(
                secret="chave-de-teste-com-comprimento-mais-que-suficiente",
                algorithm="HS256",
                access_ttl=timedelta(minutes=15),
            ),
            clock=Clock(tz=ZoneInfo("America/Sao_Paulo")),
            refresh_ttl=timedelta(days=30),
            rate_limiter=self.limiter,
            login_limits=LIMITS,
        )


def ana() -> User:
    return User(
        id=uuid4(),
        email="ana@exemplo.com",
        username="ana",
        password_hash="hash",
        role=Role.USER,
        is_active=True,
    )


async def test_the_attempt_counts_by_ip_then_by_email_before_the_password() -> None:
    harness = Harness(RecordingRateLimiter(), users=FakeUsers(ana()))

    await harness.service.login("ana@exemplo.com", PASSWORD, client_ip=CLIENT_IP)

    assert harness.limiter.hits == [
        (LIMITS.per_ip, f"login:ip:{CLIENT_IP}"),
        (LIMITS.per_email, f"login:email:{fingerprint('ana@exemplo.com')}"),
    ]


async def test_the_email_key_is_a_hash_that_ignores_case_and_spaces() -> None:
    """O storage não guarda quem tentou entrar, e `ANA@` não ganha uma cota à parte."""
    harness = Harness(RecordingRateLimiter())

    with pytest.raises(Exception):  # noqa: B017 - a credencial é o de menos aqui
        await harness.service.login(" ANA@Exemplo.COM ", PASSWORD, client_ip=CLIENT_IP)

    _, email_key = harness.limiter.hits[1]
    assert email_key == f"login:email:{fingerprint('ana@exemplo.com')}"
    assert "ana" not in email_key


async def test_an_ip_over_the_limit_is_refused_even_with_the_right_password() -> None:
    harness = Harness(RecordingRateLimiter(refuse="login:ip:"), users=FakeUsers(ana()))

    with pytest.raises(TooManyAttemptsError) as refused:
        await harness.service.login("ana@exemplo.com", PASSWORD, client_ip=CLIENT_IP)

    assert refused.value.headers == {"Retry-After": "42"}
    # Barrada no IP, a tentativa não gasta a cota do e-mail de ninguém...
    assert [key for _, key in harness.limiter.hits] == [f"login:ip:{CLIENT_IP}"]
    # ...nem chega ao banco ou ao argon2: não gasta CPU e não diz se a senha servia.
    assert harness.users.lookups == []
    assert harness.hasher.calls == []


async def test_an_email_over_the_limit_is_refused_from_a_fresh_ip() -> None:
    harness = Harness(RecordingRateLimiter(refuse="login:email:"), users=FakeUsers(ana()))

    with pytest.raises(TooManyAttemptsError):
        await harness.service.login("ana@exemplo.com", PASSWORD, client_ip="198.51.100.2")

    assert harness.users.lookups == []
    assert harness.hasher.calls == []


async def test_a_refused_login_leaves_the_ip_in_the_log(caplog: pytest.LogCaptureFixture) -> None:
    """É por esse IP que se confere, no deploy, que o proxy está bem configurado."""
    harness = Harness(RecordingRateLimiter(refuse="login:ip:"))

    with (
        caplog.at_level(logging.WARNING, logger="services.auth_service"),
        pytest.raises(TooManyAttemptsError),
    ):
        await harness.service.login("ana@exemplo.com", PASSWORD, client_ip=CLIENT_IP)

    assert f"ip={CLIENT_IP}" in caplog.text
    assert "ana@exemplo.com" not in caplog.text
