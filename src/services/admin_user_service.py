"""Regra do CRUD administrativo de usuários.

Python puro: nada aqui conhece HTTP nem FastAPI. As dependências são declaradas
como `Protocol` — o serviço diz de que operações precisa, e tanto o repositório
real quanto um duble de teste as satisfazem. É o que permite testar a regra sem
Postgres (`tests/unit/test_admin_user_service.py`).

Uma trava vive aqui e não no router: **administrador não age sobre a própria
conta por estas rotas**. Como quem chega até aqui já é admin e não consegue se
rebaixar nem se excluir, sempre resta pelo menos um administrador ativo — a
garantia sai de graça, sem contar linhas na tabela a cada requisição.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from core.errors import SelfTargetError, UserNotFoundError
from core.security import PasswordHasher
from models.user import User
from repositories.admin_user_repository import UserFilters
from repositories.user_repository import translate_integrity_error
from schemas.user import AdminUserCreateIn, AdminUserUpdateIn


class AdminUserStore(Protocol):
    """O que este serviço precisa de um repositório irrestrito de usuários."""

    async def get(self, user_id: UUID) -> User | None: ...

    async def list_users(
        self, *, filters: UserFilters, limit: int, offset: int
    ) -> Sequence[User]: ...

    async def count_users(self, *, filters: UserFilters) -> int: ...

    def add(self, user: User) -> None: ...

    async def delete(self, user: User) -> None: ...


class Transaction(Protocol):
    """A parte da sessão que é assunto do serviço: quando o trabalho fecha.

    `AsyncSession` satisfaz este Protocol estruturalmente; o teste unitário
    entrega uma transação de mentira e não abre conexão nenhuma.
    """

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


@dataclass(frozen=True, slots=True)
class UserPage:
    items: Sequence[User]
    total: int
    limit: int
    offset: int


class AdminUserService:
    def __init__(
        self,
        *,
        transaction: Transaction,
        users: AdminUserStore,
        hasher: PasswordHasher,
    ) -> None:
        self._transaction = transaction
        self._users = users
        self._hasher = hasher

    async def list_users(self, *, filters: UserFilters, limit: int, offset: int) -> UserPage:
        items = await self._users.list_users(filters=filters, limit=limit, offset=offset)
        total = await self._users.count_users(filters=filters)
        return UserPage(items=items, total=total, limit=limit, offset=offset)

    async def create_user(self, data: AdminUserCreateIn) -> User:
        user = User(
            email=data.email,
            username=data.username,
            password_hash=self._hasher.hash(data.password),
            first_name=data.first_name,
            last_name=data.last_name,
            role=data.role,
            is_active=data.is_active,
        )
        self._users.add(user)
        await self._commit()
        return user

    async def update_user(self, user_id: UUID, data: AdminUserUpdateIn, *, actor: User) -> User:
        user = await self._target(user_id, actor)

        changes = data.changes()
        for field, value in changes.items():
            setattr(user, field, value)

        await self._commit()
        return user

    async def delete_user(self, user_id: UUID, *, actor: User) -> None:
        user = await self._target(user_id, actor)
        await self._users.delete(user)
        await self._commit()

    async def _target(self, user_id: UUID, actor: User) -> User:
        """Resolve o alvo da ação, recusando o próprio administrador.

        A checagem vem antes da consulta de propósito: a regra não depende do
        que existe no banco, e assim ela não pode ser contornada por corrida.
        """
        if user_id == actor.id:
            raise SelfTargetError()

        user = await self._users.get(user_id)
        if user is None:
            raise UserNotFoundError()
        return user

    async def _commit(self) -> None:
        """Fecha o trabalho, traduzindo colisão de UNIQUE em conflito de domínio.

        O rollback não é opcional: uma sessão que voltasse suja para o pool
        levaria o erro para a próxima requisição, que não fez nada de errado.
        """
        try:
            await self._transaction.commit()
        except IntegrityError as exc:
            await self._transaction.rollback()
            raise translate_integrity_error(exc) from exc
