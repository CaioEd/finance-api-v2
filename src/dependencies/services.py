"""Fiação dos serviços.

Mesma razão de `dependencies/repositories.py`: `services/` guarda regra de
negócio em Python puro, sem uma linha de FastAPI. A montagem das dependências
é assunto da borda HTTP e mora aqui.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.clock import Clock
from core.config import Settings
from core.security import PasswordHasher, TokenCodec
from dependencies.database import get_session
from dependencies.repositories import (
    get_category_repository,
    get_refresh_token_repository,
    get_user_repository,
)
from dependencies.state import get_app_settings, get_clock, get_password_hasher, get_token_codec
from repositories.category_repository import CategoryRepository
from repositories.refresh_token_repository import RefreshTokenRepository
from repositories.user_repository import UserRepository
from services.auth_service import AuthService
from services.category_service import CategoryService
from services.user_service import UserService


def get_auth_service(
    session: AsyncSession = Depends(get_session),
    users: UserRepository = Depends(get_user_repository),
    tokens: RefreshTokenRepository = Depends(get_refresh_token_repository),
    hasher: PasswordHasher = Depends(get_password_hasher),
    codec: TokenCodec = Depends(get_token_codec),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_app_settings),
) -> AuthService:
    return AuthService(
        session=session,
        users=users,
        tokens=tokens,
        hasher=hasher,
        codec=codec,
        clock=clock,
        refresh_ttl=timedelta(days=settings.refresh_token_ttl_days),
    )


def get_category_service(
    session: AsyncSession = Depends(get_session),
    categories: CategoryRepository = Depends(get_category_repository),
) -> CategoryService:
    return CategoryService(session=session, categories=categories)


def get_user_service(
    session: AsyncSession = Depends(get_session),
    tokens: RefreshTokenRepository = Depends(get_refresh_token_repository),
    hasher: PasswordHasher = Depends(get_password_hasher),
    clock: Clock = Depends(get_clock),
) -> UserService:
    return UserService(session=session, tokens=tokens, hasher=hasher, clock=clock)
