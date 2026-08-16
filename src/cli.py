"""Comandos operacionais.

    python -m cli create-admin --email admin@exemplo.com --username admin

O primeiro administrador nasce por aqui, nunca por registro público: um
endpoint que cria admin é um endpoint que alguém vai chamar.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from getpass import getpass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.config import get_settings
from core.security import PasswordHasher
from models.user import Role, User
from schemas.user import PASSWORD_MIN_LENGTH


async def create_admin(email: str, username: str, password: str) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    hasher = PasswordHasher.from_settings(settings)

    try:
        async with session_factory() as session:
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
    finally:
        await engine.dispose()

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

    args = parser.parse_args(argv)
    if args.command == "create-admin":
        return asyncio.run(create_admin(args.email, args.username, _read_password(args.password)))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
