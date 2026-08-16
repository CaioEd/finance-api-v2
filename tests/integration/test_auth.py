"""Ciclo de vida da sessão: registro, login, rotação e logout."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import DEFAULT_PASSWORD, register_user, registration_payload

CountRows = Callable[[str], Awaitable[int]]


# ------------------------------------------------------------------ registro


async def test_register_returns_tokens_and_the_user(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/register", json=registration_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert body["access_token"] and body["refresh_token"]
    assert body["user"]["email"] == "ana@exemplo.com"
    assert body["user"]["role"] == "user"
    assert body["user"]["is_active"] is True


async def test_register_never_returns_the_password_hash(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/register", json=registration_payload())

    assert "password" not in response.text
    assert "argon2" not in response.text


async def test_register_normalizes_email_and_username(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json=registration_payload(email="  ANA@Exemplo.COM ", username="ANA"),
    )

    body = response.json()["user"]
    assert body["email"] == "ana@exemplo.com"
    assert body["username"] == "ana"


async def test_duplicate_email_is_a_conflict(client: AsyncClient) -> None:
    await register_user(client)

    response = await client.post(
        "/api/v1/auth/register", json=registration_payload(username="outra")
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


async def test_duplicate_email_differing_only_in_case_is_a_conflict(client: AsyncClient) -> None:
    """A normalização é o que faz a constraint UNIQUE valer alguma coisa."""
    await register_user(client)

    response = await client.post(
        "/api/v1/auth/register",
        json=registration_payload(email="ANA@EXEMPLO.COM", username="outra"),
    )

    assert response.status_code == 409


async def test_duplicate_username_is_a_conflict(client: AsyncClient) -> None:
    await register_user(client)

    response = await client.post(
        "/api/v1/auth/register", json=registration_payload(email="outra@exemplo.com")
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "username_taken"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("password", "curta"),
        ("email", "nao-e-email"),
        ("username", "com espaço"),
    ],
)
async def test_invalid_registration_is_rejected(
    client: AsyncClient, field: str, value: str
) -> None:
    payload = registration_payload()
    payload[field] = value

    response = await client.post("/api/v1/auth/register", json=payload)

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "validation_error"
    assert any(field in detail["field"] for detail in body["details"])


# ------------------------------------------------------------------ login


async def test_login_with_email_and_password(client: AsyncClient) -> None:
    user = await register_user(client)

    response = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": user.password}
    )

    assert response.status_code == 200
    assert response.json()["access_token"]


async def test_login_accepts_email_in_any_case(client: AsyncClient) -> None:
    user = await register_user(client)

    response = await client.post(
        "/api/v1/auth/login", json={"email": "ANA@exemplo.com", "password": user.password}
    )

    assert response.status_code == 200


async def test_wrong_password_and_unknown_email_are_indistinguishable(
    client: AsyncClient,
) -> None:
    """Respostas diferentes entregariam a lista de contas cadastradas."""
    user = await register_user(client)

    wrong_password = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": "senha-errada-mas-longa"}
    )
    unknown_email = await client.post(
        "/api/v1/auth/login", json={"email": "ninguem@exemplo.com", "password": user.password}
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert wrong_password.json()["error"]["code"] == "invalid_credentials"


async def test_username_is_not_a_credential(client: AsyncClient) -> None:
    user = await register_user(client)

    response = await client.post(
        "/api/v1/auth/login", json={"email": user.username, "password": user.password}
    )

    assert response.status_code == 422


# ------------------------------------------------------------------ rotação


async def test_refresh_returns_a_new_pair(client: AsyncClient) -> None:
    user = await register_user(client)

    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": user.refresh_token})

    assert response.status_code == 200
    body = response.json()
    assert body["refresh_token"] != user.refresh_token
    assert body["access_token"]


async def test_rotated_refresh_token_stops_working(client: AsyncClient) -> None:
    user = await register_user(client)
    rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": user.refresh_token})
    new_refresh = rotated.json()["refresh_token"]

    reuse = await client.post("/api/v1/auth/refresh", json={"refresh_token": user.refresh_token})

    assert reuse.status_code == 401
    assert reuse.json()["error"]["code"] == "token_reuse_detected"
    assert new_refresh  # o sucessor existia antes do reuso


async def test_reuse_brings_down_the_whole_family(client: AsyncClient) -> None:
    """O sinal de token roubado derruba também o token legítimo em circulação.

    Não há como saber qual dos dois lados é o dono, então os dois refazem login.
    """
    user = await register_user(client)
    rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": user.refresh_token})
    successor = rotated.json()["refresh_token"]

    await client.post("/api/v1/auth/refresh", json={"refresh_token": user.refresh_token})
    after_reuse = await client.post("/api/v1/auth/refresh", json={"refresh_token": successor})

    assert after_reuse.status_code == 401


async def test_unknown_refresh_token_is_rejected(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": "nao-existe"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_refresh_token"


async def test_login_twice_creates_independent_families(client: AsyncClient) -> None:
    """Sair no celular não pode derrubar a sessão do navegador."""
    user = await register_user(client)
    second = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": user.password}
    )
    second_refresh = second.json()["refresh_token"]

    await client.post("/api/v1/auth/logout", json={"refresh_token": user.refresh_token})

    still_valid = await client.post("/api/v1/auth/refresh", json={"refresh_token": second_refresh})
    assert still_valid.status_code == 200


# ------------------------------------------------------------------ logout


async def test_logout_revokes_the_token(client: AsyncClient) -> None:
    user = await register_user(client)

    logout = await client.post("/api/v1/auth/logout", json={"refresh_token": user.refresh_token})
    reuse = await client.post("/api/v1/auth/refresh", json={"refresh_token": user.refresh_token})

    assert logout.status_code == 204
    assert reuse.status_code == 401


async def test_logout_is_idempotent(client: AsyncClient) -> None:
    """Logout que falha só ensina o cliente a ignorar o erro."""
    user = await register_user(client)

    first = await client.post("/api/v1/auth/logout", json={"refresh_token": user.refresh_token})
    second = await client.post("/api/v1/auth/logout", json={"refresh_token": user.refresh_token})
    unknown = await client.post("/api/v1/auth/logout", json={"refresh_token": "nunca-existiu"})

    assert first.status_code == second.status_code == unknown.status_code == 204


async def test_logout_does_not_delete_history(client: AsyncClient, count_rows: CountRows) -> None:
    """Revogar marca `revoked_at`; apagar a linha perderia a cadeia de rotação."""
    user = await register_user(client)
    before = await count_rows("refresh_tokens")

    await client.post("/api/v1/auth/logout", json={"refresh_token": user.refresh_token})

    assert await count_rows("refresh_tokens") == before


async def test_access_token_still_works_after_logout(client: AsyncClient) -> None:
    """Access token não é revogável — a janela de exposição é o TTL de 15 min.

    Documentado como decisão em §1.4: consultar estado a cada requisição
    anularia a razão de existir do JWT.
    """
    user = await register_user(client)
    await client.post("/api/v1/auth/logout", json={"refresh_token": user.refresh_token})

    response = await client.get("/api/v1/users/me", headers=user.auth)

    assert response.status_code == 200


async def test_password_is_not_stored_in_clear_text(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await register_user(client)

    stored = await db_session.execute(text("SELECT password_hash FROM users"))
    hashes = [row[0] for row in stored]

    assert hashes
    assert all(DEFAULT_PASSWORD not in value for value in hashes)
    assert all(value.startswith("$argon2id$") for value in hashes)
