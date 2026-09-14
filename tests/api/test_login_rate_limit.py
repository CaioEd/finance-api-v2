"""O limite de tentativas do login, pela aplicação montada.

A regra — o que conta, em que ordem, o que a tentativa barrada deixa de fazer —
está em `tests/unit/test_auth_service.py`; o resolvedor de IP e o contador, em
`tests/unit/test_rate_limit.py`. Aqui fica o que só a aplicação inteira mostra:
o 429 no envelope com `Retry-After`, o IP lido do cabeçalho que as Settings
mandam ler, e os números vindos delas.

Cada teste ganha uma aplicação nova (ver `conftest.py`) e, com ela, um
limitador zerado: nenhum teste herda tentativas de outro.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.config import Settings
from tests.api.client import ApiClient, Response
from tests.api.factories import register_user

LOGIN = "/api/v1/auth/login"
WRONG_PASSWORD = "senha-errada-mas-longa"


@pytest.fixture
def settings(settings: Settings, request: pytest.FixtureRequest) -> Settings:
    """Os limites fixados aqui, e não herdados do `.env` de quem roda a suíte.

    O cabeçalho vem ligado como atrás de um ALB — um proxy, `X-Forwarded-For` —,
    porque é o que permite simular clientes de IPs diferentes. Teste que precisa
    de outra configuração a pede por `indirect`.
    """
    overrides: dict[str, Any] = getattr(request, "param", {})
    return settings.model_copy(
        update={
            "rate_limit_enabled": True,
            "rate_limit_storage_url": "memory://",
            "rate_limit_strategy": "moving-window",
            "rate_limit_login_ip_attempts": 5,
            "rate_limit_login_ip_window_seconds": 300,
            "rate_limit_login_email_attempts": 10,
            "rate_limit_login_email_window_seconds": 600,
            "client_ip_header": "X-Forwarded-For",
            "trusted_proxy_count": 1,
            **overrides,
        }
    )


def attempt(
    client: ApiClient,
    email: str,
    password: str = WRONG_PASSWORD,
    *,
    forwarded_for: str | None = None,
) -> Response:
    headers = {"X-Forwarded-For": forwarded_for} if forwarded_for else {}
    return client.post(LOGIN, json={"email": email, "password": password}, headers=headers)


def assert_refused(response: Response, *, window_seconds: int) -> None:
    assert response.status_code == 429, response.text
    assert response.json() == {
        "error": {
            "code": "too_many_attempts",
            "message": "Muitas tentativas. Aguarde antes de tentar de novo.",
            "details": [],
        }
    }
    assert 0 < int(response.headers["Retry-After"]) <= window_seconds


def test_the_sixth_attempt_from_the_same_ip_is_refused(client: ApiClient) -> None:
    user = register_user(client)

    first_five = [attempt(client, user.email).status_code for _ in range(5)]
    sixth = attempt(client, user.email, user.password)

    assert first_five == [401] * 5
    assert_refused(sixth, window_seconds=300)


def test_the_ip_limit_counts_every_email_it_tries(client: ApiClient) -> None:
    """Uma senha contra muitas contas (password spraying) esbarra no limite do IP."""
    user = register_user(client)

    for n in range(5):
        assert attempt(client, f"conta{n}@exemplo.com").status_code == 401

    assert_refused(attempt(client, user.email, user.password), window_seconds=300)


def test_right_passwords_count_too(client: ApiClient) -> None:
    user = register_user(client)

    for _ in range(5):
        assert attempt(client, user.email, user.password).status_code == 200

    assert_refused(attempt(client, user.email, user.password), window_seconds=300)


def test_the_email_limit_holds_across_ips(client: ApiClient) -> None:
    """Dez IPs, uma tentativa cada, contra a mesma conta: o décimo primeiro é barrado."""
    ana = register_user(client)
    bruno = register_user(client, email="bruno@exemplo.com", username="bruno")

    for n in range(10):
        assert attempt(client, ana.email, forwarded_for=f"198.51.100.{n}").status_code == 401

    assert_refused(
        attempt(client, ana.email, ana.password, forwarded_for="203.0.113.1"), window_seconds=600
    )
    # A conta barrada é a atacada; a do vizinho, de um IP novo, continua entrando.
    assert attempt(client, bruno.email, bruno.password, forwarded_for="203.0.113.2").is_success


def test_the_email_limit_ignores_the_case_of_the_address(client: ApiClient) -> None:
    user = register_user(client)

    for n in range(10):
        email = user.email.upper() if n % 2 else user.email
        assert attempt(client, email, forwarded_for=f"198.51.100.{n}").status_code == 401

    assert_refused(attempt(client, user.email, forwarded_for="203.0.113.1"), window_seconds=600)


def test_forging_the_start_of_forwarded_for_does_not_reset_the_count(client: ApiClient) -> None:
    """O proxy acrescenta o IP real à direita; o que o cliente escreve à esquerda não conta."""
    user = register_user(client)

    for n in range(5):
        forged = f"10.0.0.{n}, 203.0.113.9"
        assert attempt(client, user.email, forwarded_for=forged).status_code == 401

    refused = attempt(client, user.email, user.password, forwarded_for="10.9.9.9, 203.0.113.9")
    assert_refused(refused, window_seconds=300)


@pytest.mark.parametrize("settings", [{"client_ip_header": ""}], indirect=True)
def test_without_a_proxy_declared_forwarded_for_is_ignored(client: ApiClient) -> None:
    """Sem proxy na frente, `X-Forwarded-For` é só um cabeçalho que o cliente escreveu."""
    user = register_user(client)

    for n in range(5):
        assert attempt(client, user.email, forwarded_for=f"198.51.100.{n}").status_code == 401

    assert_refused(attempt(client, user.email, forwarded_for="203.0.113.1"), window_seconds=300)


def test_only_login_is_limited(client: ApiClient) -> None:
    """Registro e refresh continuam abertos para o IP que estourou o login."""
    user = register_user(client)
    for _ in range(6):
        attempt(client, user.email)

    registered = client.post(
        "/api/v1/auth/register",
        json={"email": "bruno@exemplo.com", "username": "bruno", "password": WRONG_PASSWORD},
    )
    refreshed = client.post("/api/v1/auth/refresh", json={"refresh_token": user.refresh_token})

    assert (registered.status_code, refreshed.status_code) == (201, 200)


@pytest.mark.parametrize("settings", [{"rate_limit_login_ip_attempts": 2}], indirect=True)
def test_the_limits_come_from_the_settings(client: ApiClient) -> None:
    user = register_user(client)

    assert [attempt(client, user.email).status_code for _ in range(2)] == [401, 401]
    assert_refused(attempt(client, user.email), window_seconds=300)


@pytest.mark.parametrize("settings", [{"rate_limit_enabled": False}], indirect=True)
def test_with_the_limit_disabled_no_attempt_is_refused(client: ApiClient) -> None:
    user = register_user(client)

    statuses = {attempt(client, user.email).status_code for _ in range(12)}

    assert statuses == {401}


@pytest.mark.parametrize(
    "settings", [{"cors_origins": ["http://front.exemplo.com"]}], indirect=True
)
def test_a_front_on_another_origin_can_read_how_long_to_wait(client: ApiClient) -> None:
    """Sem `expose_headers`, o navegador esconde o `Retry-After` do JavaScript."""
    user = register_user(client)
    origin = {"Origin": "http://front.exemplo.com"}
    for _ in range(5):
        client.post(LOGIN, json={"email": user.email, "password": WRONG_PASSWORD}, headers=origin)

    refused = client.post(
        LOGIN, json={"email": user.email, "password": WRONG_PASSWORD}, headers=origin
    )

    assert refused.status_code == 429
    assert "Retry-After" in refused.headers["Access-Control-Expose-Headers"]
