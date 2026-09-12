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
from core.pdf import Brand
from core.security import PasswordHasher, TokenCodec
from dependencies.database import get_session
from dependencies.repositories import (
    get_admin_user_repository,
    get_balance_repository,
    get_category_repository,
    get_refresh_token_repository,
    get_transaction_repository,
    get_user_repository,
)
from dependencies.state import get_app_settings, get_clock, get_password_hasher, get_token_codec
from repositories.admin_user_repository import AdminUserRepository
from repositories.balance_repository import BalanceRepository
from repositories.category_repository import CategoryRepository
from repositories.refresh_token_repository import RefreshTokenRepository
from repositories.transaction_repository import TransactionRepository
from repositories.user_repository import UserRepository
from services.admin_user_service import AdminUserService
from services.auth_service import AuthService
from services.balance_service import BalanceService
from services.category_service import CategoryService
from services.report_service import ReportService
from services.transaction_service import TransactionService
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


def get_balance_service(
    balances: BalanceRepository = Depends(get_balance_repository),
    clock: Clock = Depends(get_clock),
) -> BalanceService:
    """Sem sessão: as três consultas de saldo são leitura, e não há o que commitar."""
    return BalanceService(balances=balances, clock=clock)


def get_transaction_service(
    session: AsyncSession = Depends(get_session),
    transactions: TransactionRepository = Depends(get_transaction_repository),
    categories: CategoryRepository = Depends(get_category_repository),
    clock: Clock = Depends(get_clock),
) -> TransactionService:
    """A sessão entra como `UnitOfWork`, e a categoria como `CategoryLookup`.

    O serviço só enxerga de cada uma o que declarou precisar; o repositório de
    categorias chega inteiro, mas o Protocol estreito impede que lançar vire um
    caminho para alterar categoria.
    """
    return TransactionService(
        unit_of_work=session,
        transactions=transactions,
        categories=categories,
        clock=clock,
    )


def get_report_service(
    transactions: TransactionService = Depends(get_transaction_service),
    balances: BalanceService = Depends(get_balance_service),
    categories: CategoryRepository = Depends(get_category_repository),
    clock: Clock = Depends(get_clock),
    settings: Settings = Depends(get_app_settings),
) -> ReportService:
    """Relatório depende dos **serviços**, não dos repositórios.

    É a fiação que garante a decisão do `report_service`: o PDF nasce da mesma
    resposta que a tela recebe, e não de uma segunda consulta que poderia
    discordar dela. O repositório de categorias entra só para escrever o nome da
    categoria filtrada no cabeçalho.

    A marca sai da configuração: `REPORT_BRAND_NAME` no topo da folha, com a
    imagem de `REPORT_LOGO_PATH` ao lado quando houver uma.
    """
    return ReportService(
        transactions=transactions,
        balances=balances,
        categories=categories,
        clock=clock,
        brand=Brand(name=settings.report_brand_name, logo_path=settings.report_logo_path),
    )


def get_user_service(
    session: AsyncSession = Depends(get_session),
    tokens: RefreshTokenRepository = Depends(get_refresh_token_repository),
    hasher: PasswordHasher = Depends(get_password_hasher),
    clock: Clock = Depends(get_clock),
) -> UserService:
    return UserService(session=session, tokens=tokens, hasher=hasher, clock=clock)


def get_admin_user_service(
    session: AsyncSession = Depends(get_session),
    users: AdminUserRepository = Depends(get_admin_user_repository),
    hasher: PasswordHasher = Depends(get_password_hasher),
) -> AdminUserService:
    """A sessão entra como `Transaction`: o serviço só usa commit e rollback."""
    return AdminUserService(transaction=session, users=users, hasher=hasher)
