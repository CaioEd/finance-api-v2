"""O contrato de `/admin/users` que o painel de administração consome.

As duas matrizes já dizem quem alcança estas rotas e que elas criam, listam,
atualizam e excluem. O que fica aqui é o que só este recurso tem, e do que a
tela do front depende: a listagem traz a própria conta do administrador, o
papel escolhido na criação vale na hora, o conflito diz **qual** campo colidiu
(o formulário marca o campo certo pelo `code`), a exclusão leva junto o que é
da conta, e a própria conta não se edita nem se exclui por aqui.

Irmão de `tests/integration/test_admin_users.py`, que cobre o mesmo recurso
contra Postgres — paginação estável e curingas da busca. Nada abaixo depende de
SQL que o SQLite não tenha: o nome da constraint violada, que o conflito
precisa, é reposto por `tests/api/sqlite_backend.py`, que também liga as chaves
estrangeiras — sem isso a cascata da exclusão passaria sem ter acontecido.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.category import Category
from models.refresh_token import RefreshToken
from models.transaction import Transaction
from tests.api.client import ApiClient, Response
from tests.api.factories import register_admin, register_user
from tests.factories import DEFAULT_PASSWORD, RegisteredUser

USERS = "/api/v1/admin/users"
LOGIN = "/api/v1/auth/login"

USER_FIELDS = {
    "id",
    "email",
    "username",
    "first_name",
    "last_name",
    "role",
    "is_active",
    "created_at",
}


# --------------------------------------------------------------------- apoio


def new_user_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "email": "novo@exemplo.com",
        "username": "novo",
        "password": DEFAULT_PASSWORD,
        "first_name": "Novo",
        "last_name": "Usuário",
    }
    return payload | overrides


def create(client: ApiClient, admin: RegisteredUser, **overrides: Any) -> dict[str, Any]:
    """Cria pela rota e afirma o 201: o teste do *depois* não passa com a criação falhando."""
    response = client.post(USERS, headers=admin.auth, json=new_user_payload(**overrides))
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def log_in(client: ApiClient, email: str, password: str = DEFAULT_PASSWORD) -> Response:
    return client.post(LOGIN, json={"email": email, "password": password})


def bearer(response: Response) -> dict[str, str]:
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def error_code(response: Response) -> str:
    code: str = response.json()["error"]["code"]
    return code


# ------------------------------------------------------------------- listagem


def test_list_includes_the_admin_own_account(client: ApiClient) -> None:
    """A tela marca a linha de quem está logado — ela precisa vir na lista."""
    admin = register_admin(client)
    register_user(client, email="bruno@exemplo.com", username="bruno")

    response = client.get(USERS, headers=admin.auth)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert admin.id in {item["id"] for item in body["items"]}


def test_list_items_carry_exactly_the_public_fields(client: ApiClient) -> None:
    """Cada coluna da tabela vem daqui, e nenhuma coluna a mais: sem hash de senha."""
    admin = register_admin(client)

    item = client.get(USERS, headers=admin.auth).json()["items"][0]

    assert set(item) == USER_FIELDS


def test_list_search_and_filters_narrow_the_total(client: ApiClient) -> None:
    """`total` acompanha o recorte — é dele que sai o número de páginas da tela."""
    admin = register_admin(client)
    create(client, admin, email="carla@exemplo.com", username="carla", first_name="Carla")
    create(client, admin, email="davi@exemplo.com", username="davi", role="admin")
    create(client, admin, email="eva@exemplo.com", username="eva", is_active=False)

    by_name = client.get(USERS, headers=admin.auth, params={"q": "carl"}).json()
    admins = client.get(USERS, headers=admin.auth, params={"role": "admin"}).json()
    inactive = client.get(USERS, headers=admin.auth, params={"is_active": "false"}).json()

    assert [item["username"] for item in by_name["items"]] == ["carla"]
    assert by_name["total"] == 1
    assert {item["username"] for item in admins["items"]} == {"admin", "davi"}
    assert admins["total"] == 2
    assert [item["username"] for item in inactive["items"]] == ["eva"]


def test_list_page_size_is_capped(client: ApiClient) -> None:
    admin = register_admin(client)

    response = client.get(USERS, headers=admin.auth, params={"limit": 101})

    assert response.status_code == 422
    assert error_code(response) == "validation_error"


# -------------------------------------------------------------------- criação


def test_create_normalizes_email_and_username(client: ApiClient) -> None:
    """O formulário manda o que foi digitado; a unicidade vale sobre a forma normalizada."""
    admin = register_admin(client)

    created = create(client, admin, email="  Bruno@Exemplo.COM ", username=" Bruno ")

    assert created["email"] == "bruno@exemplo.com"
    assert created["username"] == "bruno"


def test_create_defaults_to_an_active_common_user(client: ApiClient) -> None:
    admin = register_admin(client)

    created = create(client, admin)

    assert created["role"] == "user"
    assert created["is_active"] is True


def test_an_admin_created_here_reaches_the_panel_at_once(client: ApiClient) -> None:
    admin = register_admin(client)
    create(client, admin, email="gestora@exemplo.com", username="gestora", role="admin")

    headers = bearer(log_in(client, "gestora@exemplo.com"))

    assert client.get(USERS, headers=headers).status_code == 200


def test_a_common_user_created_here_is_kept_out_of_the_panel(client: ApiClient) -> None:
    admin = register_admin(client)
    create(client, admin)

    headers = bearer(log_in(client, "novo@exemplo.com"))
    response = client.get(USERS, headers=headers)

    assert response.status_code == 403
    assert error_code(response) == "forbidden"


def test_an_account_created_inactive_cannot_log_in(client: ApiClient) -> None:
    admin = register_admin(client)
    create(client, admin, is_active=False)

    response = log_in(client, "novo@exemplo.com")

    assert response.status_code == 403
    assert error_code(response) == "account_inactive"


@pytest.mark.parametrize(
    ("clash", "code"),
    [
        pytest.param(
            {"email": "BRUNO@exemplo.com", "username": "outro"}, "email_taken", id="email"
        ),
        pytest.param(
            {"email": "outro@exemplo.com", "username": "Bruno"}, "username_taken", id="username"
        ),
    ],
)
def test_create_conflict_names_the_field(
    client: ApiClient, clash: dict[str, str], code: str
) -> None:
    """O `code` é o que deixa o formulário marcar o campo que colidiu, e não os dois.

    A colisão vem em outra caixa de propósito: a unicidade vale sobre a forma normalizada.
    """
    admin = register_admin(client)
    create(client, admin, email="bruno@exemplo.com", username="bruno")

    response = client.post(USERS, headers=admin.auth, json=new_user_payload(**clash))

    assert response.status_code == 409
    assert error_code(response) == code


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"password": "curta"}, id="senha-curta"),
        pytest.param({"username": "com espaço"}, id="username-invalido"),
        pytest.param({"username": "ab"}, id="username-curto"),
        pytest.param({"email": "sem-arroba"}, id="email-invalido"),
        pytest.param({"role": "superuser"}, id="papel-desconhecido"),
        pytest.param({"password_hash": "x"}, id="campo-fora-do-contrato"),
    ],
)
def test_create_rejects_what_the_contract_does_not_accept(
    client: ApiClient, overrides: dict[str, Any]
) -> None:
    admin = register_admin(client)

    response = client.post(USERS, headers=admin.auth, json=new_user_payload(**overrides))

    assert response.status_code == 422, response.text
    assert error_code(response) == "validation_error"
    assert client.get(USERS, headers=admin.auth).json()["total"] == 1, (
        "a conta foi criada assim mesmo"
    )


# ------------------------------------------------------------------ atualização


def test_promoting_and_demoting_takes_effect_on_the_next_request(client: ApiClient) -> None:
    """O papel é lido do banco a cada requisição: o token já emitido acompanha a mudança."""
    admin = register_admin(client)
    bruno = register_user(client, email="bruno@exemplo.com", username="bruno")
    target = f"{USERS}/{bruno.id}"

    promoted = client.patch(target, headers=admin.auth, json={"role": "admin"})
    reached = client.get(USERS, headers=bruno.auth)
    demoted = client.patch(target, headers=admin.auth, json={"role": "user"})
    kept_out = client.get(USERS, headers=bruno.auth)

    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"
    assert reached.status_code == 200
    assert demoted.json()["role"] == "user"
    assert kept_out.status_code == 403


def test_update_sends_back_the_whole_user(client: ApiClient) -> None:
    """A tela troca a linha pela resposta do PATCH, sem ler a lista de novo."""
    admin = register_admin(client)
    bruno = register_user(client, email="bruno@exemplo.com", username="bruno")

    response = client.patch(
        f"{USERS}/{bruno.id}", headers=admin.auth, json={"last_name": "Souza", "is_active": False}
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == USER_FIELDS
    assert body["last_name"] == "Souza"
    assert body["is_active"] is False
    assert body["email"] == "bruno@exemplo.com"


@pytest.mark.parametrize(
    ("field", "code"),
    [("email", "email_taken"), ("username", "username_taken")],
)
def test_update_conflict_names_the_field(client: ApiClient, field: str, code: str) -> None:
    admin = register_admin(client)
    register_user(client, email="bruno@exemplo.com", username="bruno")
    carla = register_user(client, email="carla@exemplo.com", username="carla")
    clash = {"email": "bruno@exemplo.com", "username": "bruno"}[field]

    response = client.patch(f"{USERS}/{carla.id}", headers=admin.auth, json={field: clash})

    assert response.status_code == 409
    assert error_code(response) == code


def test_update_does_not_take_a_password(client: ApiClient) -> None:
    """Trocar a senha de outra pessoa não é edição de cadastro — o formulário não oferece."""
    admin = register_admin(client)
    bruno = register_user(client, email="bruno@exemplo.com", username="bruno")

    response = client.patch(
        f"{USERS}/{bruno.id}", headers=admin.auth, json={"password": "outra-senha-longa"}
    )

    assert response.status_code == 422
    assert log_in(client, bruno.email, bruno.password).status_code == 200


@pytest.mark.parametrize(
    "body", [{"first_name": "Eu"}, {"role": "user"}, {"is_active": False}], ids=str
)
def test_admin_cannot_edit_their_own_account_here(client: ApiClient, body: dict[str, Any]) -> None:
    """A tela esconde o lápis na própria linha; a API recusa do mesmo jeito."""
    admin = register_admin(client)

    response = client.patch(f"{USERS}/{admin.id}", headers=admin.auth, json=body)

    assert response.status_code == 403
    assert error_code(response) == "self_target_forbidden"
    me = client.get("/api/v1/users/me", headers=admin.auth).json()
    assert (me["first_name"], me["role"], me["is_active"]) == ("Ana", "admin", True)


# -------------------------------------------------------------------- exclusão


def rows_owned_by(client: ApiClient, user_id: str) -> dict[str, int]:
    """Quantas linhas ainda pendem da conta, contadas no banco: rota nenhuma lista as de outro."""

    async def count(session: AsyncSession) -> dict[str, int]:
        owner = UUID(user_id)
        counts: dict[str, int] = {}
        for name, model in (
            ("categories", Category),
            ("transactions", Transaction),
            ("refresh_tokens", RefreshToken),
        ):
            statement = select(func.count()).select_from(model).where(model.user_id == owner)
            counts[name] = int(await session.scalar(statement) or 0)
        return counts

    return client.in_the_database(count)


def test_delete_takes_the_account_and_everything_it_owns(client: ApiClient) -> None:
    """O modal de confirmação promete que lançamentos, categorias e sessões vão junto.

    O lançamento aponta para uma categoria da própria conta de propósito: as duas
    caem na mesma instrução, e é o `NO ACTION` da chave do lançamento que deixa isso
    passar (ver `models/transaction.py`).
    """
    admin = register_admin(client)
    bruno = register_user(client, email="bruno@exemplo.com", username="bruno")
    category = client.post(
        "/api/v1/categories", headers=bruno.auth, json={"name": "Freela", "kind": "income"}
    )
    assert category.status_code == 201, category.text
    transaction = client.post(
        "/api/v1/transactions",
        headers=bruno.auth,
        json={"amount": "150.00", "category_id": category.json()["id"], "description": "Site"},
    )
    assert transaction.status_code == 201, transaction.text
    assert all(rows_owned_by(client, bruno.id).values()), "o cenário não criou o que excluir"

    response = client.delete(f"{USERS}/{bruno.id}", headers=admin.auth)

    assert response.status_code == 204
    assert not response.content
    assert rows_owned_by(client, bruno.id) == {
        "categories": 0,
        "transactions": 0,
        "refresh_tokens": 0,
    }
    listed = client.get(USERS, headers=admin.auth).json()
    assert [item["id"] for item in listed["items"]] == [admin.id]


def test_a_deleted_account_loses_access_at_once(client: ApiClient) -> None:
    """O access token ainda não venceu, mas aponta para ninguém: 401, e o login também cai."""
    admin = register_admin(client)
    bruno = register_user(client, email="bruno@exemplo.com", username="bruno")

    assert client.delete(f"{USERS}/{bruno.id}", headers=admin.auth).status_code == 204

    me = client.get("/api/v1/users/me", headers=bruno.auth)
    assert me.status_code == 401
    assert error_code(me) == "invalid_token"
    login = log_in(client, bruno.email, bruno.password)
    assert login.status_code == 401
    assert error_code(login) == "invalid_credentials"


def test_deleting_someone_already_gone_names_the_reason(client: ApiClient) -> None:
    """Outra aba já excluiu: o `code` é o que deixa a tela recarregar a lista em vez de insistir."""
    admin = register_admin(client)
    bruno = register_user(client, email="bruno@exemplo.com", username="bruno")

    first = client.delete(f"{USERS}/{bruno.id}", headers=admin.auth)
    again = client.delete(f"{USERS}/{bruno.id}", headers=admin.auth)

    assert first.status_code == 204
    assert again.status_code == 404
    assert error_code(again) == "user_not_found"


def test_admin_cannot_delete_their_own_account_here(client: ApiClient) -> None:
    """A tela esconde a lixeira na própria linha; a API recusa do mesmo jeito."""
    admin = register_admin(client)

    response = client.delete(f"{USERS}/{admin.id}", headers=admin.auth)

    assert response.status_code == 403
    assert error_code(response) == "self_target_forbidden"
    assert client.get("/api/v1/users/me", headers=admin.auth).status_code == 200
