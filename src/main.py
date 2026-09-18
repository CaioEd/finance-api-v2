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
from datetime import timedelta

import httpx
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from api.router import api_router
from api.routes.health import router as health_router
from core.clock import Clock
from core.config import Settings, get_settings
from core.database import Database
from core.errors import register_exception_handlers
from core.rate_limit import ClientIpResolver, build_rate_limiter
from core.scheduler import PeriodicJob
from core.security import PasswordHasher, TokenCodec
from jobs.investment_quotes import MarketProviders, QuoteBudget, refresh_investment_values
from jobs.recurring_transactions import register_due_recurrences
from providers.base import DEFAULT_TIMEOUT_SECONDS
from providers.bcb import BcbClient
from providers.brapi import BrapiClient
from providers.twelve_data import TwelveDataClient
from services.market_service import MarketService
from version import __version__

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings: Settings = app.state.settings
    app.state.database = Database(settings)
    # Uma sessão HTTP para o processo inteiro: os provedores de cotação são
    # consultados a cada 15 minutos, e abrir conexão por consulta pagaria o
    # handshake TLS toda vez. Fecha no `finally`, depois dos trabalhos.
    app.state.http = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS)
    app.state.market = _market_service(app.state.http, settings)
    recurrences = _recurring_scheduler(app, settings)
    quotes = _quote_scheduler(app, settings)
    logger.info("aplicação iniciada em ambiente=%s", settings.environment)
    try:
        yield
    finally:
        # Antes do `dispose`, para uma rodada em curso não ficar sem conexão.
        for job in (recurrences, quotes):
            if job is not None:
                await job.stop()
        await app.state.http.aclose()
        await app.state.database.dispose()
        logger.info("aplicação encerrada")


def _recurring_scheduler(app: FastAPI, settings: Settings) -> PeriodicJob | None:
    """Banco e relógio são lidos do `app.state` a cada rodada, e não capturados aqui."""
    if not settings.recurring_scheduler_enabled:
        return None
    job = PeriodicJob(
        lambda: register_due_recurrences(app.state.database, app.state.clock),
        interval_seconds=settings.recurring_scheduler_interval_seconds,
        name="recorrências",
    )
    job.start()
    return job


def _providers(http: httpx.AsyncClient, settings: Settings) -> MarketProviders:
    """Os clientes que têm credencial. Sem token, o provedor não existe.

    Recusar a subida por falta de chave de cotação seria desproporcional: o resto
    da aplicação funciona sem ela, e é assim que a suíte roda.
    """
    return MarketProviders(
        brapi=BrapiClient(http=http, token=settings.brapi_token) if settings.brapi_token else None,
        twelve_data=(
            TwelveDataClient(http=http, api_key=settings.twelve_data_api_key)
            if settings.twelve_data_api_key
            else None
        ),
        # O Banco Central é público: não há credencial que possa faltar.
        bcb=BcbClient(http=http),
    )


def _market_service(http: httpx.AsyncClient, settings: Settings) -> MarketService:
    providers = _providers(http, settings)
    return MarketService(brapi=providers.brapi, twelve_data=providers.twelve_data)


def _quote_scheduler(app: FastAPI, settings: Settings) -> PeriodicJob | None:
    """Agendador de cotações. Lê banco e relógio do `app.state` a cada rodada."""
    if not settings.investment_scheduler_enabled:
        return None
    budget = QuoteBudget(
        brapi_symbols=settings.brapi_max_symbols_per_run,
        twelve_data_symbols=settings.twelve_data_max_symbols_per_run,
        accrual_batch=settings.investment_accrual_batch_size,
        rate_max_age=timedelta(hours=settings.investment_rate_max_age_hours),
    )
    job = PeriodicJob(
        lambda: refresh_investment_values(
            app.state.database, app.state.clock, _providers(app.state.http, settings), budget
        ),
        interval_seconds=settings.investment_scheduler_interval_seconds,
        name="cotações",
    )
    job.start()
    return job


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
    # O limitador guarda os contadores de tentativa: um por aplicação, e não de
    # módulo, então cada `create_app` — cada teste — começa do zero.
    app.state.rate_limiter = build_rate_limiter(settings)
    app.state.client_ip_resolver = ClientIpResolver.from_settings(settings)
    _warn_if_the_client_ip_is_the_proxy(settings)

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
            # Sem isto o front noutra origem não lê o nome do arquivo dos
            # relatórios, nem quanto esperar depois de um 429 no login.
            expose_headers=["Content-Disposition", "Retry-After"],
        )

    app.include_router(health_router)
    app.include_router(api_router)
    return app


def _warn_if_the_client_ip_is_the_proxy(settings: Settings) -> None:
    """Avisa quando o limite por IP de produção provavelmente conta o proxy.

    Em produção quase sempre há proxy na frente (Railway, ALB), e sem
    `CLIENT_IP_HEADER` todo cliente chega com o IP dele: o limite por IP passa a
    valer para todos juntos, e poucas senhas erradas de quem quer que seja
    barram o login de todo mundo. Não impede a subida — pode não haver proxy
    mesmo —, mas deixa o aviso no log do deploy.
    """
    if settings.is_production and settings.rate_limit_enabled and not settings.client_ip_header:
        logger.warning(
            "CLIENT_IP_HEADER vazio em produção: atrás de proxy, todos os clientes dividem "
            "o mesmo limite de login por IP. Ver docs/rate-limit.md"
        )
