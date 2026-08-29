"""Acesso a dados de categorias.

O escopo por usuário é imposto **aqui**, não no endpoint: toda consulta nasce
de `_visible_to`, então não existe caminho que devolva a categoria de outra
pessoa por esquecimento de um `if` na borda.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import CategoryNameTakenError, ConflictError
from models.category import Category, CategoryKind


def _visible_to(user_id: UUID) -> ColumnElement[bool]:
    """As categorias do sistema e as do próprio usuário — nada mais."""
    return or_(Category.user_id.is_(None), Category.user_id == user_id)


class CategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_visible(
        self, user_id: UUID, *, kind: CategoryKind | None = None
    ) -> Sequence[Category]:
        query = select(Category).where(_visible_to(user_id))
        if kind is not None:
            query = query.where(Category.kind == kind)
        # `lower` na ordenação pela mesma razão que no índice: caixa não é
        # critério de ordem para quem lê a lista.
        query = query.order_by(Category.kind, func.lower(Category.name))
        result = await self._session.scalars(query)
        return result.all()

    async def get_visible(self, category_id: UUID, user_id: UUID) -> Category | None:
        """Devolve `None` para categoria de terceiro — que a borda traduz em 404.

        Não é `session.get`: aquele traria a linha de qualquer dono, e o
        escopo passaria a depender de quem chamou lembrar de conferir.
        """
        result = await self._session.scalars(
            select(Category).where(Category.id == category_id, _visible_to(user_id))
        )
        return result.first()

    def add(self, category: Category) -> None:
        self._session.add(category)


def translate_integrity_error(exc: IntegrityError) -> ConflictError:
    """Traduz a violação de unicidade pelo nome do índice.

    Mesma decisão de `repositories.user_repository`: quem decide se o nome
    repete é o banco, porque um SELECT prévio tem janela de corrida com o
    INSERT. Os dois índices parciais respondem pelo mesmo conflito do ponto de
    vista de quem chamou — o nome já está em uso.
    """
    if "uq_categories_" in str(exc.orig):
        return CategoryNameTakenError()
    return ConflictError()
