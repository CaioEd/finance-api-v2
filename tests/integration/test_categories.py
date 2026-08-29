"""Categorias: as do sistema, as do usuário e a fronteira entre uma pessoa e outra.

O que precisa ficar provado aqui é o isolamento: a categoria de alguém não
aparece na lista de outro, nem é alcançável pelo id, nem pode ser editada ou
apagada por quem não a criou.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import register_admin, register_user

CATEGORIES = "/api/v1/categories"


async def create_category(
    client: AsyncClient, auth: dict[str, str], **overrides: Any
) -> dict[str, Any]:
    payload = {"name": "Padaria", "kind": "expense"} | overrides
    response = await client.post(CATEGORIES, headers=auth, json=payload)
    response.raise_for_status()
    body: dict[str, Any] = response.json()
    return body


# ------------------------------------------------------------ categorias do sistema


async def test_a_new_user_already_sees_the_system_categories(client: AsyncClient) -> None:
    """Elas nascem na migration, então existem antes do primeiro cadastro."""
    user = await register_user(client)

    response = await client.get(CATEGORIES, headers=user.auth)

    assert response.status_code == 200
    body = response.json()
    assert body, "nenhuma categoria do sistema encontrada"
    assert all(category["is_global"] for category in body)
    names = {category["name"] for category in body}
    assert {"Salário", "Alimentação", "Moradia"} <= names


async def test_system_categories_are_the_same_for_everyone(client: AsyncClient) -> None:
    ana = await register_user(client)
    bruno = await register_user(client, email="bruno@exemplo.com", username="bruno")

    ana_sees = await client.get(CATEGORIES, headers=ana.auth)
    bruno_sees = await client.get(CATEGORIES, headers=bruno.auth)

    assert ana_sees.json() == bruno_sees.json()


async def test_a_system_category_is_read_only_for_a_regular_user(client: AsyncClient) -> None:
    """403, não 404: ela é visível para todos, então esconder que existe não protege nada."""
    user = await register_user(client)
    listed = (await client.get(CATEGORIES, headers=user.auth)).json()
    global_id = listed[0]["id"]

    updated = await client.patch(
        f"{CATEGORIES}/{global_id}", headers=user.auth, json={"name": "Minha versão"}
    )
    deleted = await client.delete(f"{CATEGORIES}/{global_id}", headers=user.auth)

    assert updated.status_code == 403
    assert deleted.status_code == 403
    assert updated.json()["error"]["code"] == "forbidden"
    still_there = await client.get(f"{CATEGORIES}/{global_id}", headers=user.auth)
    assert still_there.json()["name"] == listed[0]["name"]


# ------------------------------------------------------------ o administrador


async def test_an_admin_renames_a_system_category_for_everyone(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await register_admin(client, db_session)
    global_id = next(
        category["id"]
        for category in (await client.get(CATEGORIES, headers=admin.auth)).json()
        if category["name"] == "Lazer"
    )

    response = await client.patch(
        f"{CATEGORIES}/{global_id}", headers=admin.auth, json={"name": "Lazer e cultura"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Lazer e cultura"
    assert body["is_global"] is True, "a categoria virou privada do admin ao ser editada"

    outro = await register_user(client, email="bruno@exemplo.com", username="bruno")
    listed = (await client.get(CATEGORIES, headers=outro.auth)).json()
    assert "Lazer e cultura" in {category["name"] for category in listed}


async def test_an_admin_deletes_a_system_category(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    admin = await register_admin(client, db_session)
    listed = (await client.get(CATEGORIES, headers=admin.auth)).json()
    global_id = listed[0]["id"]

    response = await client.delete(f"{CATEGORIES}/{global_id}", headers=admin.auth)

    assert response.status_code == 204
    remaining = (await client.get(CATEGORIES, headers=admin.auth)).json()
    assert len(remaining) == len(listed) - 1


async def test_an_admin_cannot_repeat_an_existing_system_name(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """O índice parcial das globais vale para o admin como para qualquer um."""
    admin = await register_admin(client, db_session)
    listed = (await client.get(CATEGORIES, headers=admin.auth)).json()
    moradia = next(category for category in listed if category["name"] == "Moradia")
    alimentacao = next(category for category in listed if category["name"] == "Alimentação")

    response = await client.patch(
        f"{CATEGORIES}/{alimentacao['id']}", headers=admin.auth, json={"name": moradia["name"]}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "category_name_taken"


async def test_an_admin_still_cannot_reach_a_category_of_another_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Ser admin dá a lista global, não a lista privada de ninguém — isso é a fase 6."""
    ana = await register_user(client)
    admin = await register_admin(client, db_session, email="chefe@exemplo.com", username="chefe")
    da_ana = await create_category(client, ana.auth, name="Terapia")

    read = await client.get(f"{CATEGORIES}/{da_ana['id']}", headers=admin.auth)
    updated = await client.patch(
        f"{CATEGORIES}/{da_ana['id']}", headers=admin.auth, json={"name": "Outra coisa"}
    )
    listed = (await client.get(CATEGORIES, headers=admin.auth)).json()

    assert [read.status_code, updated.status_code] == [404, 404]
    assert "Terapia" not in {category["name"] for category in listed}


