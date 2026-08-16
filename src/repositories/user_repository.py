"""Acesso a dados de usuários.

Sem escopo de dono: usuário não é recurso de outro usuário, e todo chamador
passa um id que veio do token. A partir da fase 6 existe um
`AdminUserRepository` separado, irrestrito e explicitamente nomeado, usado só
sob `require_role(ADMIN)`.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import ConflictError, EmailTakenError, UsernameTakenError
from models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.scalars(select(User).where(User.email == email))
        return result.first()

    async def get_by_username(self, username: str) -> User | None:
        result = await self._session.scalars(select(User).where(User.username == username))
        return result.first()

    def add(self, user: User) -> None:
        self._session.add(user)


def translate_integrity_error(exc: IntegrityError) -> ConflictError:
    """Traduz a violação de UNIQUE pelo nome da constraint.

    Uma checagem prévia por SELECT teria uma janela de corrida entre a consulta
    e o INSERT; quem decide é o banco. O nome da constraint é estável porque a
    convenção de nomes é nossa (`core.database.NAMING_CONVENTION`).

    Mora aqui, e não nos serviços, porque este módulo é o que conhece as
    constraints da tabela `users` — e dois serviços precisam da tradução.
    """
    detail = str(exc.orig)
    if "uq_users_username" in detail:
        return UsernameTakenError()
    if "uq_users_email" in detail:
        return EmailTakenError()
    return ConflictError()
