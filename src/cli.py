"""Comandos operacionais.

    python -m cli create-admin --email admin@exemplo.com --username admin
    python -m cli seed-dev

`create-admin` é o caminho para qualquer ambiente: o primeiro administrador
nasce por aqui, nunca por registro público — um endpoint que cria admin é um
endpoint que alguém vai chamar.

`seed-dev` é conveniência de desenvolvimento e **recusa rodar fora de
local/test** (ver `refuse_outside_dev`).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from getpass import getpass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import Environment, Settings, get_settings
from core.security import PasswordHasher
from models.user import Role, User
from repositories.user_repository import UserRepository
from schemas.user import PASSWORD_MIN_LENGTH

DEV_ENVIRONMENTS = frozenset({Environment.LOCAL, Environment.TEST})

# Nada de `@dev.local`: `.local` é TLD de uso especial (RFC 6761) e o validador
# de e-mail o recusa — a conta seria criada e o login responderia 422.
DEFAULT_DEV_EMAIL = "admin@exemplo.com"
DEFAULT_DEV_USERNAME = "admin"
# Credencial de desenvolvimento, fraca de propósito e travada por ambiente.
DEFAULT_DEV_PASSWORD = "123456"


def refuse_outside_dev(environment: Environment) -> None:
    """Barra o seed em qualquer ambiente que não seja de desenvolvimento.

    A conta semeada tem senha fraca e conhecida. Em dev isso é conveniência;
    em staging ou produção seria uma porta aberta com a chave na fechadura.
    A trava é aqui, e não no shell script que chama, porque quem chama muda.
    """
    if environment not in DEV_ENVIRONMENTS:
        raise SystemExit(
            f"seed-dev recusado em ENVIRONMENT={environment}: a conta semeada tem senha fraca "
            f"e conhecida. Use `create-admin` para criar administrador de verdade."
        )


@asynccontextmanager
async def open_session(settings: Settings) -> AsyncGenerator[AsyncSession]:
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def upsert_dev_admin(
    session: AsyncSession,
    hasher: PasswordHasher,
    *,
    email: str,
    username: str,
    password: str,
) -> bool:
    """Cria a conta de desenvolvimento, ou repõe o estado dela se já existir.

    Idempotente de propósito: o comando roda a cada subida do container, e o
    dev precisa poder contar com a senha documentada mesmo que alguém a tenha
    trocado ou desativado a conta na sessão anterior.

    Devolve `True` se criou, `False` se repôs.

    Escreve pelo model, sem passar pelos schemas: a senha de exemplo tem menos
    caracteres que o mínimo exigido no registro pela API. Isso é aceitável num
    dado de desenvolvimento e é justamente por isso que a função é barrada fora
    de local/test.
    """
    email = email.strip().lower()
    username = username.strip().lower()

    users = UserRepository(session)
    existing = await users.get_by_email(email) or await users.get_by_username(username)

    if existing is not None:
        existing.email = email
        existing.username = username
        existing.password_hash = hasher.hash(password)
        existing.role = Role.ADMIN
        existing.is_active = True
        await session.commit()
        return False

    users.add(
        User(
            email=email,
            username=username,
            password_hash=hasher.hash(password),
            first_name="Dev",
            last_name="Admin",
            role=Role.ADMIN,
        )
    )
    await session.commit()
    return True


async def seed_dev() -> int:
    settings = get_settings()
    refuse_outside_dev(settings.environment)

    email = os.environ.get("DEV_ADMIN_EMAIL", DEFAULT_DEV_EMAIL)
    username = os.environ.get("DEV_ADMIN_USERNAME", DEFAULT_DEV_USERNAME)
    password = os.environ.get("DEV_ADMIN_PASSWORD", DEFAULT_DEV_PASSWORD)

    async with open_session(settings) as session:
        created = await upsert_dev_admin(
            session,
            PasswordHasher.from_settings(settings),
            email=email,
            username=username,
            password=password,
        )

    verb = "criada" if created else "reposta"
    print(f"conta de desenvolvimento {verb}: {email} / {password}")  # noqa: T201
    return 0


async def create_admin(email: str, username: str, password: str) -> int:
    settings = get_settings()
    hasher = PasswordHasher.from_settings(settings)

    async with open_session(settings) as session:
        session.add(
            User(
                email=email.strip().lower(),
                username=username.strip().lower(),
                password_hash=hasher.hash(password),
                role=Role.ADMIN,
            )
        )
        try:
            await session.commit()
        except IntegrityError:
            print(f"já existe uma conta com este e-mail ou username: {email}", file=sys.stderr)  # noqa: T201
            return 1

    print(f"admin criado: {email}")  # noqa: T201
    return 0


def _read_password(provided: str | None) -> str:
    password = provided or getpass("senha do admin: ")
    if len(password) < PASSWORD_MIN_LENGTH:
        raise SystemExit(f"a senha precisa ter ao menos {PASSWORD_MIN_LENGTH} caracteres")
    return password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cli", description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    admin = subcommands.add_parser("create-admin", help="cria um usuário com papel admin")
    admin.add_argument("--email", required=True)
    admin.add_argument("--username", required=True)
    admin.add_argument(
        "--password",
        help="omita para digitar sem eco (preferível: não fica no histórico do shell)",
    )

    subcommands.add_parser(
        "seed-dev",
        help="cria/repõe a conta de desenvolvimento (só em ENVIRONMENT=local|test)",
    )

    args = parser.parse_args(argv)
    if args.command == "create-admin":
        return asyncio.run(create_admin(args.email, args.username, _read_password(args.password)))
    if args.command == "seed-dev":
        return asyncio.run(seed_dev())
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
