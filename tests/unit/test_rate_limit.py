"""O núcleo do limite de tentativas: de quem é a tentativa, e se ela cabe.

Sem aplicação e sem banco. O contador é o de verdade — o slowapi com storage em
memória, que não faz I/O —, e o tempo é fixado trocando o `time` que o `limits`
e `core.rate_limit` enxergam, em vez de dormir no teste.
"""

from __future__ import annotations

import logging

import limits.storage.memory
import pytest
from limits.errors import ConfigurationError
from starlette.requests import Request

import core.rate_limit
from core.config import Environment, Settings
from core.rate_limit import (
    ClientIpResolver,
    LoginRateLimits,
    RateLimit,
    SlowapiRateLimiter,
    UnlimitedRateLimiter,
    build_rate_limiter,
)
from main import create_app

VALID = {
    "environment": "local",
    "app_timezone": "America/Sao_Paulo",
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    "jwt_secret_key": "chave-de-teste-com-comprimento-mais-que-suficiente",
}


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **{**VALID, **overrides})  # type: ignore[arg-type]


def request_from(
    peer: str | None = "10.0.0.7", headers: list[tuple[str, str]] | None = None
) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": [
                (name.lower().encode("latin-1"), value.encode("latin-1"))
                for name, value in headers or []
            ],
            "client": (peer, 54321) if peer is not None else None,
        }
    )


# ------------------------------------------------------------------ IP do cliente


def test_without_a_header_the_socket_is_the_client_and_forwarded_for_is_ignored() -> None:
    """Sem proxy declarado, qualquer cabeçalho de IP veio do próprio cliente."""
    resolver = ClientIpResolver()

    request = request_from(peer="203.0.113.9", headers=[("X-Forwarded-For", "1.2.3.4")])

    assert resolver.resolve(request) == "203.0.113.9"


@pytest.mark.parametrize(("trusted", "expected"), [(1, "198.51.100.2"), (2, "198.51.100.1")])
def test_the_address_is_read_from_the_right(trusted: int, expected: str) -> None:
    resolver = ClientIpResolver(header="X-Forwarded-For", trusted_proxies=trusted)

    request = request_from(headers=[("X-Forwarded-For", "198.51.100.1, 198.51.100.2")])

    assert resolver.resolve(request) == expected


def test_what_the_client_writes_on_the_left_is_never_read() -> None:
    """Trocar o começo da lista a cada requisição não pode render um contador novo."""
    resolver = ClientIpResolver(header="X-Forwarded-For", trusted_proxies=1)

    seen = {
        resolver.resolve(request_from(headers=[("X-Forwarded-For", f"{spoofed}, 203.0.113.9")]))
        for spoofed in ("1.1.1.1", "8.8.8.8", "127.0.0.1", "1.1.1.1, 2.2.2.2")
    }

    assert seen == {"203.0.113.9"}


def test_a_header_repeated_in_two_lines_is_one_list() -> None:
    """Com `get`, a primeira linha — a que o cliente escreveu — venceria."""
    resolver = ClientIpResolver(header="X-Real-IP", trusted_proxies=1)

    request = request_from(headers=[("X-Real-IP", "6.6.6.6"), ("X-Real-IP", "203.0.113.9")])

    assert resolver.resolve(request) == "203.0.113.9"


def test_the_header_name_ignores_case() -> None:
    resolver = ClientIpResolver(header="x-real-ip", trusted_proxies=1)

    assert resolver.resolve(request_from(headers=[("X-Real-IP", "203.0.113.9")])) == "203.0.113.9"


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("X-Forwarded-For", "198.51.100.2")],
        [("X-Forwarded-For", "nao-e-ip, lixo")],
        [("X-Forwarded-For", " , ")],
    ],
    ids=["ausente", "curto-demais", "nao-e-ip", "vazio"],
)
def test_an_unusable_header_falls_back_to_the_socket(headers: list[tuple[str, str]]) -> None:
    resolver = ClientIpResolver(header="X-Forwarded-For", trusted_proxies=2)

    assert resolver.resolve(request_from(peer="10.0.0.7", headers=headers)) == "10.0.0.7"


def test_ipv6_is_counted_by_its_64_network() -> None:
    """Contar por endereço daria a quem tem uma /64 inteira 2^64 contadores zerados."""
    resolver = ClientIpResolver(header="X-Real-IP")

    first = resolver.resolve(request_from(headers=[("X-Real-IP", "2001:db8:1:2:aaaa::1")]))
    second = resolver.resolve(request_from(headers=[("X-Real-IP", "2001:db8:1:2:bbbb::9")]))

    assert first == second == "2001:db8:1:2::/64"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("::ffff:203.0.113.9", "203.0.113.9"),
        ("203.0.113.9:4711", "203.0.113.9"),
        ("[2001:db8::1]:443", "2001:db8::/64"),
    ],
    ids=["ipv4-mapeado", "ipv4-com-porta", "ipv6-com-porta"],
)
def test_the_same_client_written_differently_is_the_same_key(value: str, expected: str) -> None:
    resolver = ClientIpResolver(header="X-Forwarded-For")

    assert resolver.resolve(request_from(headers=[("X-Forwarded-For", value)])) == expected


def test_a_socket_peer_that_is_not_an_ip_is_used_as_is() -> None:
    """O `TestClient` conecta como "testclient"; ainda assim é uma chave estável."""
    assert ClientIpResolver().resolve(request_from(peer="testclient")) == "testclient"


