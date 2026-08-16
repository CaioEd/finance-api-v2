"""Ambiente do Alembic, em modo async.

A URL vem das Settings — o mesmo lugar de onde a aplicação a lê. Duplicá-la no
alembic.ini é como o schema de produção e o de teste divergem.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from core.config import get_settings
from core.database import Base

# Importados só para registrar as tabelas no metadata do autogenerate.
# Model novo em `models/` precisa entrar nesta lista, ou o autogenerate não o vê.
from models import refresh_token as _refresh_token_model  # noqa: F401
from models import user as _user_model  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Quem chama o Alembic pela linha de comando não passa URL, e ela vem das
# Settings. Quem chama programaticamente (a suíte de testes) já a definiu — e
# sobrescrevê-la aqui apontaria as migrations do teste para o banco de
# desenvolvimento.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
