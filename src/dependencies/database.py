"""Sessão de banco por requisição."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import Database


async def get_session(request: Request) -> AsyncGenerator[AsyncSession]:
    """Abre uma sessão por requisição e a fecha ao final.

    Sobrescrita nos testes (`dependency_overrides`) para que cada teste rode
    dentro de uma transação com rollback.
    """
    database: Database = request.app.state.database
    async with database.sessionmaker() as session:
        yield session
