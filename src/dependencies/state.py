"""Objetos construídos uma vez na subida e guardados em `app.state`.

Sem I/O de requisição — configuração, relógio, hasher, codec de token, o
limitador de tentativas, quem resolve o IP do cliente e os clientes de cotação
(que abrem conexão, mas sobre uma sessão HTTP única do processo). Ficam no
estado da aplicação para que o teste possa trocá-los ao criar o app. O
limitador é o único com estado (os contadores), e por isso nasce com a
aplicação: cada `create_app` começa do zero.
"""

from __future__ import annotations

from fastapi import Request

from core.clock import Clock
from core.config import Settings
from core.rate_limit import ClientIpResolver, RateLimiter
from core.security import PasswordHasher, TokenCodec
from services.market_service import MarketService


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def get_password_hasher(request: Request) -> PasswordHasher:
    hasher: PasswordHasher = request.app.state.password_hasher
    return hasher


def get_token_codec(request: Request) -> TokenCodec:
    codec: TokenCodec = request.app.state.token_codec
    return codec


def get_rate_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


def get_client_ip(request: Request) -> str:
    """O IP de quem fez a requisição, lido como a borda de rede manda (`CLIENT_IP_HEADER`)."""
    resolver: ClientIpResolver = request.app.state.client_ip_resolver
    return resolver.resolve(request)


def get_market_service(request: Request) -> MarketService:
    """Os clientes de cotação do processo, montados na subida.

    Vêm do `app.state` como os demais objetos sem I/O de requisição: a sessão
    HTTP é uma só para o processo, e criar uma por requisição pagaria o
    handshake TLS em toda busca que o usuário digitasse.
    """
    service: MarketService = request.app.state.market
    return service
