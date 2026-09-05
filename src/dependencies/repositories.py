"""Fiação dos repositórios.

Ficam aqui, e não junto das classes, para que `repositories/` não precise
importar o FastAPI: repositório é SQL, e testá-lo não deveria exigir um
framework web.
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from dependencies.database import get_session
from repositories.admin_user_repository import AdminUserRepository
from repositories.balance_repository import BalanceRepository
from repositories.category_repository import CategoryRepository
from repositories.refresh_token_repository import RefreshTokenRepository
from repositories.transaction_repository import TransactionRepository
from repositories.user_repository import UserRepository


def get_user_repository(session: AsyncSession = Depends(get_session)) -> UserRepository:
    return UserRepository(session)


def get_category_repository(session: AsyncSession = Depends(get_session)) -> CategoryRepository:
    return CategoryRepository(session)


def get_balance_repository(session: AsyncSession = Depends(get_session)) -> BalanceRepository:
    """Só agrega. Nenhuma rota de saldo alcança lançamento a lançamento por aqui."""
    return BalanceRepository(session)


def get_transaction_repository(
    session: AsyncSession = Depends(get_session),
) -> TransactionRepository:
    return TransactionRepository(session)


def get_refresh_token_repository(
    session: AsyncSession = Depends(get_session),
) -> RefreshTokenRepository:
    return RefreshTokenRepository(session)


def get_admin_user_repository(
    session: AsyncSession = Depends(get_session),
) -> AdminUserRepository:
    """Repositório irrestrito. Só as rotas sob `require_role(ADMIN)` o pedem."""
    return AdminUserRepository(session)
