"""Matriz de autorização.

Os problemas 4 e 5 do sistema antigo (endpoints sem autenticação e CRUD de
usuários aberto a qualquer um) não vieram de uma decisão errada: vieram de
esquecimento em rota nova. Um teste que só cobre as rotas que alguém lembrou de
cobrir repetiria o erro.

Por isso são dois testes:

1. **A matriz** declara, rota a rota, o que cada persona deve receber.
2. **A checagem de completude** compara a matriz com as rotas realmente
   registradas na aplicação. Rota nova sem entrada aqui reprova o build, e a
   forma mais rápida de fazer o build passar é dizer quem pode acessá-la.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from tests.factories import RegisteredUser, register_user

ANONYMOUS = "anônimo"
OWNER = "dono"

# Rotas que existem justamente para quem ainda não tem token.
PUBLIC_ROUTES: set[tuple[str, str]] = {
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/logout"),
}

# Rotas operacionais e de documentação, fora do contrato de produto.
UNVERSIONED_ROUTES_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json")


PATH_PARAMETER = re.compile(r"\{[^}]+\}")
NONEXISTENT_ID = "00000000-0000-0000-0000-000000000000"

type Setup = Callable[[AsyncClient, RegisteredUser], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class ProtectedRoute:
    method: str
    path: str
    """O template, como o OpenAPI o registra — é ele que a completude compara."""

    body: dict[str, Any] | None = None
    setup: Setup | None = None
    """Rota com parâmetro no caminho: cria o recurso e devolve o caminho concreto."""

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)

    @property
    def unauthenticated_path(self) -> str:
        """Um id que não existe basta: a autenticação é conferida antes dele."""
        return PATH_PARAMETER.sub(NONEXISTENT_ID, self.path)

    async def owner_path(self, client: AsyncClient, user: RegisteredUser) -> str:
        if self.setup is None:
            return self.path
        return await self.setup(client, user)

    def __str__(self) -> str:
        return f"{self.method} {self.path}"


async def a_category_of(client: AsyncClient, user: RegisteredUser) -> str:
    response = await client.post(
        "/api/v1/categories", headers=user.auth, json={"name": "Padaria", "kind": "expense"}
    )
    response.raise_for_status()
    return f"/api/v1/categories/{response.json()['id']}"


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
]


@pytest.mark.parametrize("route", PROTECTED_ROUTES, ids=str)
async def test_protected_route_rejects_anonymous(
    client: AsyncClient, route: ProtectedRoute
) -> None:
    response = await client.request(route.method, route.unauthenticated_path, json=route.body)

    assert response.status_code == 401, f"{route} respondeu {response.status_code} sem token"
    assert response.json()["error"]["code"] in {"invalid_token", "token_expired"}


@pytest.mark.parametrize("route", PROTECTED_ROUTES, ids=str)
async def test_protected_route_rejects_a_garbage_token(
    client: AsyncClient, route: ProtectedRoute
) -> None:
    headers = {"Authorization": "Bearer nao-e-um-token"}

    response = await client.request(
        route.method, route.unauthenticated_path, headers=headers, json=route.body
    )

    assert response.status_code == 401


@pytest.mark.parametrize("route", PROTECTED_ROUTES, ids=str)
async def test_protected_route_accepts_the_owner(
    client: AsyncClient, route: ProtectedRoute
) -> None:
    user = await register_user(client)
    path = await route.owner_path(client, user)

    response = await client.request(route.method, path, headers=user.auth, json=route.body)

    assert response.status_code in {200, 201, 204}, (
        f"{route} recusou o próprio dono: {response.status_code} {response.text}"
    )


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
    declared = {route.key for route in PROTECTED_ROUTES} | PUBLIC_ROUTES

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
