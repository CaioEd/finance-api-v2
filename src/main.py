"""Fábrica da aplicação.

Exporta `create_app` e nada mais: não existe `app` de módulo. Criar a aplicação
no import obrigaria a resolver configuração e abrir conexão só para importar o
módulo — o que quebra teste e mata a possibilidade de subir o app com
configuração diferente. O servidor usa o modo fábrica:

    uvicorn main:create_app --factory
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from api.router import api_router
from api.routes.health import router as health_router
from core.clock import Clock
from core.config import Settings, get_settings
from core.database import Database
from core.errors import register_exception_handlers
from core.security import PasswordHasher, TokenCodec
from version import __version__

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings: Settings = app.state.settings
    app.state.database = Database(settings)
    logger.info("aplicação iniciada em ambiente=%s", settings.environment)
    try:
        yield
    finally:
        await app.state.database.dispose()
        logger.info("aplicação encerrada")


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    """Monta a aplicação.

    `clock` é injetável para que o teste possa fixar "agora" sem mexer no
    relógio do processo. Em produção o default lê o relógio do sistema.
    """
    settings = settings or get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    # Objetos sem I/O, construídos uma vez: o custo do argon2 é por operação,
    # não por instância, e o codec é imutável.
    app.state.settings = settings
    app.state.clock = clock or Clock(tz=settings.tzinfo)
    app.state.password_hasher = PasswordHasher.from_settings(settings)
    app.state.token_codec = TokenCodec.from_settings(settings)

    register_exception_handlers(app)

    # Atenção: este middleware responde antes do roteamento, então a rejeição de
    # Host sai como texto puro ("Invalid host header", 400) e não no envelope de
    # erro — é o único ponto da borda HTTP fora do formato, e é de propósito:
    # reimplementar o matching de host do Starlette custaria mais do que vale.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(health_router)
    app.include_router(api_router)
    return app
