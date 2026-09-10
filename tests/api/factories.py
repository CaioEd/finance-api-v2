"""Builders de dados de teste, na versão síncrona.

Irmão de `tests/factories.py`, que serve à suíte de integração. São dois porque
os clientes são dois — lá o `httpx.AsyncClient`, aqui o `TestClient` — e o que
muda entre eles é só o `await`. O que **não** muda (o formato do registro e o
`RegisteredUser` que ele devolve) vem de lá, importado: duas listas de campos
para o mesmo cadastro é como as duas suítes passam a testar contratos
diferentes sem ninguém perceber.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from models.user import Role, User
from tests.api.client import ApiClient
from tests.factories import RegisteredUser, registration_payload


def register_user(client: ApiClient, **overrides: Any) -> RegisteredUser:
    payload = registration_payload(**overrides)
    response = client.post("/api/v1/auth/register", json=payload)
    response.raise_for_status()
    body = response.json()
    return RegisteredUser(
        id=body["user"]["id"],
        email=body["user"]["email"],
        username=body["user"]["username"],
        password=payload["password"],
        access_token=body["access_token"],
        refresh_token=body["refresh_token"],
    )


def promote_to_admin(client: ApiClient, user: RegisteredUser) -> None:
    """Promove direto no banco.

    Não existe rota que promova alguém a administrador, e não deveria existir:
    o primeiro admin nasce do `python -m cli create-admin`. O token já emitido
    continua valendo porque a autorização lê o papel do banco a cada
    requisição, não do `role` gravado no access token.
    """

    async def promote(session: AsyncSession) -> None:
        record = await session.get(User, UUID(user.id))
        if record is None:  # pragma: no cover - só acontece se o registro falhar
            raise LookupError(f"usuário {user.id} não existe")
        record.role = Role.ADMIN

    client.in_the_database(promote)


def register_admin(
    client: ApiClient,
    *,
    email: str = "admin@exemplo.com",
    username: str = "admin",
    **overrides: Any,
) -> RegisteredUser:
    admin = register_user(client, email=email, username=username, **overrides)
    promote_to_admin(client, admin)
    return admin
