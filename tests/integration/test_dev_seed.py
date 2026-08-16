"""Conta de desenvolvimento semeada a cada subida do container.

Dois comportamentos importam aqui: a trava de ambiente (a conta tem senha
fraca e conhecida) e a idempotência (o comando roda toda vez, não só na
primeira).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cli import (
    DEFAULT_DEV_EMAIL,
    DEFAULT_DEV_PASSWORD,
    DEFAULT_DEV_USERNAME,
    refuse_outside_dev,
    upsert_dev_admin,
)
from core.config import Environment, Settings
from core.security import PasswordHasher
from models.user import Role
from repositories.user_repository import UserRepository


@pytest.fixture
def hasher(settings: Settings) -> PasswordHasher:
    return PasswordHasher.from_settings(settings)


# ------------------------------------------------------------ trava de ambiente


@pytest.mark.parametrize("environment", [Environment.LOCAL, Environment.TEST])
def test_seed_is_allowed_in_development(environment: Environment) -> None:
    refuse_outside_dev(environment)  # não levanta


@pytest.mark.parametrize("environment", [Environment.STAGING, Environment.PRODUCTION])
def test_seed_refuses_outside_development(environment: Environment) -> None:
    """Senha fraca e conhecida fora de dev seria porta aberta com a chave na fechadura."""
    with pytest.raises(SystemExit, match="seed-dev recusado"):
        refuse_outside_dev(environment)


# ------------------------------------------------------------ semeadura


async def test_seed_creates_an_active_admin(
    db_session: AsyncSession, hasher: PasswordHasher
) -> None:
    created = await upsert_dev_admin(
        db_session,
        hasher,
        email=DEFAULT_DEV_EMAIL,
        username=DEFAULT_DEV_USERNAME,
        password=DEFAULT_DEV_PASSWORD,
    )

    user = await UserRepository(db_session).get_by_email(DEFAULT_DEV_EMAIL)

    assert created is True
    assert user is not None
    assert user.role is Role.ADMIN
    assert user.is_active is True
    assert hasher.verify(user.password_hash, DEFAULT_DEV_PASSWORD)


async def test_seed_runs_twice_without_duplicating(
    db_session: AsyncSession, hasher: PasswordHasher
) -> None:
    """Roda a cada subida do container: a segunda vez não pode explodir."""
    first = await upsert_dev_admin(
        db_session,
        hasher,
        email=DEFAULT_DEV_EMAIL,
        username=DEFAULT_DEV_USERNAME,
        password=DEFAULT_DEV_PASSWORD,
    )
    second = await upsert_dev_admin(
        db_session,
        hasher,
        email=DEFAULT_DEV_EMAIL,
        username=DEFAULT_DEV_USERNAME,
        password=DEFAULT_DEV_PASSWORD,
    )

    total = await db_session.execute(
        text("SELECT count(*) FROM users WHERE email = :email"), {"email": DEFAULT_DEV_EMAIL}
    )

    assert (first, second) == (True, False)
    assert total.scalar_one() == 1


async def test_seed_restores_password_role_and_activation(
    db_session: AsyncSession, hasher: PasswordHasher
) -> None:
    """O dev precisa poder contar com a senha documentada na subida seguinte."""
    await upsert_dev_admin(
        db_session,
        hasher,
        email=DEFAULT_DEV_EMAIL,
        username=DEFAULT_DEV_USERNAME,
        password=DEFAULT_DEV_PASSWORD,
    )
    users = UserRepository(db_session)
    user = await users.get_by_email(DEFAULT_DEV_EMAIL)
    assert user is not None
    user.password_hash = hasher.hash("outra-senha-qualquer")
    user.role = Role.USER
    user.is_active = False
    await db_session.commit()

    await upsert_dev_admin(
        db_session,
        hasher,
        email=DEFAULT_DEV_EMAIL,
        username=DEFAULT_DEV_USERNAME,
        password=DEFAULT_DEV_PASSWORD,
    )

    restored = await users.get_by_email(DEFAULT_DEV_EMAIL)
    assert restored is not None
    assert hasher.verify(restored.password_hash, DEFAULT_DEV_PASSWORD)
    assert restored.role is Role.ADMIN
    assert restored.is_active is True


async def test_seeded_account_can_log_in(
    client: AsyncClient, db_session: AsyncSession, hasher: PasswordHasher
) -> None:
    """A senha tem menos caracteres que o mínimo do registro; o login aceita mesmo assim.

    É o que torna a conta útil — e a razão de o seed gravar pelo model, sem
    passar pelos schemas de entrada.
    """
    await upsert_dev_admin(
        db_session,
        hasher,
        email=DEFAULT_DEV_EMAIL,
        username=DEFAULT_DEV_USERNAME,
        password=DEFAULT_DEV_PASSWORD,
    )

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": DEFAULT_DEV_EMAIL, "password": DEFAULT_DEV_PASSWORD},
    )

    assert response.status_code == 200
    assert response.json()["access_token"]
