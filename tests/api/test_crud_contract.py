"""Contrato de CRUD, exercitado igual em todo recurso da API.

Irmão de `test_authorization_matrix.py`, e com a mesma estrutura: aquele
responde **quem** alcança cada rota, este responde **o que ela faz**. A forma é
a mesma de propósito — uma declaração por recurso, invariantes parametrizadas
sobre ela, e uma checagem de completude que reprova o build quando um recurso
novo entra sem declaração.

O que se protege aqui não é um domínio, é a uniformidade. Um cliente que
aprendeu a falar com `/categories` precisa acertar `/transactions` de primeira:
mesmo envelope de erro, mesmo 404 para id que não existe, mesmo 422 para campo
desconhecido, mesmo PATCH que muda só o que foi mandado. Testado domínio a
domínio, cada um acerta o que o autor lembrou de cobrir — e as diferenças só
aparecem para quem consome.

`/users/me` fica de fora da parametrização: é um singleton, sem coleção nem id
no caminho, e forçá-lo neste molde exigiria um `if` em cada invariante. O
contrato dele está em `tests/integration/test_users.py`, e a checagem de
completude o declara explicitamente para que a ausência seja uma decisão, não
um esquecimento.

Uniformidade é assunto da borda HTTP, não do banco, então esta matriz roda na
suíte de API — `TestClient` contra um SQLite em memória, sem Docker (ver
`tests/api/conftest.py`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI

from schemas.base import PatchIn
from schemas.category import CategoryUpdateIn
from schemas.transaction import TransactionUpdateIn
from schemas.user import AdminUserUpdateIn
from tests.api.client import ApiClient, Response
from tests.api.factories import register_admin, register_user
from tests.factories import RegisteredUser

GHOST = "00000000-0000-0000-0000-000000000000"
"""Um UUID sintaticamente válido que não pertence a ninguém."""

MALFORMED = "nao-e-um-uuid"

UNVERSIONED_ROUTES_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json")

# Rotas que existem e não são CRUD de recurso. Declaradas para que a checagem
# de completude reprove o que for **esquecido**, e não o que foi decidido.
NOT_CRUD: set[tuple[str, str]] = {
    # Autenticação: emite e revoga token, não administra recurso.
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/logout"),
    # O próprio perfil: singleton, resolvido pelo token —
    # ver `tests/integration/test_users.py`.
    ("GET", "/api/v1/users/me"),
    ("PATCH", "/api/v1/users/me"),
    ("DELETE", "/api/v1/users/me"),
    ("POST", "/api/v1/users/me/password"),
    # Saldo: agregação de leitura sobre lançamentos, não um recurso guardado.
    # Não há o que criar, atualizar nem excluir — o contrato dele está em
    # `test_balance.py`.
    ("GET", "/api/v1/balance/current"),
    ("GET", "/api/v1/balance/monthly"),
    ("GET", "/api/v1/balance/range"),
    # Relatórios: exportação em PDF, não recurso — ver `test_reports.py`.
    ("GET", "/api/v1/reports/transactions"),
    ("GET", "/api/v1/reports/balance/monthly"),
    ("GET", "/api/v1/reports/balance/range"),
}


type Body = dict[str, Any]
type Actor = Callable[[ApiClient], RegisteredUser]
type BodyFactory = Callable[[ApiClient, RegisteredUser], Body]


def suffix() -> str:
    """Sufixo curto e único por chamada.

    Nome de categoria é único por usuário e e-mail é único no sistema: um teste
    que cria dois recursos com o corpo fixo mediria um 409 em vez do contrato.
    """
    return uuid4().hex[:8]


# ------------------------------------------------------------ corpos de criação


def a_category(_client: ApiClient, _user: RegisteredUser) -> Body:
    return {"name": f"Padaria {suffix()}", "kind": "expense"}


def a_transaction(client: ApiClient, user: RegisteredUser) -> Body:
    """Lançamento aponta para categoria, e o id dela nasce com o dono."""
    created = client.post("/api/v1/categories", headers=user.auth, json=a_category(client, user))
    created.raise_for_status()
    return {"amount": "12.34", "category_id": created.json()["id"], "description": "Feira"}


def an_account(_client: ApiClient, _user: RegisteredUser) -> Body:
    sfx = suffix()
    return {
        "email": f"novo-{sfx}@exemplo.com",
        "username": f"novo{sfx}",
        "password": "senha-bem-comprida",
        "first_name": "Novo",
        "last_name": "Usuário",
    }


# ------------------------------------------------------------------ declaração


@dataclass(frozen=True, slots=True)
class Crud:
    """Um recurso de coleção, no que ele tem de diferente dos outros."""

    name: str
    """Vira o id do teste parametrizado, então é ASCII e sem espaço.

    O pytest escapa acento no id (`transações` sai como `transa\xe7\xf5es`), e
    aí `-k transacoes` não seleciona mais nada — o recorte por recurso, que é o
    uso mais comum na linha de comando, deixaria de funcionar.
    """

    collection: str
    item: str
    """O molde do caminho, como o OpenAPI o registra — é ele que a completude compara."""

    create: BodyFactory
    patch: Body
    """O que um PATCH válido muda. A chave é o campo conferido na resposta."""

    update_schema: type[PatchIn]
    """De onde saem os campos que o teste manda como nulos.

    Lido do schema, e não escrito à mão: campo novo no contrato entra na
    verificação sozinho, sem depender de alguém lembrar de acrescentá-lo aqui.
    """

    actor: Actor
    """Quem tem direito de operar o recurso — o dono, ou o administrador."""

    paginated: bool
    """Listagem envelopada em `items`/`total`, em vez de lista crua."""

    item_read: bool = True
    """Nem todo recurso expõe `GET /{id}`; `admin/users` não expõe."""

    def url(self, resource_id: str) -> str:
        return self.item.replace("{id}", resource_id)

    def __str__(self) -> str:
        return self.name


RESOURCES = [
    Crud(
        name="categorias",
        collection="/api/v1/categories",
        item="/api/v1/categories/{id}",
        create=a_category,
        patch={"name": "Padaria e mercado"},
        update_schema=CategoryUpdateIn,
        actor=register_user,
        paginated=False,
    ),
    Crud(
        name="transacoes",
        collection="/api/v1/transactions",
        item="/api/v1/transactions/{id}",
        create=a_transaction,
        patch={"amount": "56.78"},
        update_schema=TransactionUpdateIn,
        actor=register_user,
        paginated=True,
    ),
    Crud(
        name="usuarios-admin",
        collection="/api/v1/admin/users",
        item="/api/v1/admin/users/{id}",
        create=an_account,
        patch={"first_name": "Renomeado"},
        update_schema=AdminUserUpdateIn,
        actor=register_admin,
        paginated=True,
        item_read=False,
    ),
]

ROUTE_TEMPLATES = {
    "/api/v1/categories/{id}": "/api/v1/categories/{category_id}",
    "/api/v1/transactions/{id}": "/api/v1/transactions/{transaction_id}",
    "/api/v1/admin/users/{id}": "/api/v1/admin/users/{user_id}",
}
"""O nome do parâmetro muda de rota para rota; o molde do OpenAPI usa o de lá."""


# --------------------------------------------------------------------- apoio


def create_one(client: ApiClient, user: RegisteredUser, crud: Crud) -> Body:
    response = client.post(crud.collection, headers=user.auth, json=crud.create(client, user))
    assert response.status_code == 201, f"{crud}: criação falhou — {response.text}"
    body: Body = response.json()
    return body


def listing(client: ApiClient, user: RegisteredUser, crud: Crud) -> list[Body]:
    response = client.get(crud.collection, headers=user.auth)
    assert response.status_code == 200, f"{crud}: listagem falhou — {response.text}"
    payload = response.json()
    items: list[Body] = payload["items"] if crud.paginated else payload
    return items


def read_back(client: ApiClient, user: RegisteredUser, crud: Crud, resource_id: str) -> Body | None:
    """O recurso como quem consome volta a enxergá-lo, ou `None` se sumiu.

    Passa pelo `GET /{id}` quando ele existe e pela listagem quando não —
    é o que permite `admin/users`, sem rota de detalhe, responder às mesmas
    invariantes que os outros em vez de ficar de fora delas.
    """
    if crud.item_read:
        response = client.get(crud.url(resource_id), headers=user.auth)
        if response.status_code == 404:
            return None
        assert response.status_code == 200, f"{crud}: leitura falhou — {response.text}"
        body: Body = response.json()
        return body

    for item in listing(client, user, crud):
        if item["id"] == resource_id:
            return item
    return None


def assert_envelope(response: Response, code: str | None = None) -> None:
    """Toda resposta de erro sai no mesmo formato — inclusive as do framework."""
    body = response.json()
    assert set(body) == {"error"}, f"resposta fora do envelope: {response.text}"
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["message"]
    if code is not None:
        assert body["error"]["code"] == code


def nulls_around(crud: Crud) -> Body:
    """O `patch` do recurso, com todo o resto do contrato preenchido de nulo.

    É o corpo que um formulário manda quando a pessoa mexeu num campo só.
    """
    return dict.fromkeys(crud.update_schema.model_fields) | crud.patch


# --------------------------------------------------------------- o ciclo de vida


@pytest.mark.parametrize("crud", RESOURCES, ids=str)
def test_create_then_read_returns_the_same_resource(client: ApiClient, crud: Crud) -> None:
    user = crud.actor(client)

    created = create_one(client, user, crud)
    read = read_back(client, user, crud, created["id"])

    assert read is not None, f"{crud}: o recurso criado não é alcançável"
    assert read == created, f"{crud}: leitura difere da criação"


@pytest.mark.parametrize("crud", RESOURCES, ids=str)
def test_the_created_resource_appears_in_the_listing(client: ApiClient, crud: Crud) -> None:
    user = crud.actor(client)

    created = create_one(client, user, crud)

    assert created["id"] in {item["id"] for item in listing(client, user, crud)}


@pytest.mark.parametrize("crud", RESOURCES, ids=str)
def test_delete_removes_the_resource(client: ApiClient, crud: Crud) -> None:
    user = crud.actor(client)
    created = create_one(client, user, crud)

    response = client.delete(crud.url(created["id"]), headers=user.auth)

    assert response.status_code == 204, f"{crud}: exclusão respondeu {response.status_code}"
    assert response.content == b"", f"{crud}: 204 não pode ter corpo"
    assert read_back(client, user, crud, created["id"]) is None
    assert created["id"] not in {item["id"] for item in listing(client, user, crud)}


@pytest.mark.parametrize("crud", RESOURCES, ids=str)
def test_deleting_twice_is_not_found(client: ApiClient, crud: Crud) -> None:
    """A segunda exclusão é 404, não 204: o recurso não existe mais."""
    user = crud.actor(client)
    created = create_one(client, user, crud)
    client.delete(crud.url(created["id"]), headers=user.auth)

    response = client.delete(crud.url(created["id"]), headers=user.auth)

    assert response.status_code == 404, f"{crud}: respondeu {response.status_code}"
    assert_envelope(response)


# --------------------------------------------------------------------- o PATCH


@pytest.mark.parametrize("crud", RESOURCES, ids=str)
def test_update_changes_what_was_sent_and_keeps_the_rest(client: ApiClient, crud: Crud) -> None:
    user = crud.actor(client)
    created = create_one(client, user, crud)

    response = client.patch(crud.url(created["id"]), headers=user.auth, json=crud.patch)

    assert response.status_code == 200, f"{crud}: PATCH respondeu {response.text}"
    updated = response.json()
    for field, value in crud.patch.items():
        assert updated[field] == value, f"{crud}: {field} não mudou"
    intocados = set(created) - set(crud.patch)
    assert {f: updated[f] for f in intocados} == {f: created[f] for f in intocados}


@pytest.mark.parametrize("crud", RESOURCES, ids=str)
def test_patch_ignores_the_fields_that_came_as_null(client: ApiClient, crud: Crud) -> None:
    """Nulo é "não mexa", igual a ausente — ver `schemas.base.PatchIn`.

    Sem isto, editar um campo tentava gravar NULL em todos os outros; as
    colunas são NOT NULL e a recusa do banco saía como `409 conflict`,
    obrigando a reenviar o recurso inteiro para trocar um campo.
    """
    user = crud.actor(client)
    created = create_one(client, user, crud)

    response = client.patch(crud.url(created["id"]), headers=user.auth, json=nulls_around(crud))

    assert response.status_code == 200, f"{crud}: PATCH com nulos respondeu {response.text}"
    updated = response.json()
    for field, value in crud.patch.items():
        assert updated[field] == value
    intocados = set(created) - set(crud.patch)
    assert {f: updated[f] for f in intocados} == {f: created[f] for f in intocados}


@pytest.mark.parametrize("crud", RESOURCES, ids=str)
def test_a_patch_of_only_nulls_changes_nothing(client: ApiClient, crud: Crud) -> None:
    user = crud.actor(client)
    created = create_one(client, user, crud)
    todos_nulos = dict.fromkeys(crud.update_schema.model_fields)

    response = client.patch(crud.url(created["id"]), headers=user.auth, json=todos_nulos)

    assert response.status_code == 200
    assert response.json() == created


@pytest.mark.parametrize("crud", RESOURCES, ids=str)
def test_an_empty_patch_is_a_no_op(client: ApiClient, crud: Crud) -> None:
    user = crud.actor(client)
    created = create_one(client, user, crud)

    response = client.patch(crud.url(created["id"]), headers=user.auth, json={})

    assert response.status_code == 200, f"{crud}: corpo vazio respondeu {response.text}"
    assert response.json() == created


# ------------------------------------------------------------------- as recusas


@pytest.mark.parametrize("crud", RESOURCES, ids=str)
@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
def test_an_id_that_does_not_exist_is_not_found(client: ApiClient, crud: Crud, method: str) -> None:
    if method == "GET" and not crud.item_read:
        pytest.skip(f"{crud} não expõe GET de item")
    user = crud.actor(client)

    response = client.request(
        method, crud.url(GHOST), headers=user.auth, json=crud.patch if method == "PATCH" else None
    )

    assert response.status_code == 404, f"{crud} {method}: respondeu {response.status_code}"
    assert_envelope(response)


@pytest.mark.parametrize("crud", RESOURCES, ids=str)
@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
def test_a_malformed_id_is_a_validation_error(client: ApiClient, crud: Crud, method: str) -> None:
    """422, e não 404: o caminho está mal formado, não é um recurso ausente."""
    if method == "GET" and not crud.item_read:
        pytest.skip(f"{crud} não expõe GET de item")
    user = crud.actor(client)

    response = client.request(
        method,
        crud.url(MALFORMED),
        headers=user.auth,
        json=crud.patch if method == "PATCH" else None,
    )

    assert response.status_code == 422, f"{crud} {method}: respondeu {response.status_code}"
    assert_envelope(response, code="validation_error")
    assert all(d["field"].startswith("path.") for d in response.json()["error"]["details"])


@pytest.mark.parametrize("crud", RESOURCES, ids=str)
def test_create_rejects_an_unknown_field(client: ApiClient, crud: Crud) -> None:
    """`extra="forbid"`: descartar em silêncio faria o 201 mentir."""
    user = crud.actor(client)
    body = crud.create(client, user) | {"campo_que_nao_existe": "x"}

    response = client.post(crud.collection, headers=user.auth, json=body)

    assert response.status_code == 422, f"{crud}: aceitou campo desconhecido"
    assert_envelope(response, code="validation_error")


@pytest.mark.parametrize("crud", RESOURCES, ids=str)
def test_update_rejects_an_unknown_field(client: ApiClient, crud: Crud) -> None:
    user = crud.actor(client)
    created = create_one(client, user, crud)

    response = client.patch(
        crud.url(created["id"]), headers=user.auth, json={"campo_que_nao_existe": "x"}
    )

    assert response.status_code == 422, f"{crud}: aceitou campo desconhecido"
    assert_envelope(response, code="validation_error")
    assert read_back(client, user, crud, created["id"]) == created


# -------------------------------------------------------------- forma da resposta


@pytest.mark.parametrize("crud", RESOURCES, ids=str)
def test_create_read_and_update_agree_on_the_shape(client: ApiClient, crud: Crud) -> None:
    """O mesmo recurso tem os mesmos campos nas três respostas.

    Um `POST` que devolve menos campos que o `GET` obriga quem consome a
    reconsultar o recurso que acabou de criar.
    """
    user = crud.actor(client)
    created = create_one(client, user, crud)
    read = read_back(client, user, crud, created["id"])
    updated = client.patch(crud.url(created["id"]), headers=user.auth, json=crud.patch)

    assert read is not None
    assert set(created) == set(read) == set(updated.json())


@pytest.mark.parametrize("crud", RESOURCES, ids=str)
def test_the_listing_carries_the_same_shape_as_the_item(client: ApiClient, crud: Crud) -> None:
    user = crud.actor(client)
    created = create_one(client, user, crud)

    listado = next(i for i in listing(client, user, crud) if i["id"] == created["id"])

    assert set(listado) == set(created)


@pytest.mark.parametrize("crud", [c for c in RESOURCES if c.paginated], ids=str)
def test_a_paginated_listing_reports_the_whole_filter(client: ApiClient, crud: Crud) -> None:
    """`total` é do filtro inteiro, não do que coube na página."""
    user = crud.actor(client)
    create_one(client, user, crud)
    create_one(client, user, crud)

    response = client.get(crud.collection, headers=user.auth, params={"limit": 1})

    assert response.status_code == 200
    page = response.json()
    assert set(page) == {"items", "total", "limit", "offset"}
    assert len(page["items"]) == 1
    assert page["total"] >= 2
    assert page["limit"] == 1
    assert page["offset"] == 0


# ------------------------------------------------------------------ completude


def test_every_crud_route_is_declared_in_this_matrix(app: FastAPI) -> None:
    """Impede que um recurso novo entre sem o contrato uniforme verificado.

    A enumeração vem do schema OpenAPI, não de `app.routes`: o FastAPI guarda
    routers incluídos como objetos opacos, e varrer `app.routes` devolveria uma
    lista vazia — um teste que passa sem verificar nada é pior que teste nenhum.
    """
    declared: set[tuple[str, str]] = set(NOT_CRUD)
    for crud in RESOURCES:
        template = ROUTE_TEMPLATES[crud.item]
        declared |= {
            ("GET", crud.collection),
            ("POST", crud.collection),
            ("PATCH", template),
            ("DELETE", template),
        }
        if crud.item_read:
            declared.add(("GET", template))

    registered: set[tuple[str, str]] = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if not path.startswith(UNVERSIONED_ROUTES_PREFIXES)
        for method in operations
    }

    assert registered, "nenhuma rota encontrada — a enumeração quebrou"

    missing = registered - declared
    assert not missing, (
        f"rotas sem contrato de CRUD verificado: {sorted(missing)}. "
        "Declare o recurso em RESOURCES, ou justifique a ausência em NOT_CRUD."
    )

    stale = declared - registered
    assert not stale, f"a matriz cita rotas que não existem mais: {sorted(stale)}"
