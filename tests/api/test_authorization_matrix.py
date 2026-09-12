"""Matriz de autorização.

Os problemas 4 e 5 do sistema antigo (endpoints sem autenticação e CRUD de
usuários aberto a qualquer um) não vieram de uma decisão errada: vieram de
esquecimento em rota nova. Um teste que só cobre as rotas que alguém lembrou de
cobrir repetiria o erro.

Por isso são duas coisas:

1. **A matriz** declara, rota a rota, o que cada persona deve receber. São três
   personas — anônimo, autenticado comum e administrador — e as rotas se dividem
   em `PUBLIC_ROUTES` (existem para quem não tem token), `PROTECTED_ROUTES`
   (basta autenticar) e `ADMIN_ROUTES` (exigem o papel):

   | | pública | protegida | de admin |
   |---|---|---|---|
   | **anônimo** | passa | 401 | 401 |
   | **autenticado** | passa | passa | 403 |
   | **administrador** | passa | passa | passa |

   As nove casas são verificadas. A linha das públicas é o que faltava: elas só
   constavam da checagem de completude, e nada afirmava que ainda funcionam sem
   token — uma dependência de autenticação acrescentada por engano ao router de
   `/auth` trancaria todo mundo do lado de fora sem reprovar o build.
2. **A checagem de completude** compara a matriz com as rotas realmente
   registradas na aplicação. Rota nova sem entrada aqui reprova o build, e a
   forma mais rápida de fazer o build passar é dizer quem pode acessá-la.

Quem alcança uma rota é decidido pelas dependências do router, não pelo banco,
então a matriz roda na suíte de API — `TestClient` contra um SQLite em memória,
sem Docker (ver `tests/api/conftest.py`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI

from tests.api.client import ApiClient
from tests.api.factories import register_admin, register_user
from tests.factories import RegisteredUser, registration_payload

ANONYMOUS = "anônimo"
OWNER = "dono"

type AnonymousBody = Callable[[ApiClient], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class PublicRoute:
    """Rota que existe justamente para quem ainda não tem token.

    O corpo é montado por uma função, e não escrito aqui: login e refresh
    precisam de uma conta que exista, e o token dela não é conhecido antes de a
    requisição de registro acontecer. Sem isso, o teste mediria o `401` de
    credencial errada e concluiria — errado — que a rota exige autenticação.
    """

    method: str
    path: str
    body: AnonymousBody
    expected: int

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)

    def __str__(self) -> str:
        return f"{self.method} {self.path}"


def a_registration(_client: ApiClient) -> dict[str, Any]:
    return registration_payload()


def credentials_of_an_existing_account(client: ApiClient) -> dict[str, Any]:
    user = register_user(client)
    return {"email": user.email, "password": user.password}


def a_valid_refresh_token(client: ApiClient) -> dict[str, Any]:
    user = register_user(client)
    return {"refresh_token": user.refresh_token}


PUBLIC_ROUTES = [
    PublicRoute("POST", "/api/v1/auth/register", a_registration, expected=201),
    PublicRoute("POST", "/api/v1/auth/login", credentials_of_an_existing_account, expected=200),
    PublicRoute("POST", "/api/v1/auth/refresh", a_valid_refresh_token, expected=200),
    PublicRoute("POST", "/api/v1/auth/logout", a_valid_refresh_token, expected=204),
]

# Um id sintático válido que não pertence a ninguém: serve para montar a URL
# de quem nem token tem, onde a autorização decide antes de o alvo importar.
NOBODY = "00000000-0000-0000-0000-000000000000"

# Rotas operacionais e de documentação, fora do contrato de produto.
UNVERSIONED_ROUTES_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json")


PATH_PARAMETER = re.compile(r"\{[^}]+\}")

type Setup = Callable[[ApiClient, RegisteredUser], str]
type BodySetup = Callable[[ApiClient, RegisteredUser], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ProtectedRoute:
    method: str
    path: str
    """O template, como o OpenAPI o registra — é ele que a completude compara."""

    body: dict[str, Any] | None = None
    setup: Setup | None = None
    """Rota com parâmetro no caminho: cria o recurso e devolve o caminho concreto."""

    body_setup: BodySetup | None = None
    """Rota cujo corpo referencia outro recurso: monta o corpo para o dono.

    Lançamento aponta para uma categoria, e o id dela não é conhecido antes
    de o dono existir — nem o das categorias do sistema, que nascem com o id
    que o banco sorteia. O `body` estático continua servindo aos testes de
    anônimo e de token inválido, onde a autorização decide antes de o corpo ser
    validado.
    """

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)

    def url(self, target_id: str = NOBODY) -> str:
        """O `path` guarda o molde do OpenAPI; a chamada precisa de um id real.

        Sem um id, `/users/{user_id}` viraria `/users/`, e o FastAPI responderia
        307 para a rota de listagem — o teste passaria a medir o redirecionamento
        em vez da autorização. O default basta a quem decide a autorização antes
        de o alvo importar: sem token, o id nunca chega a ser consultado.

        A substituição é por expressão regular, e não `str.format`, porque o
        nome do parâmetro muda de rota para rota (`user_id`, `category_id`) e
        um `format` estoura em todo nome que não fosse o esperado.
        """
        return PATH_PARAMETER.sub(target_id, self.path)

    def owner_path(self, client: ApiClient, user: RegisteredUser) -> str:
        """O caminho do recurso do próprio usuário, criando-o antes se preciso.

        Categoria não se alcança por um id qualquer: o dono precisa ter criado a
        dele, senão o teste do dono mediria um 404 em vez da autorização.
        """
        if self.setup is None:
            return self.url()
        return self.setup(client, user)

    def owner_body(self, client: ApiClient, user: RegisteredUser) -> dict[str, Any] | None:
        """O corpo do próprio usuário, montado antes se ele referenciar outro recurso."""
        if self.body_setup is None:
            return self.body
        return self.body_setup(client, user)

    def __str__(self) -> str:
        return f"{self.method} {self.path}"


def a_category_id_of(client: ApiClient, user: RegisteredUser) -> str:
    response = client.post(
        "/api/v1/categories", headers=user.auth, json={"name": "Padaria", "kind": "expense"}
    )
    response.raise_for_status()
    return str(response.json()["id"])


def a_category_of(client: ApiClient, user: RegisteredUser) -> str:
    return f"/api/v1/categories/{a_category_id_of(client, user)}"


def a_transaction_body_of(client: ApiClient, user: RegisteredUser) -> dict[str, Any]:
    return {"amount": "12.34", "category_id": a_category_id_of(client, user)}


def a_transaction_of(client: ApiClient, user: RegisteredUser) -> str:
    response = client.post(
        "/api/v1/transactions", headers=user.auth, json=a_transaction_body_of(client, user)
    )
    response.raise_for_status()
    return f"/api/v1/transactions/{response.json()['id']}"


DATE_RANGE_QUERY = "occurred_from=2026-01-01&occurred_to=2026-12-31"


def a_date_range(_client: ApiClient, _user: RegisteredUser) -> str:
    """`/balance/range` exige as duas pontas, e o `setup` é o que as fornece.

    Sem elas a rota responderia 422 ao próprio dono, e o teste passaria a medir
    a validação do parâmetro em vez da autorização. Para o anônimo o caminho nu
    basta: a autenticação decide antes de o parâmetro ser lido.
    """
    return f"/api/v1/balance/range?{DATE_RANGE_QUERY}"


def a_report_date_range(_client: ApiClient, _user: RegisteredUser) -> str:
    """O mesmo de `a_date_range`, na rota que exporta aquele saldo em PDF."""
    return f"/api/v1/reports/balance/range?{DATE_RANGE_QUERY}"


PROTECTED_ROUTES = [
    ProtectedRoute("GET", "/api/v1/users/me"),
    ProtectedRoute("PATCH", "/api/v1/users/me", body={"first_name": "X"}),
    ProtectedRoute(
        "POST",
        "/api/v1/users/me/password",
        body={"current_password": "senha-bem-comprida", "new_password": "outra-senha-longa"},
    ),
    ProtectedRoute("DELETE", "/api/v1/users/me", body={"password": "senha-bem-comprida"}),
    ProtectedRoute("GET", "/api/v1/categories"),
    ProtectedRoute("POST", "/api/v1/categories", body={"name": "Padaria", "kind": "expense"}),
    ProtectedRoute("GET", "/api/v1/categories/{category_id}", setup=a_category_of),
    ProtectedRoute(
        "PATCH",
        "/api/v1/categories/{category_id}",
        body={"name": "Padaria e mercado"},
        setup=a_category_of,
    ),
    ProtectedRoute("DELETE", "/api/v1/categories/{category_id}", setup=a_category_of),
    ProtectedRoute("GET", "/api/v1/transactions"),
    ProtectedRoute(
        "POST",
        "/api/v1/transactions",
        body={"amount": "12.34", "category_id": NOBODY},
        body_setup=a_transaction_body_of,
    ),
    ProtectedRoute("GET", "/api/v1/transactions/{transaction_id}", setup=a_transaction_of),
    ProtectedRoute(
        "PATCH",
        "/api/v1/transactions/{transaction_id}",
        body={"amount": "56.78"},
        setup=a_transaction_of,
    ),
    ProtectedRoute("DELETE", "/api/v1/transactions/{transaction_id}", setup=a_transaction_of),
    ProtectedRoute("GET", "/api/v1/balance/current"),
    ProtectedRoute("GET", "/api/v1/balance/monthly"),
    ProtectedRoute("GET", "/api/v1/balance/range", setup=a_date_range),
    # Relatórios: leem o mesmo recorte das rotas acima e devolvem PDF. Autenticar
    # basta — não há relatório de dado de terceiro, porque o escopo por dono
    # continua vindo do repositório por trás dos mesmos serviços.
    ProtectedRoute("GET", "/api/v1/reports/transactions"),
    ProtectedRoute("GET", "/api/v1/reports/balance/monthly"),
    ProtectedRoute("GET", "/api/v1/reports/balance/range", setup=a_report_date_range),
]

# Autenticar não basta: estas exigem o papel de administrador.
ADMIN_ROUTES = [
    ProtectedRoute("GET", "/api/v1/admin/users"),
    ProtectedRoute(
        "POST",
        "/api/v1/admin/users",
        body={
            "email": "novo@exemplo.com",
            "username": "novo",
            "password": "senha-bem-comprida",
        },
    ),
    ProtectedRoute("PATCH", "/api/v1/admin/users/{user_id}", body={"first_name": "X"}),
    ProtectedRoute("DELETE", "/api/v1/admin/users/{user_id}"),
]


@pytest.mark.parametrize("route", PUBLIC_ROUTES, ids=str)
def test_public_route_works_without_a_token(client: ApiClient, route: PublicRoute) -> None:
    """A linha que faltava na matriz: público continua público.

    Bastaria alguém acrescentar `dependencies=[Depends(get_current_user)]` ao
    router de `/auth` — como os outros routers têm, por ser o default do
    projeto — para ninguém conseguir mais entrar. Nenhum outro teste pegaria
    isso: os fixtures registram e logam para *chegar* nas rotas protegidas, e
    falhariam com um erro que aponta para o lugar errado.
    """
    response = client.request(route.method, route.path, json=route.body(client))

    assert response.status_code == route.expected, (
        f"{route} respondeu {response.status_code} sem token: {response.text}"
    )


@pytest.mark.parametrize("route", PUBLIC_ROUTES, ids=str)
def test_public_route_ignores_a_garbage_token(client: ApiClient, route: PublicRoute) -> None:
    """Um token velho no cliente não pode impedir o login que o renovaria."""
    body = route.body(client)
    headers = {"Authorization": "Bearer nao-e-um-token"}

    response = client.request(route.method, route.path, headers=headers, json=body)

    assert response.status_code == route.expected, f"{route} tropeçou num token inválido"


@pytest.mark.parametrize("route", PROTECTED_ROUTES, ids=str)
def test_protected_route_rejects_anonymous(client: ApiClient, route: ProtectedRoute) -> None:
    response = client.request(route.method, route.url(), json=route.body)

    assert response.status_code == 401, f"{route} respondeu {response.status_code} sem token"
    assert response.json()["error"]["code"] in {"invalid_token", "token_expired"}


@pytest.mark.parametrize("route", PROTECTED_ROUTES, ids=str)
def test_protected_route_rejects_a_garbage_token(client: ApiClient, route: ProtectedRoute) -> None:
    headers = {"Authorization": "Bearer nao-e-um-token"}

    response = client.request(route.method, route.url(), headers=headers, json=route.body)

    assert response.status_code == 401


@pytest.mark.parametrize("route", PROTECTED_ROUTES, ids=str)
def test_protected_route_accepts_the_owner(client: ApiClient, route: ProtectedRoute) -> None:
    user = register_user(client)
    path = route.owner_path(client, user)
    body = route.owner_body(client, user)

    response = client.request(route.method, path, headers=user.auth, json=body)

    assert response.status_code in {200, 201, 204}, (
        f"{route} recusou o próprio dono: {response.status_code} {response.text}"
    )


@pytest.mark.parametrize("route", PROTECTED_ROUTES, ids=str)
def test_protected_route_accepts_an_admin_too(client: ApiClient, route: ProtectedRoute) -> None:
    """Administrador é um autenticado com um papel a mais, não uma persona à parte.

    `require_role` fecha a rota de admin para o usuário comum; o inverso não
    existe, e uma rota comum que passasse a exigir `Role.USER` — em vez de só
    exigir autenticação — trancaria o administrador para fora da própria conta.
    """
    admin = register_admin(client)
    path = route.owner_path(client, admin)
    body = route.owner_body(client, admin)

    response = client.request(route.method, path, headers=admin.auth, json=body)

    assert response.status_code in {200, 201, 204}, (
        f"{route} recusou um administrador: {response.status_code} {response.text}"
    )


@pytest.mark.parametrize("route", ADMIN_ROUTES, ids=str)
def test_admin_route_rejects_anonymous(client: ApiClient, route: ProtectedRoute) -> None:
    response = client.request(route.method, route.url(), json=route.body)

    assert response.status_code == 401, f"{route} respondeu {response.status_code} sem token"


@pytest.mark.parametrize("route", ADMIN_ROUTES, ids=str)
def test_admin_route_rejects_a_common_user(client: ApiClient, route: ProtectedRoute) -> None:
    """O CRUD de usuários aberto a qualquer autenticado era o problema 5 do sistema antigo."""
    intruder = register_user(client)
    victim = register_user(client, email="bruno@exemplo.com", username="bruno")

    response = client.request(
        route.method, route.url(victim.id), headers=intruder.auth, json=route.body
    )

    assert response.status_code == 403, f"{route} aceitou um usuário comum"
    assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("route", ADMIN_ROUTES, ids=str)
def test_admin_route_accepts_an_admin(client: ApiClient, route: ProtectedRoute) -> None:
    admin = register_admin(client)
    target = register_user(client, email="bruno@exemplo.com", username="bruno")

    response = client.request(
        route.method, route.url(target.id), headers=admin.auth, json=route.body
    )

    assert response.status_code in {200, 201, 204}, f"{route} recusou um administrador"


def test_one_user_never_reaches_another(client: ApiClient) -> None:
    """`/users/me` resolve pelo token, não por um id vindo do cliente.

    Não existe rota que aceite o id de outro usuário — é assim que o escopo
    deixa de ser uma checagem que alguém pode esquecer.
    """
    ana = register_user(client)
    bruno = register_user(client, email="bruno@exemplo.com", username="bruno")

    ana_sees = client.get("/api/v1/users/me", headers=ana.auth)
    bruno_sees = client.get("/api/v1/users/me", headers=bruno.auth)

    assert ana_sees.json()["id"] == ana.id
    assert bruno_sees.json()["id"] == bruno.id
    assert ana.id != bruno.id


def test_every_route_is_declared_in_this_matrix(app: FastAPI) -> None:
    """Impede que uma rota nova entre sem que alguém decida quem a acessa.

    A enumeração vem do schema OpenAPI, não de `app.routes`: o FastAPI guarda
    routers incluídos como objetos opacos, e varrer `app.routes` devolveria uma
    lista vazia — um teste que passa sem verificar nada é pior que teste nenhum.
    """
    declared = {route.key for route in [*PUBLIC_ROUTES, *PROTECTED_ROUTES, *ADMIN_ROUTES]}

    registered: set[tuple[str, str]] = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if not path.startswith(UNVERSIONED_ROUTES_PREFIXES)
        for method in operations
    }

    assert registered, "nenhuma rota encontrada — a enumeração quebrou"

    missing = registered - declared
    assert not missing, (
        "rotas sem decisão de autorização registrada em PROTECTED_ROUTES ou "
        f"PUBLIC_ROUTES: {sorted(missing)}"
    )

    stale = declared - registered
    assert not stale, f"a matriz cita rotas que não existem mais: {sorted(stale)}"
