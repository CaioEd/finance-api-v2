"""Acesso a dados de recorrências.

Como em `transaction_repository`: escopo por dono imposto aqui e categoria
carregada junto. A exceção é `lock_due`, do agendador, que atravessa todos os
donos de propósito e não é alcançada por nenhuma rota.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from models.category import Category, CategoryKind
from models.recurring_transaction import RecurringTransaction


def _conditions(user_id: UUID, kind: CategoryKind | None) -> list[ColumnElement[bool]]:
    """O mesmo `WHERE` na listagem e na contagem, senão o `total` mente."""
    conditions: list[ColumnElement[bool]] = [RecurringTransaction.user_id == user_id]
    if kind is not None:
        conditions.append(Category.kind == kind)
    return conditions


def _with_category() -> Select[tuple[RecurringTransaction]]:
    return (
        select(RecurringTransaction)
        .join(Category, RecurringTransaction.category_id == Category.id)
        .options(contains_eager(RecurringTransaction.category))
    )


class RecurringTransactionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_recurring(
        self, user_id: UUID, *, kind: CategoryKind | None, limit: int, offset: int
    ) -> Sequence[RecurringTransaction]:
        statement = (
            _with_category()
            .where(*_conditions(user_id, kind))
            # Pelo dia do mês; o id desempata para a paginação ser estável.
            .order_by(RecurringTransaction.day_of_month, RecurringTransaction.id)
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.scalars(statement)
        return result.all()

    async def count_recurring(self, user_id: UUID, *, kind: CategoryKind | None) -> int:
        statement = (
            select(func.count())
            .select_from(RecurringTransaction)
            .join(Category, RecurringTransaction.category_id == Category.id)
            .where(*_conditions(user_id, kind))
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def get_owned(
        self, rule_id: UUID, user_id: UUID, *, lock: bool = False
    ) -> RecurringTransaction | None:
        """`None` para recorrência de terceiro (a borda traduz em 404).

        `lock=True` trava a linha até o commit: a edição recalcula a próxima data,
        e o agendador não pode avançá-la no meio do caminho.
        """
        statement = _with_category().where(
            RecurringTransaction.id == rule_id, RecurringTransaction.user_id == user_id
        )
        if lock:
            statement = statement.with_for_update(of=RecurringTransaction)
        result = await self._session.scalars(statement)
        return result.first()

    async def lock_due(self, today: date, *, limit: int) -> Sequence[RecurringTransaction]:
        """Trava e devolve até `limit` recorrências ativas e vencidas.

        `SKIP LOCKED` deixa várias réplicas rodarem juntas: cada linha fica com
        quem a travou primeiro, até o commit que avança a data. `of=` evita
        travar a categoria do JOIN.
        """
        statement = (
            _with_category()
            .where(
                RecurringTransaction.is_active.is_(True),
                RecurringTransaction.next_occurrence_on <= today,
            )
            .order_by(RecurringTransaction.next_occurrence_on, RecurringTransaction.id)
            .limit(limit)
            .with_for_update(skip_locked=True, of=RecurringTransaction)
        )
        result = await self._session.scalars(statement)
        return result.all()

    def add(self, rule: RecurringTransaction) -> None:
        self._session.add(rule)

    async def delete(self, rule: RecurringTransaction) -> None:
        await self._session.delete(rule)