def test_a_request_without_a_socket_peer_still_has_a_key() -> None:
    assert ClientIpResolver().resolve(request_from(peer=None)) == "desconhecido"


def test_the_resolver_comes_from_the_settings() -> None:
    settings = _settings(client_ip_header="X-Real-IP", trusted_proxy_count=2)

    assert ClientIpResolver.from_settings(settings) == ClientIpResolver("X-Real-IP", 2)


# ------------------------------------------------------------------ contador


class FrozenTime:
    """O `time` do `limits` e de `core.rate_limit`, parado onde o teste mandar."""

    def __init__(self) -> None:
        self.now = 1_000_000.0

    def time(self) -> float:
        return self.now


@pytest.fixture
def frozen_time(monkeypatch: pytest.MonkeyPatch) -> FrozenTime:
    frozen = FrozenTime()
    monkeypatch.setattr(limits.storage.memory, "time", frozen)
    monkeypatch.setattr(core.rate_limit, "time", frozen)
    return frozen


@pytest.mark.parametrize("strategy", ["moving-window", "fixed-window", "sliding-window-counter"])
async def test_the_attempts_of_the_limit_fit_and_the_next_one_does_not(strategy: str) -> None:
    limiter = SlowapiRateLimiter(storage_url="memory://", strategy=strategy)
    limit = RateLimit(attempts=3, window_seconds=60)

    results = [await limiter.hit(limit, "login:ip:203.0.113.9") for _ in range(4)]

    assert [result.allowed for result in results] == [True, True, True, False]
    assert 0 < results[-1].retry_after_seconds <= 60


async def test_each_key_counts_alone() -> None:
    limiter = SlowapiRateLimiter(storage_url="memory://", strategy="moving-window")
    limit = RateLimit(attempts=1, window_seconds=60)

    await limiter.hit(limit, "login:ip:203.0.113.9")

    assert (await limiter.hit(limit, "login:ip:198.51.100.2")).allowed


async def test_the_window_moves_and_says_how_long_to_wait(frozen_time: FrozenTime) -> None:
    """Janela móvel: cada tentativa sai da conta 60 s depois de feita, e não num corte fixo."""
    limiter = SlowapiRateLimiter(storage_url="memory://", strategy="moving-window")
    limit = RateLimit(attempts=2, window_seconds=60)
    key = "login:email:abc"

    assert (await limiter.hit(limit, key)).allowed  # t=0
    frozen_time.now += 30
    assert (await limiter.hit(limit, key)).allowed  # t=30

    frozen_time.now += 10
    refused = await limiter.hit(limit, key)  # t=40: a de t=0 só sai em t=60
    assert (refused.allowed, refused.retry_after_seconds) == (False, 20)

    frozen_time.now += 21
    assert (await limiter.hit(limit, key)).allowed  # t=61: a de t=0 saiu

    frozen_time.now += 1
    refused = await limiter.hit(limit, key)  # t=62: a de t=30 sai em t=90
    assert (refused.allowed, refused.retry_after_seconds) == (False, 28)


@pytest.mark.parametrize(
    ("storage_url", "strategy"),
    [("memory://", "token-bucket"), ("nao-existe://localhost", "moving-window")],
    ids=["estrategia", "storage"],
)
def test_a_bad_backend_fails_at_startup_not_at_the_first_login(
    storage_url: str, strategy: str
) -> None:
    with pytest.raises(ConfigurationError):
        SlowapiRateLimiter(storage_url=storage_url, strategy=strategy)


async def test_the_unlimited_limiter_never_refuses() -> None:
    limiter = UnlimitedRateLimiter()
    limit = RateLimit(attempts=1, window_seconds=60)

    results = [await limiter.hit(limit, "login:ip:203.0.113.9") for _ in range(5)]

    assert all(result.allowed for result in results)


def test_the_setting_chooses_the_implementation() -> None:
    assert isinstance(build_rate_limiter(_settings()), SlowapiRateLimiter)
    assert isinstance(build_rate_limiter(_settings(rate_limit_enabled=False)), UnlimitedRateLimiter)


def test_the_login_limits_default_to_5_by_ip_and_10_by_email() -> None:
    assert LoginRateLimits.from_settings(_settings()) == LoginRateLimits(
        per_ip=RateLimit(attempts=5, window_seconds=300),
        per_email=RateLimit(attempts=10, window_seconds=600),
    )


# ------------------------------------------------------------------ aviso de subida


def _production(**overrides: object) -> Settings:
    return _settings(environment=Environment.PRODUCTION, debug=False, **overrides)


def test_production_without_a_client_ip_header_warns(caplog: pytest.LogCaptureFixture) -> None:
    """Atrás de proxy, sem o cabeçalho, todo cliente dividiria o limite do IP do proxy."""
    with caplog.at_level(logging.WARNING, logger="main"):
        create_app(_production())

    assert "CLIENT_IP_HEADER" in caplog.text


@pytest.mark.parametrize(
    "overrides",
    [{"client_ip_header": "X-Real-IP"}, {"rate_limit_enabled": False}],
    ids=["com-cabecalho", "limite-desligado"],
)
def test_production_configured_for_its_proxy_does_not_warn(
    caplog: pytest.LogCaptureFixture, overrides: dict[str, object]
) -> None:
    with caplog.at_level(logging.WARNING, logger="main"):
        create_app(_production(**overrides))

    assert "CLIENT_IP_HEADER" not in caplog.text
