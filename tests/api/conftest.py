"""Fixtures da suíte de API: um SQLite em memória por teste, e o `TestClient`.

Nenhum passo de preparação e nenhum serviço de fora — o banco nasce e morre com
o teste. É o que permite `pytest tests/api` numa máquina sem Docker, e é a
razão de esta suíte existir separada de `tests/integration/`.

O isolamento também é diferente: lá cada teste roda numa transação que sofre
rollback; aqui cada teste ganha um banco novo. Em memória, criar as quatro
tabelas custa menos que abrir a transação, e em troca o código sob teste comita
de verdade, sem savepoint nenhum entre ele e o banco.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from functools import partial
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from core.clock import Clock
from core.config import Environment, Settings
from dependencies.database import get_session
from main import create_app
from tests.api import endpoint_coverage
from tests.api.client import ApiClient
from tests.api.sqlite_backend import SqliteDatabase, create_schema, sqlite_engine

if TYPE_CHECKING:
    from _pytest.terminal import TerminalReporter

APP_TIMEZONE = ZoneInfo("America/Sao_Paulo")


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Configuração da suíte, explícita e independente do `.env` da máquina.

    `database_url` é um DSN de Postgres porque o validador de `Settings` recusa
    qualquer outro driver — e é bom que recuse, já que a aplicação de verdade
    não roda em SQLite. Ele nunca abre socket: a engine desta suíte é
    construída à parte e substitui `app.state.database` na subida.
    """
    return Settings(
        environment=Environment.TEST,
        app_timezone=str(APP_TIMEZONE),
        database_url="postgresql+asyncpg://nao-utilizado@localhost:5432/nao_utilizado",
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
def client(settings: Settings, clock: Clock) -> Iterator[ApiClient]:
    engine = sqlite_engine()
    database = SqliteDatabase(engine)
    application = create_app(settings, clock=clock)

    async def open_session() -> AsyncGenerator[AsyncSession]:
        async with database.sessionmaker() as session:
            yield session

    application.dependency_overrides[get_session] = open_session
    endpoint_coverage.register(application.openapi)

    with ApiClient(application, database) as http_client:
        # Entrar no `with` roda o lifespan, e com ele nasce um `Database`
        # apontando para o DSN das Settings — que nunca chegou a conectar.
        # Trocá-lo aqui é o que faz `/health/ready` medir o banco desta suíte,
        # em vez de um Postgres que não existe. O `dispose` do que fica no
        # lugar é do próprio lifespan, na saída do `with`.
        http_client.portal.call(application.state.database.dispose)
        application.state.database = database
        http_client.portal.call(partial(create_schema, engine))
        yield http_client


@pytest.fixture
def app(client: ApiClient) -> FastAPI:
    """A aplicação por trás do cliente, para quem precisa do schema OpenAPI.

    Deriva do `client` de propósito: uma segunda aplicação, montada à parte,
    poderia publicar um contrato diferente do que a suíte acabou de exercitar —
    e é exatamente a divergência entre os dois que as checagens de completude
    existem para pegar.
    """
    application = client.app
    assert isinstance(application, FastAPI)
    return application


def pytest_terminal_summary(terminalreporter: TerminalReporter) -> None:
    """Fecha a rodada dizendo quanto da API a suíte exercitou.

    Fica no conftest desta pasta, então só aparece quando a suíte de API entra
    na rodada — `make test-unit` continua terminando sem uma linha a mais.
    """
    coverage = endpoint_coverage.summary()
    if coverage is None:
        return

    terminalreporter.write_sep("-", "cobertura de endpoints da API")
    terminalreporter.write_line(
        coverage.headline, green=coverage.complete, yellow=not coverage.complete
    )
    for line in coverage.gaps:
        terminalreporter.write_line(line)
