"""Contrato público do domínio de usuários.

Schemas de saída nunca expõem o model: `password_hash` não tem como vazar
por esquecimento porque não existe campo para ele aqui.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field, StringConstraints

from models.user import Role

PASSWORD_MIN_LENGTH = 10


def _normalize(value: object) -> object:
    """Tira espaço nas pontas e caixa alta.

    Normalizar na entrada é o que faz a constraint UNIQUE valer alguma coisa:
    sem isto, ` Caio@x.com ` e `caio@x.com` convivem como contas diferentes.
    """
    return value.strip().lower() if isinstance(value, str) else value


type Email = Annotated[EmailStr, BeforeValidator(_normalize)]
type Username = Annotated[
    str,
    BeforeValidator(_normalize),
    StringConstraints(min_length=3, max_length=150, pattern=r"^[a-z0-9._-]+$"),
]
type Password = Annotated[str, Field(min_length=PASSWORD_MIN_LENGTH, max_length=128)]
type PersonName = Annotated[str, StringConstraints(max_length=100)]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    username: str
    first_name: str
    last_name: str
    role: Role
    is_active: bool
    created_at: datetime


class UserUpdateIn(BaseModel):
    """Todos os campos opcionais: é PATCH."""

    model_config = ConfigDict(extra="forbid")

    email: Email | None = None
    username: Username | None = None
    first_name: PersonName | None = None
    last_name: PersonName | None = None


class PasswordChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str
    new_password: Password


class AccountDeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str
