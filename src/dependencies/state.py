"""Objetos construídos uma vez na subida e guardados em `app.state`.

São imutáveis e sem I/O — configuração, relógio, hasher e codec de token. Ficam
no estado da aplicação para que o teste possa trocá-los ao criar o app.
"""

from __future__ import annotations

from fastapi import Request

from core.clock import Clock
from core.config import Settings
from core.security import PasswordHasher, TokenCodec


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
