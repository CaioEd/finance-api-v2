"""Fixtures que abrem conexão — e por isso moram **aqui**, não na raiz.

Um fixture `autouse` no conftest raiz vale para a árvore inteira de `tests/`.
Era assim que `migrated_schema` chegava em `tests/unit`, que não toca I/O em
uma linha sequer, e fazia a suíte unitária exigir um Postgres de pé só para
começar a coletar. Neste diretório o alcance é o correto: quem depende de banco
está aqui dentro.

O schema é montado **pelas migrations**, não por `Base.metadata.create_all()`:
migration quebrada precisa reprovar o build, e `create_all` esconderia isso
justamente onde ele seria descoberto.

Cada teste roda dentro de uma transação que sofre rollback no fim. Os
`commit()` dos serviços viram SAVEPOINT (`join_transaction_mode`), então o
código sob teste não sabe que está isolado e nenhum teste enxerga o dado do
outro — sem TRUNCATE e sem depender da ordem de execução.
"""

from __future__ import annotations

import os
import threading
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.clock import Clock
from core.config import Environment, Settings
from dependencies.database import get_session
from main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://finance:finance@localhost:5433/finance_test"
APP_TIMEZONE = ZoneInfo("America/Sao_Paulo")


def _test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
    database = urlsplit(url).path.lstrip("/")
    if not database.endswith("_test"):
        # A suíte derruba e recria o schema. Apontá-la para o banco de
        # desenvolvimento por um TEST_DATABASE_URL errado destruiria os dados.
        raise RuntimeError(f"o banco de teste precisa terminar em '_test' (recebido: {database!r})")
    return url


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Configuração da suíte, explícita e independente do .env da máquina."""
    return Settings(
        environment=Environment.TEST,
        app_timezone=str(APP_TIMEZONE),
        database_url=_test_database_url(),
        jwt_secret_key="chave-de-teste-com-comprimento-mais-que-suficiente",
        debug=True,
        docs_enabled=True,
        allowed_hosts=["*"],
        cors_origins=[],
        # argon2 no custo mínimo: a suíte hasheia dezenas de senhas, e o custo
        # de produção transformaria a suíte em minutos de espera.
        argon2_time_cost=1,
        argon2_memory_cost_kib=8,
        argon2_parallelism=1,
    )


@pytest.fixture(scope="session", autouse=True)
def migrated_schema(settings: Settings) -> None:
    """Recria o schema do zero e aplica as migrations, uma vez por sessão.

    `autouse` continua sendo o certo — só que agora o escopo é este diretório,
    onde todo teste realmente precisa do schema.

    Roda numa thread separada porque `alembic/env.py` chama `asyncio.run`, que
    não pode ser invocado de dentro de um event loop já em execução.
    """
    failures: list[BaseException] = []

    def run() -> None:
        try:
            config = Config(str(PROJECT_ROOT / "alembic.ini"))
            config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
            config.set_main_option("sqlalchemy.url", settings.database_url)
            command.downgrade(config, "base")
            command.upgrade(config, "head")
        except BaseException as exc:  # repassado para a thread principal
            failures.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    worker.join()
    if failures:
        raise failures[0]


@pytest.fixture
async def db_session(settings: Settings, migrated_schema: None) -> AsyncGenerator[AsyncSession]:
    """Depende do schema de propósito, mesmo com o `autouse` acima.

    A dependência explícita é o que mantém a ordem correta se alguém um dia
    mover este fixture para outro lugar.
    """
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            factory = async_sessionmaker(
                bind=connection,
                expire_on_commit=False,
                autoflush=False,
                join_transaction_mode="create_savepoint",
            )
            async with factory() as session:
                yield session
            await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
def clock() -> Clock:
    """Relógio real.

    Congelar o relógio da aplicação inteira aqui seria contraproducente: a
    validade do access token é verificada pelo PyJWT contra o relógio do
    sistema, e um `Clock` adiantado faria todo token nascer com `iat` no futuro
    — recusado como "not yet valid". Teste que precisa de instante fixo injeta
    o seu (`Clock(tz=..., instant=...)`), como fazem os testes de `core.clock`.
    """
    return Clock(tz=APP_TIMEZONE)


@pytest.fixture
async def app(
    settings: Settings, db_session: AsyncSession, clock: Clock
) -> AsyncGenerator[FastAPI]:
    application = create_app(settings, clock=clock)

    async def override_get_session() -> AsyncGenerator[AsyncSession]:
        yield db_session

    application.dependency_overrides[get_session] = override_get_session

    # ASGITransport não dispara o lifespan; sem isto, app.state.database não existe.
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


@pytest.fixture
def count_rows(db_session: AsyncSession) -> Callable[[str], Awaitable[int]]:
    """Conta linhas de uma tabela — usado para provar que um GET não escreve."""

    async def count(table: str) -> int:
        result = await db_session.execute(text(f"SELECT count(*) FROM {table}"))
        return int(result.scalar_one())

    return count