# ------------------------------------------------------------ categorias do usuário


async def test_create_returns_the_category_as_the_users_own(client: AsyncClient) -> None:
    user = await register_user(client)

    response = await client.post(
        CATEGORIES, headers=user.auth, json={"name": "Academia", "kind": "expense"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Academia"
    assert body["kind"] == "expense"
    assert body["is_global"] is False
    assert set(body) == {"id", "name", "kind", "is_global", "created_at"}


async def test_created_category_shows_up_next_to_the_system_ones(client: AsyncClient) -> None:
    user = await register_user(client)
    before = len((await client.get(CATEGORIES, headers=user.auth)).json())

    await create_category(client, user.auth, name="Academia")

    listed = (await client.get(CATEGORIES, headers=user.auth)).json()
    assert len(listed) == before + 1
    mine = [category for category in listed if not category["is_global"]]
    assert [category["name"] for category in mine] == ["Academia"]


async def test_the_name_is_normalized_before_it_is_stored(client: AsyncClient) -> None:
    user = await register_user(client)

    body = await create_category(client, user.auth, name="  Plano   de saúde  ")

    assert body["name"] == "Plano de saúde"


async def test_kind_filters_the_listing(client: AsyncClient) -> None:
    user = await register_user(client)
    await create_category(client, user.auth, name="Aulas de inglês", kind="expense")
    await create_category(client, user.auth, name="Dividendos", kind="income")

    response = await client.get(CATEGORIES, headers=user.auth, params={"kind": "income"})

    assert response.status_code == 200
    body = response.json()
    assert all(category["kind"] == "income" for category in body)
    assert "Dividendos" in {category["name"] for category in body}


async def test_an_unknown_kind_is_rejected(client: AsyncClient) -> None:
    user = await register_user(client)

    response = await client.get(CATEGORIES, headers=user.auth, params={"kind": "transferencia"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_update_changes_only_what_was_sent(client: AsyncClient) -> None:
    user = await register_user(client)
    category = await create_category(client, user.auth, name="Padaria", kind="expense")

    response = await client.patch(
        f"{CATEGORIES}/{category['id']}", headers=user.auth, json={"name": "Padaria e mercado"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Padaria e mercado"
    assert body["kind"] == "expense"


async def test_delete_removes_the_category(client: AsyncClient) -> None:
    user = await register_user(client)
    category = await create_category(client, user.auth)

    response = await client.delete(f"{CATEGORIES}/{category['id']}", headers=user.auth)

    assert response.status_code == 204
    gone = await client.get(f"{CATEGORIES}/{category['id']}", headers=user.auth)
    assert gone.status_code == 404


async def test_an_unknown_id_is_not_found(client: AsyncClient) -> None:
    user = await register_user(client)

    response = await client.get(f"{CATEGORIES}/{uuid4()}", headers=user.auth)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# ------------------------------------------------------------ unicidade do nome


async def test_the_same_name_twice_is_a_conflict(client: AsyncClient) -> None:
    user = await register_user(client)
    await create_category(client, user.auth, name="Padaria")

    response = await client.post(
        CATEGORIES, headers=user.auth, json={"name": "Padaria", "kind": "expense"}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "category_name_taken"


async def test_the_same_name_in_another_case_is_also_a_conflict(client: AsyncClient) -> None:
    """Senão "Mercado" e "mercado" convivem na mesma lista como coisas diferentes."""
    user = await register_user(client)
    await create_category(client, user.auth, name="Mercado")

    response = await client.post(
        CATEGORIES, headers=user.auth, json={"name": "MERCADO", "kind": "expense"}
    )

    assert response.status_code == 409


async def test_a_system_name_can_be_reused_by_a_user(client: AsyncClient) -> None:
    """A unicidade é por dono: a lista global de um não é a lista privada do outro."""
    user = await register_user(client)

    response = await client.post(
        CATEGORIES, headers=user.auth, json={"name": "Moradia", "kind": "expense"}
    )

    assert response.status_code == 201


async def test_the_same_name_in_the_other_kind_is_allowed(client: AsyncClient) -> None:
    user = await register_user(client)
    await create_category(client, user.auth, name="Investimentos próprios", kind="expense")

    response = await client.post(
        CATEGORIES, headers=user.auth, json={"name": "Investimentos próprios", "kind": "income"}
    )

    assert response.status_code == 201


async def test_renaming_onto_an_existing_name_is_a_conflict(client: AsyncClient) -> None:
    user = await register_user(client)
    await create_category(client, user.auth, name="Padaria")
    outra = await create_category(client, user.auth, name="Farmácia")

    response = await client.patch(
        f"{CATEGORIES}/{outra['id']}", headers=user.auth, json={"name": "Padaria"}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "category_name_taken"


# ------------------------------------------------------------ isolamento entre usuários


async def test_a_category_of_another_user_is_invisible(client: AsyncClient) -> None:
    ana = await register_user(client)
    bruno = await register_user(client, email="bruno@exemplo.com", username="bruno")
    await create_category(client, ana.auth, name="Terapia")

    listed = (await client.get(CATEGORIES, headers=bruno.auth)).json()

    assert "Terapia" not in {category["name"] for category in listed}
    assert all(category["is_global"] for category in listed)


async def test_reaching_another_users_category_by_id_answers_404(client: AsyncClient) -> None:
    """404 e não 403: um 403 confirmaria que aquele id existe."""
    ana = await register_user(client)
    bruno = await register_user(client, email="bruno@exemplo.com", username="bruno")
    da_ana = await create_category(client, ana.auth, name="Terapia")

    read = await client.get(f"{CATEGORIES}/{da_ana['id']}", headers=bruno.auth)
    updated = await client.patch(
        f"{CATEGORIES}/{da_ana['id']}", headers=bruno.auth, json={"name": "Sequestrada"}
    )
    deleted = await client.delete(f"{CATEGORIES}/{da_ana['id']}", headers=bruno.auth)

    assert [read.status_code, updated.status_code, deleted.status_code] == [404, 404, 404]
    assert (await client.get(f"{CATEGORIES}/{da_ana['id']}", headers=ana.auth)).json() == da_ana


async def test_two_users_can_have_a_category_with_the_same_name(client: AsyncClient) -> None:
    ana = await register_user(client)
    bruno = await register_user(client, email="bruno@exemplo.com", username="bruno")

    await create_category(client, ana.auth, name="Padaria")
    response = await client.post(
        CATEGORIES, headers=bruno.auth, json={"name": "Padaria", "kind": "expense"}
    )

    assert response.status_code == 201


async def test_deleting_the_account_takes_its_categories(
    client: AsyncClient, count_rows: Callable[[str], Awaitable[int]]
) -> None:
    """FK com ON DELETE CASCADE: nada do usuário fica para trás."""
    user = await register_user(client)
    system_categories = await count_rows("categories")
    await create_category(client, user.auth, name="Terapia")
    assert await count_rows("categories") == system_categories + 1

    await client.request(
        "DELETE", "/api/v1/users/me", headers=user.auth, json={"password": user.password}
    )

    assert await count_rows("categories") == system_categories
