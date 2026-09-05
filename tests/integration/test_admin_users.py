"""CRUD administrativo de usuários, ponta a ponta.

Quem pode chegar a estas rotas é assunto da matriz de autorização; aqui o que
está sob teste é o contrato: o que cada verbo devolve, o que vira conflito, e o
que acontece com quem foi desativado ou excluído.
"""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import DEFAULT_PASSWORD, register_admin, register_user

USERS = "/api/v1/admin/users"


def new_user_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "email": "novo@exemplo.com",
        "username": "novo",
        "password": DEFAULT_PASSWORD,
        "first_name": "Novo",
        "last_name": "Usuário",
    }
    return payload | overrides


# --------------------------------------------------------------------- LIST


async def test_list_returns_everyone_with_the_total(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await register_admin(client, db_session)
    await register_user(client, email="bruno@exemplo.com", username="bruno")

    response = await client.get(USERS, headers=admin.auth)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {item["email"] for item in body["items"]} == {admin.email, "bruno@exemplo.com"}
    assert body["limit"] == 50
    assert body["offset"] == 0


async def test_list_never_leaks_the_password_hash(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await register_admin(client, db_session)

    body = (await client.get(USERS, headers=admin.auth)).json()

    assert "password_hash" not in body["items"][0]
    assert "password" not in body["items"][0]


async def test_list_paginates_without_repeating_anyone(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await register_admin(client, db_session)
    for n in range(3):
        await register_user(client, email=f"user{n}@exemplo.com", username=f"user{n}")

    first = (await client.get(USERS, headers=admin.auth, params={"limit": 2})).json()
    second = (await client.get(USERS, headers=admin.auth, params={"limit": 2, "offset": 2})).json()

    assert first["total"] == second["total"] == 4
    assert len(first["items"]) == len(second["items"]) == 2
    seen = [item["id"] for item in first["items"] + second["items"]]
    assert len(set(seen)) == 4, "a mesma pessoa apareceu em duas páginas"


async def test_list_filters_by_role_and_by_state(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await register_admin(client, db_session)
    await register_user(client, email="bruno@exemplo.com", username="bruno")

    only_admins = (await client.get(USERS, headers=admin.auth, params={"role": "admin"})).json()
    actives = (await client.get(USERS, headers=admin.auth, params={"is_active": "true"})).json()

    assert only_admins["total"] == 1
    assert only_admins["items"][0]["email"] == admin.email
    assert actives["total"] == 2


async def test_list_searches_by_email_username_and_name(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await register_admin(client, db_session)
    await register_user(
        client, email="bruno@outrodominio.com", username="bruno", first_name="Bruno"
    )

    by_domain = (await client.get(USERS, headers=admin.auth, params={"q": "outrodominio"})).json()
    by_name = (await client.get(USERS, headers=admin.auth, params={"q": "bru"})).json()

    assert by_domain["total"] == 1
    assert by_name["total"] == 1
    assert by_name["items"][0]["username"] == "bruno"


async def test_search_wildcards_are_not_a_filter(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """`%` digitado é texto, não curinga: senão o filtro passa a ser do cliente."""
    admin = await register_admin(client, db_session)
    await register_user(client, email="bruno@exemplo.com", username="bruno")

    response = await client.get(USERS, headers=admin.auth, params={"q": "%"})

    assert response.json()["total"] == 0


# ------------------------------------------------------------------- CREATE


async def test_create_returns_201_and_the_account_works(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await register_admin(client, db_session)

    response = await client.post(USERS, headers=admin.auth, json=new_user_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "novo@exemplo.com"
    assert body["role"] == "user"
    assert body["is_active"] is True
    assert "password" not in body and "password_hash" not in body

    login = await client.post(
        "/api/v1/auth/login", json={"email": "novo@exemplo.com", "password": DEFAULT_PASSWORD}
    )
    assert login.status_code == 200, "a senha gravada não é a que foi enviada"


async def test_create_can_make_another_admin(client: AsyncClient, db_session: AsyncSession) -> None:
    admin = await register_admin(client, db_session)

    response = await client.post(
        USERS, headers=admin.auth, json=new_user_payload(role="admin", is_active=False)
    )

    assert response.status_code == 201
    assert response.json()["role"] == "admin"
    assert response.json()["is_active"] is False


async def test_create_with_an_email_already_taken_is_a_conflict(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await register_admin(client, db_session)

    response = await client.post(
        USERS, headers=admin.auth, json=new_user_payload(email=admin.email)
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


async def test_create_rejects_an_unknown_role(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await register_admin(client, db_session)

    response = await client.post(
        USERS, headers=admin.auth, json=new_user_payload(role="superusuario")
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


# ------------------------------------------------------------------- UPDATE


async def test_update_changes_role_and_keeps_the_rest(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await register_admin(client, db_session)
    target = await register_user(client, email="bruno@exemplo.com", username="bruno")

    response = await client.patch(
        f"{USERS}/{target.id}", headers=admin.auth, json={"role": "admin"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "admin"
    assert body["email"] == "bruno@exemplo.com"


async def test_deactivating_someone_cuts_the_access_at_once(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """O access token ainda é válido por até 15 min — quem barra é a checagem de estado."""
    admin = await register_admin(client, db_session)
    target = await register_user(client, email="bruno@exemplo.com", username="bruno")
    assert (await client.get("/api/v1/users/me", headers=target.auth)).status_code == 200

    await client.patch(f"{USERS}/{target.id}", headers=admin.auth, json={"is_active": False})

    blocked = await client.get("/api/v1/users/me", headers=target.auth)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "account_inactive"


async def test_update_ignores_the_fields_that_came_as_null(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Promover alguém não pode exigir reenviar o cadastro inteiro."""
    admin = await register_admin(client, db_session)
    target = await register_user(client, email="bruno@exemplo.com", username="bruno")

    response = await client.patch(
        f"{USERS}/{target.id}",
        headers=admin.auth,
        json={
            "email": None,
            "username": None,
            "first_name": None,
            "last_name": None,
            "role": "admin",
            "is_active": None,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "admin"
    assert body["email"] == "bruno@exemplo.com"
    assert body["username"] == "bruno"
    assert body["is_active"] is True


async def test_update_of_an_unknown_user_is_a_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await register_admin(client, db_session)

    response = await client.patch(
        f"{USERS}/{uuid4()}", headers=admin.auth, json={"is_active": False}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "user_not_found"


async def test_admin_cannot_demote_themselves(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A trava que garante que sempre sobra um administrador."""
    admin = await register_admin(client, db_session)

    response = await client.patch(f"{USERS}/{admin.id}", headers=admin.auth, json={"role": "user"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "self_target_forbidden"
    assert (await client.get("/api/v1/users/me", headers=admin.auth)).json()["role"] == "admin"


async def test_update_to_an_email_already_taken_is_a_conflict(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await register_admin(client, db_session)
    target = await register_user(client, email="bruno@exemplo.com", username="bruno")

    response = await client.patch(
        f"{USERS}/{target.id}", headers=admin.auth, json={"email": admin.email}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


# ------------------------------------------------------------------- DELETE


async def test_delete_removes_the_user_and_their_sessions(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await register_admin(client, db_session)
    target = await register_user(client, email="bruno@exemplo.com", username="bruno")

    response = await client.delete(f"{USERS}/{target.id}", headers=admin.auth)

    assert response.status_code == 204
    assert not response.content
    listed = (await client.get(USERS, headers=admin.auth)).json()
    assert [item["id"] for item in listed["items"]] == [admin.id]

    # O refresh token caiu junto, por ON DELETE CASCADE.
    revived = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": target.refresh_token}
    )
    assert revived.status_code == 401


async def test_delete_of_an_unknown_user_is_a_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await register_admin(client, db_session)

    response = await client.delete(f"{USERS}/{uuid4()}", headers=admin.auth)

    assert response.status_code == 404


async def test_admin_cannot_delete_themselves(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Existe `DELETE /users/me` para isso, e ele exige a senha."""
    admin = await register_admin(client, db_session)

    response = await client.delete(f"{USERS}/{admin.id}", headers=admin.auth)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "self_target_forbidden"
    assert (await client.get("/api/v1/users/me", headers=admin.auth)).status_code == 200
