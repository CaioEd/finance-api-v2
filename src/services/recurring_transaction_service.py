"""Regra das recorrências: CRUD da regra e rodada do agendador.

- Recorrência não lança o passado: início retroativo e retomada de pausa contam de hoje.
- Trocar o dia mantém o mês pendente; se o dia novo já passou, o mês entra na hora.
- Toda escrita comita junto o que já venceu: uma regra ativa sai sempre com a
  próxima data no futuro.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from core.clock import Clock
from core.errors import InvalidCategoryError, RecurringTransactionNotFoundError
from models.category import Category, CategoryKind
from models.recurring_transaction import RecurringTransaction
from models.transaction import Transaction
from models.user import User
from repositories.transaction_repository import translate_integrity_error
from schemas.recurring_transaction import RecurringTransactionCreateIn, RecurringTransactionUpdateIn
from services import recurrence
from services.transaction_service import CategoryLookup, UnitOfWork


class RecurringTransactionStore(Protocol):
    async def list_recurring(
        self, user_id: UUID, *, kind: CategoryKind | None, limit: int, offset: int
    ) -> Sequence[RecurringTransaction]: ...

    async def count_recurring(self, user_id: UUID, *, kind: CategoryKind | None) -> int: ...

    async def get_owned(
        self, rule_id: UUID, user_id: UUID, *, lock: bool = False
    ) -> RecurringTransaction | None: ...

    async def lock_due(self, today: date, *, limit: int) -> Sequence[RecurringTransaction]: ...

    def add(self, rule: RecurringTransaction) -> None: ...

    async def delete(self, rule: RecurringTransaction) -> None: ...


class TransactionSink(Protocol):
    def add(self, transaction: Transaction) -> None: ...


@dataclass(frozen=True, slots=True)
class RecurringTransactionPage:
    items: Sequence[RecurringTransaction]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class DueRound:
    rules: int
    """Regras travadas; menos que o lote significa que acabou."""

    occurrences: int


class RecurringTransactionService:
    def __init__(
        self,
        *,
        unit_of_work: UnitOfWork,
        recurrences: RecurringTransactionStore,
        transactions: TransactionSink,
        categories: CategoryLookup,
        clock: Clock,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._recurrences = recurrences
        self._transactions = transactions
        self._categories = categories
        self._clock = clock

    async def list_recurring(
        self, user: User, *, kind: CategoryKind | None, limit: int, offset: int
    ) -> RecurringTransactionPage:
        items = await self._recurrences.list_recurring(
            user.id, kind=kind, limit=limit, offset=offset
        )
        total = await self._recurrences.count_recurring(user.id, kind=kind)
        return RecurringTransactionPage(items=items, total=total, limit=limit, offset=offset)

    async def get(self, user: User, rule_id: UUID) -> RecurringTransaction:
        return await self._owned_or_fail(user, rule_id)

    async def create(self, user: User, data: RecurringTransactionCreateIn) -> RecurringTransaction:
        category = await self._visible_category_or_fail(user, data.category_id)
        today = self._clock.today()
        start = max(data.starts_on or today, today)
        rule = RecurringTransaction(
            # Id no Python: os lançamentos vencidos apontam para a regra antes do flush.
            id=uuid4(),
            user_id=user.id,
            category_id=category.id,
            category=category,
            amount=data.amount,
            description=data.description,
            day_of_month=data.day_of_month,
            next_occurrence_on=recurrence.first_occurrence_from(start, data.day_of_month),
            is_active=True,
        )
        self._recurrences.add(rule)
        self._register_due(rule, today)
        await self._commit()
        return rule

    async def update(
        self, user: User, rule_id: UUID, data: RecurringTransactionUpdateIn
    ) -> RecurringTransaction:
        rule = await self._owned_or_fail(user, rule_id, lock=True)
        changes = data.changes()

        # Fora do laço, como em `TransactionService.update`: revalida e troca `category`.
        if "category_id" in changes:
            category = await self._visible_category_or_fail(user, changes.pop("category_id"))
            rule.category_id = category.id
            rule.category = category

        # Lido antes das mudanças: é o primeiro mês ainda não registrado.
        pending_month = recurrence.month_of(rule.next_occurrence_on)
        reactivating = changes.get("is_active") is True and not rule.is_active

        for field, value in changes.items():
            setattr(rule, field, value)

        today = self._clock.today()
        if reactivating:
            start = max(today, pending_month.first_day)
            rule.next_occurrence_on = recurrence.first_occurrence_from(start, rule.day_of_month)
        elif "day_of_month" in changes:
            rule.next_occurrence_on = recurrence.occurrence_in(pending_month, rule.day_of_month)

        self._register_due(rule, today)
        await self._commit()
        return rule

    async def delete(self, user: User, rule_id: UUID) -> None:
        """O que a regra já registrou fica, sem o vínculo (`SET NULL`)."""
        rule = await self._owned_or_fail(user, rule_id)
        await self._recurrences.delete(rule)
        await self._commit()

    async def register_due(self, *, limit: int) -> DueRound:
        """Um lote do agendador, de qualquer dono: trava, lança, avança a data e comita junto."""
        today = self._clock.today()
        rules = await self._recurrences.lock_due(today, limit=limit)
        occurrences = sum(self._register_due(rule, today) for rule in rules)
        await self._commit()
        return DueRound(rules=len(rules), occurrences=occurrences)

    def _register_due(self, rule: RecurringTransaction, today: date) -> int:
        due = recurrence.register_due(rule, today)
        for transaction in due:
            self._transactions.add(transaction)
        return len(due)

    async def _owned_or_fail(
        self, user: User, rule_id: UUID, *, lock: bool = False
    ) -> RecurringTransaction:
        rule = await self._recurrences.get_owned(rule_id, user.id, lock=lock)
        if rule is None:
            raise RecurringTransactionNotFoundError()
        return rule

    async def _visible_category_or_fail(self, user: User, category_id: UUID) -> Category:
        category = await self._categories.get_visible(category_id, user.id)
        if category is None:
            raise InvalidCategoryError()
        return category

    async def _commit(self) -> None:
        try:
            await self._unit_of_work.commit()
        except IntegrityError as exc:
            await self._unit_of_work.rollback()
            raise translate_integrity_error(exc) from exc
