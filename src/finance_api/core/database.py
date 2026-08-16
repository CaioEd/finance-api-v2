"""Engine, sessão e checagem de disponibilidade do Postgres.

O engine é criado no ciclo de vida da aplicação e guardado em `app.state`,
nunca no import: módulo que abre recurso ao ser importado é impossível de
importar em teste e explode no lugar errado.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from finance_api.core.config import Settings


class Database:
    """Dono do engine e da fábrica de sessões."""

    def __init__(self, settings: Settings) -> None:
        self._engine: AsyncEngine = create_async_engine(
            settings.database_url,
            echo=settings.db_echo,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_seconds,
            pool_pre_ping=True,
        )
        self._sessionmaker = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            autoflush=False,
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        return self._sessionmaker

    async def ping(self) -> None:
        """Levanta se o banco não responder. Usado pelo readiness probe."""
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        await self._engine.dispose()


async def get_session(request: Request) -> AsyncGenerator[AsyncSession]:
    """Dependência de sessão. Sobrescrita nos testes para isolar por transação."""
    database: Database = request.app.state.database
    async with database.sessionmaker() as session:
        yield session
