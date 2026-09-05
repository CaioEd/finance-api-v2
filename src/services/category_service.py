"""Regra de categorias: listar, criar, editar e excluir.

Python puro, como os demais serviços: nada aqui conhece HTTP.

A decisão de quem pode o quê está inteira em `_mutable_or_fail`, e ela
distingue dois "não" que parecem o mesmo:

- categoria de **outro usuário** não é visível, então responde **404** — um 403
  confirmaria que ela existe;
- categoria **do sistema** é visível para todos, então esconder sua existência
  não protegeria nada: para quem não é admin, a resposta é **403**, com o
  motivo real.

Esta é a única autorização por papel que não está declarada no router (a regra
do projeto é `require_role` no router inteiro). É de propósito: a mesma rota
serve a categoria do sistema e a do usuário, e o que decide não é o endpoint —
é a linha. Uma dependência de rota teria de fechar a rota para o usuário comum,
que precisa dela para editar as próprias categorias.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import ForbiddenError, NotFoundError
from models.category import Category, CategoryKind
from models.user import User
from repositories.category_repository import CategoryRepository, translate_integrity_error
from schemas.category import CategoryCreateIn, CategoryUpdateIn


class CategoryService:
    def __init__(self, *, session: AsyncSession, categories: CategoryRepository) -> None:
        self._session = session
        self._categories = categories

    async def list_visible(
        self, user: User, *, kind: CategoryKind | None = None
    ) -> Sequence[Category]:
        """As do sistema e as do próprio usuário, na mesma lista."""
        return await self._categories.list_visible(user.id, kind=kind)

    async def get(self, user: User, category_id: UUID) -> Category:
        return await self._visible_or_fail(user, category_id)

    async def create(self, user: User, data: CategoryCreateIn) -> Category:
        category = Category(user_id=user.id, name=data.name, kind=data.kind)
        self._categories.add(category)
        await self._commit()
        return category

    async def update(self, user: User, category_id: UUID, data: CategoryUpdateIn) -> Category:
        category = await self._mutable_or_fail(user, category_id)
        for field, value in data.changes().items():
            setattr(category, field, value)
        await self._commit()
        return category

    async def delete(self, user: User, category_id: UUID) -> None:
        category = await self._mutable_or_fail(user, category_id)
        await self._session.delete(category)
        # `_commit()`, não `self._session.commit()`: excluir categoria que tem
        # lançamento viola a FK, e é `_commit` quem traduz isso em
        # `CategoryInUseError`. Sem ele o erro do driver sobe cru e vira 500.
        await self._commit()

    async def _visible_or_fail(self, user: User, category_id: UUID) -> Category:
        category = await self._categories.get_visible(category_id, user.id)
        if category is None:
            raise NotFoundError("Categoria não encontrada.")
        return category

    async def _mutable_or_fail(self, user: User, category_id: UUID) -> Category:
        """A categoria que este usuário pode alterar.

        As do sistema são do administrador; as suas são suas. A de outra
        pessoa nem chega até aqui — não é visível, e `_visible_or_fail` já
        respondeu 404 sem confirmar que ela existe. Nem para o admin: gerir a
        lista global não é enxergar a lista privada de ninguém (as rotas
        administrativas sobre dados de terceiros são a fase 6).
        """
        category = await self._visible_or_fail(user, category_id)
        if category.is_global and not user.is_admin:
            raise ForbiddenError("Só um administrador altera as categorias do sistema.")
        return category

    async def _commit(self) -> None:
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc
