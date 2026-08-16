"""Builders explícitos de dados de teste.

Sem fábrica mágica: cada teste pede o que precisa e o resto vem de um default
óbvio, legível na própria assinatura.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from httpx import AsyncClient

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
