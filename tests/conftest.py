"""Fixtures compartilhadas.

A partir da fase 1, este arquivo também passa a montar o schema (via
`alembic upgrade head`, uma vez por sessão) e a isolar cada teste numa
transação com rollback. Na fase 0 ainda não existe schema: o único teste que
toca o banco é o readiness, que só precisa de um Postgres respondendo.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from finance_api.core.config import Environment, Settings
from finance_api.main import create_app

DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://finance:finance@localhost:5433/finance_test"


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Configuração da suíte, explícita e independente do .env da máquina."""
    return Settings(
        environment=Environment.TEST,
        app_timezone="America/Sao_Paulo",
        database_url=os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL),
        debug=True,
        docs_enabled=True,
        allowed_hosts=["*"],
        cors_origins=[],
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncGenerator[FastAPI]:
    application = create_app(settings)
    # ASGITransport não dispara o lifespan; sem isto, app.state.database não existe.
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client
