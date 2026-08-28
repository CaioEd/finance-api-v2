"""Matriz de autorização.

Os problemas 4 e 5 do sistema antigo (endpoints sem autenticação e CRUD de
usuários aberto a qualquer um) não vieram de uma decisão errada: vieram de
esquecimento em rota nova. Um teste que só cobre as rotas que alguém lembrou de
cobrir repetiria o erro.

Por isso são duas coisas:

1. **A matriz** declara, rota a rota, o que cada persona deve receber. São três
   personas: anônimo, autenticado comum e administrador — e as rotas se dividem
   em `PUBLIC_ROUTES`, `PROTECTED_ROUTES` (basta autenticar) e `ADMIN_ROUTES`
   (exigem o papel).
2. **A checagem de completude** compara a matriz com as rotas realmente
   registradas na aplicação. Rota nova sem entrada aqui reprova o build, e a
   forma mais rápida de fazer o build passar é dizer quem pode acessá-la.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import register_admin, register_user

ANONYMOUS = "anônimo"
OWNER = "dono"

# Rotas que existem justamente para quem ainda não tem token.
PUBLIC_ROUTES: set[tuple[str, str]] = {
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/logout"),
}

# Um id sintático válido que não pertence a ninguém: serve para montar a URL
# de quem nem token tem, onde a autorização decide antes de o alvo importar.
NOBODY = "00000000-0000-0000-0000-000000000000"

# Rotas operacionais e de documentação, fora do contrato de produto.
UNVERSIONED_ROUTES_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json")


@dataclass(frozen=True, slots=True)
class ProtectedRoute:
    method: str
    path: str
    body: dict[str, Any] | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)

    def url(self, user_id: str = NOBODY) -> str:
        """O `path` guarda o molde do OpenAPI; a chamada precisa de um id real.

        Sem um id, `/users/{user_id}` viraria `/users/`, e o FastAPI responderia
        307 para a rota de listagem — o teste passaria a medir o redirecionamento
        em vez da autorização.
        """
        return self.path.format(user_id=user_id)

    def __str__(self) -> str:
        return f"{self.method} {self.path}"


PROTECTED_ROUTES = [
    ProtectedRoute("GET", "/api/v1/users/me"),
    ProtectedRoute("PATCH", "/api/v1/users/me", body={"first_name": "X"}),
    ProtectedRoute(
        "POST",
        "/api/v1/users/me/password",
        body={"current_password": "senha-bem-comprida", "new_password": "outra-senha-longa"},
    ),
    ProtectedRoute("DELETE", "/api/v1/users/me", body={"password": "senha-bem-comprida"}),
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


@pytest.mark.parametrize("route", PROTECTED_ROUTES, ids=str)
async def test_protected_route_rejects_anonymous(
    client: AsyncClient, route: ProtectedRoute
) -> None:
    response = await client.request(route.method, route.url(), json=route.body)

    assert response.status_code == 401, f"{route} respondeu {response.status_code} sem token"
    assert response.json()["error"]["code"] in {"invalid_token", "token_expired"}


@pytest.mark.parametrize("route", PROTECTED_ROUTES, ids=str)
async def test_protected_route_rejects_a_garbage_token(
    client: AsyncClient, route: ProtectedRoute
) -> None:
    headers = {"Authorization": "Bearer nao-e-um-token"}

    response = await client.request(route.method, route.url(), headers=headers, json=route.body)

    assert response.status_code == 401


@pytest.mark.parametrize("route", PROTECTED_ROUTES, ids=str)
async def test_protected_route_accepts_the_owner(
    client: AsyncClient, route: ProtectedRoute
) -> None:
    user = await register_user(client)

    response = await client.request(route.method, route.url(), headers=user.auth, json=route.body)

    assert response.status_code in {200, 204}, f"{route} recusou o próprio dono"


@pytest.mark.parametrize("route", ADMIN_ROUTES, ids=str)
async def test_admin_route_rejects_anonymous(client: AsyncClient, route: ProtectedRoute) -> None:
    response = await client.request(route.method, route.url(), json=route.body)

    assert response.status_code == 401, f"{route} respondeu {response.status_code} sem token"


@pytest.mark.parametrize("route", ADMIN_ROUTES, ids=str)
async def test_admin_route_rejects_a_common_user(
    client: AsyncClient, route: ProtectedRoute
) -> None:
    """O CRUD de usuários aberto a qualquer autenticado era o problema 5 do sistema antigo."""
    intruder = await register_user(client)
    victim = await register_user(client, email="bruno@exemplo.com", username="bruno")

    response = await client.request(
        route.method, route.url(victim.id), headers=intruder.auth, json=route.body
    )

    assert response.status_code == 403, f"{route} aceitou um usuário comum"
    assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("route", ADMIN_ROUTES, ids=str)
async def test_admin_route_accepts_an_admin(
    client: AsyncClient, db_session: AsyncSession, route: ProtectedRoute
) -> None:
    admin = await register_admin(client, db_session)
    target = await register_user(client, email="bruno@exemplo.com", username="bruno")

    response = await client.request(
        route.method, route.url(target.id), headers=admin.auth, json=route.body
    )

    assert response.status_code in {200, 201, 204}, f"{route} recusou um administrador"


async def test_one_user_never_reaches_another(client: AsyncClient) -> None:
    """`/users/me` resolve pelo token, não por um id vindo do cliente.

    Não existe rota que aceite o id de outro usuário — é assim que o escopo
    deixa de ser uma checagem que alguém pode esquecer.
    """
    ana = await register_user(client)
    bruno = await register_user(client, email="bruno@exemplo.com", username="bruno")

    ana_sees = await client.get("/api/v1/users/me", headers=ana.auth)
    bruno_sees = await client.get("/api/v1/users/me", headers=bruno.auth)

    assert ana_sees.json()["id"] == ana.id
    assert bruno_sees.json()["id"] == bruno.id
    assert ana.id != bruno.id


async def test_every_route_is_declared_in_this_matrix(app: FastAPI) -> None:
    """Impede que uma rota nova entre sem que alguém decida quem a acessa.

    A enumeração vem do schema OpenAPI, não de `app.routes`: o FastAPI guarda
    routers incluídos como objetos opacos, e varrer `app.routes` devolveria uma
    lista vazia — um teste que passa sem verificar nada é pior que teste nenhum.
    """
    declared = {route.key for route in [*PROTECTED_ROUTES, *ADMIN_ROUTES]} | PUBLIC_ROUTES

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
