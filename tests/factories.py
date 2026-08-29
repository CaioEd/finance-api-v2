"""Builders explícitos de dados de teste.

Sem fábrica mágica: cada teste pede o que precisa e o resto vem de um default
óbvio, legível na própria assinatura.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import Role, User

DEFAULT_PASSWORD = "senha-bem-comprida"


@dataclass(frozen=True, slots=True)
class RegisteredUser:
    """O que um registro devolve, pronto para autenticar as próximas chamadas."""

    id: str
    email: str
    username: str
    password: str
    access_token: str
    refresh_token: str

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


def registration_payload(
    *,
    email: str = "ana@exemplo.com",
    username: str = "ana",
    password: str = DEFAULT_PASSWORD,
    first_name: str = "Ana",
    last_name: str = "Ribeiro",
) -> dict[str, Any]:
    return {
        "email": email,
        "username": username,
        "password": password,
        "first_name": first_name,
        "last_name": last_name,
    }


async def register_user(client: AsyncClient, **overrides: Any) -> RegisteredUser:
    payload = registration_payload(**overrides)
    response = await client.post("/api/v1/auth/register", json=payload)
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


async def promote_to_admin(session: AsyncSession, user: RegisteredUser) -> None:
    """Promove direto no banco.

    Não existe rota que promova alguém a administrador, e não deveria existir:
    o primeiro admin nasce do `python -m cli create-admin`. O token já emitido
    continua valendo porque a autorização lê o papel do banco a cada
    requisição, não do `role` gravado no access token.
    """
    record = await session.get(User, UUID(user.id))
    if record is None:  # pragma: no cover - só acontece se o registro falhar
        raise LookupError(f"usuário {user.id} não existe")
    record.role = Role.ADMIN
    await session.commit()


async def register_admin(
    client: AsyncClient,
    session: AsyncSession,
    *,
    email: str = "admin@exemplo.com",
    username: str = "admin",
    **overrides: Any,
) -> RegisteredUser:
    admin = await register_user(client, email=email, username=username, **overrides)
    await promote_to_admin(session, admin)
    return admin
