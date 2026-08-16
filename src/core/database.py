"""Base declarativa e conexão com o Postgres.

O engine é criado no ciclo de vida da aplicação e guardado em `app.state`,
nunca no import: módulo que abre recurso ao ser importado é impossível de
importar em teste e explode no lugar errado.

A dependência de sessão (`get_session`) mora em `dependencies/database.py` —
aqui não entra nada que conheça o FastAPI.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, MetaData, func, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from core.config import Settings

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
"""Nomes previsíveis para constraints e índices.

Sem isto o Postgres inventa os nomes e o autogenerate do Alembic acaba
produzindo `DROP CONSTRAINT` com um nome que não existe. Decidido na fase 0
porque renomear constraint depois que existem migrations é caro.
"""


class Base(DeclarativeBase):
    """Base de todos os models, um por arquivo em `models/`."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """`created_at` imutável e `updated_at` automático, ambos UTC.

    Estes são os instantes do *registro*. A data do fato (competência) é uma
    coluna `DATE` própria de cada tabela que precise dela — confundir as duas
    é o que impedia lançamento retroativo no sistema antigo.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


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
