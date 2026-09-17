"""Regra das recorrências: o CRUD da regra e a rodada do agendador.

Python puro, como os demais serviços: as dependências são `Protocol`, e a regra
se exercita sem Postgres (`tests/unit/test_recurring_transaction_service.py`).
O calendário — em que dia cai cada ocorrência — é de `services.recurrence`.

Quatro regras vivem aqui, e só aqui:

1. **A categoria precisa ser visível para o dono**, com a mesma recusa única de
   `services.transaction_service` para a inexistente e a de outra pessoa.
2. **Recorrência não lança o passado.** A data de início no passado vale como
   hoje, e reativar uma regra pausada recomeça da próxima data a partir de
   hoje — o que venceu durante a pausa não volta.
3. **Trocar o dia não pula nem repete mês.** A próxima ocorrência continua no
   mês em que estava, só que no dia novo. Se esse dia já passou, o mês ainda
   não tinha sido registrado e agora venceu: entra na hora.
4. **Toda escrita termina registrando o que venceu**, no mesmo commit. Depois
   de qualquer resposta desta API, uma regra ativa tem a próxima data no
   futuro; o agendador (`register_due`) faz o mesmo pelas que ninguém tocou.
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
    """O que este serviço precisa de um repositório de recorrências."""

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
    """A única coisa que recorrência exige de lançamentos: acrescentar os que venceram."""

    def add(self, transaction: Transaction) -> None: ...


@dataclass(frozen=True, slots=True)
class RecurringTransactionPage:
    items: Sequence[RecurringTransaction]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class DueRound:
    """O que uma rodada do agendador fez."""

    rules: int
    """Quantas regras vencidas a rodada travou. Menos que o lote: acabou o trabalho."""

    occurrences: int
    """Quantos lançamentos entraram — mais que `rules` quando alguém deve vários meses."""


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
            # Explícito, e não o default da coluna: os lançamentos que vencerem
            # já nesta requisição apontam para a regra antes do flush.
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

        # A categoria sai do laço pela mesma razão de `TransactionService.update`:
        # revalidar a visibilidade, e manter `category` — de onde vem `kind` —
        # apontando para a nova na resposta desta requisição.
        if "category_id" in changes:
            category = await self._visible_category_or_fail(user, changes.pop("category_id"))
            rule.category_id = category.id
            rule.category = category

        # O mês da próxima ocorrência é lido antes de aplicar a mudança: é o
        # primeiro mês ainda não registrado, e nenhuma edição volta para antes dele.
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
        """Exclui a regra; o que ela já registrou fica, sem o vínculo (`SET NULL`)."""
        rule = await self._owned_or_fail(user, rule_id)
        await self._recurrences.delete(rule)
        await self._commit()

    async def register_due(self, *, limit: int) -> DueRound:
        """Uma rodada do agendador: até `limit` regras vencidas, de qualquer dono.

        Trava as regras, acrescenta os lançamentos que elas devem, avança a
        próxima data e comita — tudo numa transação. É ela que impede o mesmo
        mês de entrar duas vezes, e não uma constraint: ver
        `repositories.recurring_transaction_repository.lock_due`.
        """
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
