"""Acesso a dados de usuários **sem escopo de dono**.

Existe separado do `UserRepository` — e com "admin" no nome — porque é o único
lugar do projeto onde uma query alcança a linha de qualquer pessoa. Um
repositório irrestrito misturado com o comum é como o CRUD aberto do sistema
antigo nasce de novo: alguém reusa o método achando que ele filtra por dono.

Quem garante que só administrador chega aqui é `require_role(Role.ADMIN)` no
router inteiro (`api/routes/admin_users.py`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import Role, User

LIKE_ESCAPE = "\\"


@dataclass(frozen=True, slots=True)
class UserFilters:
    """O recorte pedido na listagem. Ausente (`None`) é "não filtre por isto"."""

    role: Role | None = None
    is_active: bool | None = None
    search: str | None = None


def _escaped(term: str) -> str:
    """Neutraliza os curingas do LIKE vindos do cliente.

    Sem isto, buscar por `a_b` casaria com `axb`, e um `%` sozinho listaria a
    base inteira — o filtro passaria a ser controlado por quem digita.
    """
    for char in (LIKE_ESCAPE, "%", "_"):
        term = term.replace(char, LIKE_ESCAPE + char)
    return term


class AdminUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def list_users(self, *, filters: UserFilters, limit: int, offset: int) -> Sequence[User]:
        statement = (
            select(User)
            .where(*_conditions(filters))
            # O desempate por id mantém a paginação estável: sem ele, duas
            # linhas criadas no mesmo instante podem trocar de página entre
            # duas requisições e uma delas nunca ser vista.
            .order_by(User.created_at.desc(), User.id)
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.scalars(statement)
        return result.all()

    async def count_users(self, *, filters: UserFilters) -> int:
        statement = select(func.count()).select_from(User).where(*_conditions(filters))
        return int((await self._session.execute(statement)).scalar_one())

    def add(self, user: User) -> None:
        self._session.add(user)

    async def delete(self, user: User) -> None:
        # Os refresh tokens caem por ON DELETE CASCADE, junto com tudo que for
        # do usuário nas fases seguintes.
        await self._session.delete(user)


def _conditions(filters: UserFilters) -> list[ColumnElement[bool]]:
    """Uma função só para os dois lados da paginação.

    A contagem precisa exatamente do mesmo `WHERE` da listagem; duplicar a
    construção é como o `total` acaba mentindo sobre o número de páginas.
    """
    conditions: list[ColumnElement[bool]] = []
    if filters.role is not None:
        conditions.append(User.role == filters.role)
    if filters.is_active is not None:
        conditions.append(User.is_active == filters.is_active)
    if filters.search:
        pattern = f"%{_escaped(filters.search)}%"
        conditions.append(
            or_(
                User.email.ilike(pattern, escape=LIKE_ESCAPE),
                User.username.ilike(pattern, escape=LIKE_ESCAPE),
                User.first_name.ilike(pattern, escape=LIKE_ESCAPE),
                User.last_name.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    return conditions
