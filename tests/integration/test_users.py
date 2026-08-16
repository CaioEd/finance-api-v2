"""Perfil próprio: leitura, edição, troca de senha e exclusão da conta."""

from __future__ import annotations

from httpx import AsyncClient

from tests.factories import register_user


async def test_me_returns_the_authenticated_user(client: AsyncClient) -> None:
    user = await register_user(client)

    response = await client.get("/api/v1/users/me", headers=user.auth)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == user.id
    assert body["email"] == user.email
    assert set(body) == {
        "id",
        "email",
        "username",
        "first_name",
        "last_name",
        "role",
        "is_active",
        "created_at",
    }


async def test_patch_updates_only_what_was_sent(client: AsyncClient) -> None:
    user = await register_user(client)

    response = await client.patch(
        "/api/v1/users/me", headers=user.auth, json={"first_name": "Ana Paula"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["first_name"] == "Ana Paula"
    assert body["last_name"] == "Ribeiro"
    assert body["email"] == user.email


async def test_patch_rejects_unknown_fields(client: AsyncClient) -> None:
    """`role` e `is_active` não são editáveis pelo próprio usuário."""
    user = await register_user(client)

    response = await client.patch("/api/v1/users/me", headers=user.auth, json={"role": "admin"})

    assert response.status_code == 422
    assert (await client.get("/api/v1/users/me", headers=user.auth)).json()["role"] == "user"


async def test_patch_to_an_email_already_taken_is_a_conflict(client: AsyncClient) -> None:
    await register_user(client, email="bruno@exemplo.com", username="bruno")
    ana = await register_user(client)

    response = await client.patch(
        "/api/v1/users/me", headers=ana.auth, json={"email": "bruno@exemplo.com"}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


async def test_password_change_revokes_every_session(client: AsyncClient) -> None:
    """Trocar senha é o que se faz ao suspeitar de invasão.

    Manter as sessões antigas vivas transformaria o gesto em nada.
    """
    user = await register_user(client)
    other_login = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": user.password}
    )
    other_refresh = other_login.json()["refresh_token"]

    changed = await client.post(
        "/api/v1/users/me/password",
        headers=user.auth,
        json={"current_password": user.password, "new_password": "nova-senha-comprida"},
    )

    assert changed.status_code == 204
    for token in (user.refresh_token, other_refresh):
        refused = await client.post("/api/v1/auth/refresh", json={"refresh_token": token})
        assert refused.status_code == 401


async def test_password_change_requires_the_current_password(client: AsyncClient) -> None:
    user = await register_user(client)

    response = await client.post(
        "/api/v1/users/me/password",
        headers=user.auth,
        json={"current_password": "nao-e-a-senha-atual", "new_password": "nova-senha-comprida"},
    )

    assert response.status_code == 401


async def test_login_works_with_the_new_password(client: AsyncClient) -> None:
    user = await register_user(client)
    await client.post(
        "/api/v1/users/me/password",
        headers=user.auth,
        json={"current_password": user.password, "new_password": "nova-senha-comprida"},
    )

    with_new = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": "nova-senha-comprida"}
    )
    with_old = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": user.password}
    )

    assert with_new.status_code == 200
    assert with_old.status_code == 401


async def test_delete_account_requires_the_password(client: AsyncClient) -> None:
    user = await register_user(client)

    response = await client.request(
        "DELETE", "/api/v1/users/me", headers=user.auth, json={"password": "senha-errada-longa"}
    )

    assert response.status_code == 401
    assert (await client.get("/api/v1/users/me", headers=user.auth)).status_code == 200


async def test_delete_account_removes_the_user_and_its_sessions(client: AsyncClient) -> None:
    user = await register_user(client)

    response = await client.request(
        "DELETE", "/api/v1/users/me", headers=user.auth, json={"password": user.password}
    )

    assert response.status_code == 204
    assert (await client.get("/api/v1/users/me", headers=user.auth)).status_code == 401
    refused = await client.post("/api/v1/auth/refresh", json={"refresh_token": user.refresh_token})
    assert refused.status_code == 401
