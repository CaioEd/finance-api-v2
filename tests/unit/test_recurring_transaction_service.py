"""Regra das recorrências com dubles em memória, sem banco.

A trava de `lock_due` entre conexões é testada contra Postgres, em
`tests/integration/test_recurring_transactions.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.exc import IntegrityError

from core.clock import Clock
from core.errors import InvalidCategoryError, RecurringTransactionNotFoundError
from models.category import Category, CategoryKind
from models.recurring_transaction import FK_RECURRING_CATEGORY, RecurringTransaction
from models.transaction import Transaction
from models.user import Role, User
from schemas.recurring_transaction import RecurringTransactionCreateIn, RecurringTransactionUpdateIn
from services.recurring_transaction_service import (
    RecurringTransactionService,
    RecurringTransactionStore,
    TransactionSink,
)
from services.transaction_service import CategoryLookup, UnitOfWork

SAO_PAULO = ZoneInfo("America/Sao_Paulo")

TODAY = date(2026, 9, 17)


def make_user() -> User:
    return User(
        id=uuid4(),
        email="ana@exemplo.com",
        username="ana",
        password_hash="$argon2id$hash-de-mentira",
        first_name="Ana",
        last_name="Ribeiro",
        role=Role.USER,
        is_active=True,
    )


def make_category(
    *, user_id: UUID | None, name: str = "Streaming", kind: CategoryKind = CategoryKind.EXPENSE
) -> Category:
    return Category(id=uuid4(), user_id=user_id, name=name, kind=kind)


def make_rule(
    *,
    user_id: UUID,
    category: Category,
    day_of_month: int = 20,
    next_occurrence_on: date = date(2026, 9, 20),
    is_active: bool = True,
) -> RecurringTransaction:
    return RecurringTransaction(
        id=uuid4(),
        user_id=user_id,
        category_id=category.id,
        category=category,
        amount=Decimal("39.90"),
        description="Netflix",
        day_of_month=day_of_month,
        next_occurrence_on=next_occurrence_on,
        is_active=is_active,
    )


def clock_on(day: date) -> Clock:
    """Meio-dia em São Paulo daquele dia — longe da virada, para o teste falar só de datas."""
    moment = datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC)
    return Clock(tz=SAO_PAULO, instant=lambda: moment)


class FakeRecurringStore:
    """Implementa `RecurringTransactionStore` em memória, com o escopo do repositório."""

    def __init__(self, rules: Sequence[RecurringTransaction] = ()) -> None:
        self.rules = list(rules)
        self.added: list[RecurringTransaction] = []
        self.deleted: list[RecurringTransaction] = []
        self.locked_reads = 0
        self.due_limit: int | None = None

    async def list_recurring(
        self, user_id: UUID, *, kind: CategoryKind | None, limit: int, offset: int
    ) -> Sequence[RecurringTransaction]:
        return self._owned(user_id, kind)[offset : offset + limit]

    async def count_recurring(self, user_id: UUID, *, kind: CategoryKind | None) -> int:
        return len(self._owned(user_id, kind))

    async def get_owned(
        self, rule_id: UUID, user_id: UUID, *, lock: bool = False
    ) -> RecurringTransaction | None:
        if lock:
            self.locked_reads += 1
        return next((r for r in self.rules if r.id == rule_id and r.user_id == user_id), None)

    async def lock_due(self, today: date, *, limit: int) -> Sequence[RecurringTransaction]:
        self.due_limit = limit
        due = [r for r in self.rules if r.is_active and r.next_occurrence_on <= today]
        return due[:limit]

    def add(self, rule: RecurringTransaction) -> None:
        self.added.append(rule)
        self.rules.append(rule)

    async def delete(self, rule: RecurringTransaction) -> None:
        self.deleted.append(rule)
        self.rules.remove(rule)

    def _owned(self, user_id: UUID, kind: CategoryKind | None) -> list[RecurringTransaction]:
        return [r for r in self.rules if r.user_id == user_id and (kind is None or r.kind is kind)]


class FakeTransactionSink:
    def __init__(self) -> None:
        self.added: list[Transaction] = []

    def add(self, transaction: Transaction) -> None:
        self.added.append(transaction)


class FakeCategoryLookup:
    def __init__(self, categories: Sequence[Category] = ()) -> None:
        self.categories = list(categories)

    async def get_visible(self, category_id: UUID, user_id: UUID) -> Category | None:
        return next(
            (
                c
                for c in self.categories
                if c.id == category_id and (c.user_id is None or c.user_id == user_id)
            ),
            None,
        )


class FakeUnitOfWork:
    def __init__(self, *, fails_with: Exception | None = None) -> None:
        self._fails_with = fails_with
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1
        if self._fails_with is not None:
            raise self._fails_with

    async def rollback(self) -> None:
        self.rollbacks += 1


class Setup:
    """O serviço montado com os dubles à mão, para o teste inspecionar cada um."""

    def __init__(
        self,
        *,
        rules: Sequence[RecurringTransaction] = (),
        categories: Sequence[Category] = (),
        today: date = TODAY,
        unit_of_work: FakeUnitOfWork | None = None,
    ) -> None:
        self.store = FakeRecurringStore(rules)
        self.sink = FakeTransactionSink()
        self.work = unit_of_work or FakeUnitOfWork()
        # As anotações forçam a checagem de que os dubles satisfazem os Protocol.
        recurrences: RecurringTransactionStore = self.store
        transactions: TransactionSink = self.sink
        lookup: CategoryLookup = FakeCategoryLookup(categories)
        work: UnitOfWork = self.work
        self.service = RecurringTransactionService(
            unit_of_work=work,
            recurrences=recurrences,
            transactions=transactions,
            categories=lookup,
            clock=clock_on(today),
        )


@pytest.fixture
def user() -> User:
    return make_user()


def create_in(category: Category, **overrides: object) -> RecurringTransactionCreateIn:
    payload: dict[str, object] = {
        "amount": "39.90",
        "category_id": category.id,
        "description": "Netflix",
        "day_of_month": 20,
    }
    return RecurringTransactionCreateIn.model_validate(payload | overrides)


# ------------------------------------------------------------------- CREATE


async def test_create_starts_on_the_next_occurrence_from_today(user: User) -> None:
    category = make_category(user_id=user.id)
    setup = Setup(categories=[category])

    rule = await setup.service.create(user, create_in(category, day_of_month=20))

    assert rule.next_occurrence_on == date(2026, 9, 20)
    assert rule.is_active
    assert rule.user_id == user.id
    assert setup.store.added == [rule]
    assert setup.sink.added == [], "nada venceu ainda"
    assert setup.work.commits == 1


async def test_create_with_a_day_that_already_passed_starts_next_month(user: User) -> None:
    category = make_category(user_id=user.id)
    setup = Setup(categories=[category])

    rule = await setup.service.create(user, create_in(category, day_of_month=5))

    assert rule.next_occurrence_on == date(2026, 10, 5)


async def test_create_honors_a_start_in_the_future(user: User) -> None:
    category = make_category(user_id=user.id)
    setup = Setup(categories=[category])

    rule = await setup.service.create(
        user, create_in(category, day_of_month=31, starts_on=date(2027, 2, 1))
    )

    assert rule.next_occurrence_on == date(2027, 2, 28)


async def test_a_start_in_the_past_is_treated_as_today(user: User) -> None:
    """Sem essa trava, `starts_on` em 2001 criaria trezentos lançamentos numa requisição."""
    category = make_category(user_id=user.id)
    setup = Setup(categories=[category])

    rule = await setup.service.create(
        user, create_in(category, day_of_month=20, starts_on=date(2001, 1, 1))
    )

    assert rule.next_occurrence_on == date(2026, 9, 20)
    assert setup.sink.added == []


async def test_a_rule_due_today_registers_it_in_the_same_commit(user: User) -> None:
    category = make_category(user_id=user.id)
    setup = Setup(categories=[category])

    rule = await setup.service.create(user, create_in(category, day_of_month=17))

    [transaction] = setup.sink.added
    assert transaction.occurred_on == TODAY
    assert transaction.recurring_transaction_id == rule.id
    assert rule.next_occurrence_on == date(2026, 10, 17)
    assert setup.work.commits == 1


async def test_create_accepts_a_global_category_and_takes_its_kind(user: User) -> None:
    salario = make_category(user_id=None, name="Salário", kind=CategoryKind.INCOME)
    setup = Setup(categories=[salario])

    rule = await setup.service.create(user, create_in(salario))

    assert rule.kind is CategoryKind.INCOME


async def test_create_refuses_a_category_of_another_user(user: User) -> None:
    alheia = make_category(user_id=uuid4())
    setup = Setup(categories=[alheia])

    with pytest.raises(InvalidCategoryError):
        await setup.service.create(user, create_in(alheia))

    assert setup.store.added == []
    assert setup.work.commits == 0


async def test_create_rolls_back_when_the_category_vanishes_mid_flight(user: User) -> None:
    category = make_category(user_id=user.id)
    orig = Exception(f'violates foreign key constraint "{FK_RECURRING_CATEGORY}"')
    work = FakeUnitOfWork(fails_with=IntegrityError("INSERT ...", {}, orig))
    setup = Setup(categories=[category], unit_of_work=work)

    with pytest.raises(InvalidCategoryError):
        await setup.service.create(user, create_in(category))

    assert work.rollbacks == 1


# ---------------------------------------------------------------- GET, LIST


async def test_a_rule_of_another_user_is_not_found(user: User) -> None:
    alheia = make_category(user_id=uuid4())
    de_outro = make_rule(user_id=uuid4(), category=alheia)
    setup = Setup(rules=[de_outro], categories=[alheia])

    with pytest.raises(RecurringTransactionNotFoundError):
        await setup.service.get(user, de_outro.id)
    with pytest.raises(RecurringTransactionNotFoundError):
        await setup.service.update(user, de_outro.id, RecurringTransactionUpdateIn())
    with pytest.raises(RecurringTransactionNotFoundError):
        await setup.service.delete(user, de_outro.id)

    assert setup.store.deleted == []


async def test_list_returns_the_page_and_the_total_of_the_owner(user: User) -> None:
    minha = make_category(user_id=user.id)
    alheia = make_category(user_id=uuid4())
    rules = [make_rule(user_id=user.id, category=minha) for _ in range(3)]
    rules.append(make_rule(user_id=uuid4(), category=alheia))
    setup = Setup(rules=rules)

    page = await setup.service.list_recurring(user, kind=None, limit=2, offset=0)

    assert len(page.items) == 2
    assert page.total == 3
    assert all(item.user_id == user.id for item in page.items)


# ------------------------------------------------------------------- UPDATE


async def test_update_changes_only_what_was_sent_and_locks_the_row(user: User) -> None:
    category = make_category(user_id=user.id)
    rule = make_rule(user_id=user.id, category=category)
    setup = Setup(rules=[rule], categories=[category])

    updated = await setup.service.update(
        user, rule.id, RecurringTransactionUpdateIn(amount=Decimal("44.90"))
    )

    assert updated.amount == Decimal("44.90")
    assert updated.description == "Netflix"
    assert updated.next_occurrence_on == date(2026, 9, 20), "valor novo não mexe na data"
    assert setup.store.locked_reads == 1
    assert setup.work.commits == 1


async def test_changing_the_day_keeps_the_pending_month(user: User) -> None:
    """Setembro ainda não registrado, dia 20 → 25: continua setembro, agora no 25."""
    category = make_category(user_id=user.id)
    rule = make_rule(user_id=user.id, category=category, next_occurrence_on=date(2026, 9, 20))
    setup = Setup(rules=[rule], categories=[category])

    updated = await setup.service.update(
        user, rule.id, RecurringTransactionUpdateIn(day_of_month=25)
    )

    assert updated.next_occurrence_on == date(2026, 9, 25)
    assert setup.sink.added == []


async def test_moving_the_day_to_one_that_passed_registers_the_pending_month_now(
    user: User,
) -> None:
    """Setembro pendente (dia 20), e a cobrança mudou para o dia 10: setembro venceu, entra já."""
    category = make_category(user_id=user.id)
    rule = make_rule(user_id=user.id, category=category, next_occurrence_on=date(2026, 9, 20))
    setup = Setup(rules=[rule], categories=[category])

    updated = await setup.service.update(
        user, rule.id, RecurringTransactionUpdateIn(day_of_month=10)
    )

    assert [t.occurred_on for t in setup.sink.added] == [date(2026, 9, 10)]
    assert updated.next_occurrence_on == date(2026, 10, 10)


async def test_changing_the_day_never_repeats_a_month_already_registered(user: User) -> None:
    """Setembro já registrado (próxima em outubro): trocar para o dia 10 não refaz setembro."""
    category = make_category(user_id=user.id)
    rule = make_rule(user_id=user.id, category=category, next_occurrence_on=date(2026, 10, 20))
    setup = Setup(rules=[rule], categories=[category])

    updated = await setup.service.update(
        user, rule.id, RecurringTransactionUpdateIn(day_of_month=10)
    )

    assert updated.next_occurrence_on == date(2026, 10, 10)
    assert setup.sink.added == []


async def test_pausing_stops_the_rule_without_moving_the_date(user: User) -> None:
    category = make_category(user_id=user.id)
    rule = make_rule(user_id=user.id, category=category)
    setup = Setup(rules=[rule], categories=[category])

    updated = await setup.service.update(
        user, rule.id, RecurringTransactionUpdateIn(is_active=False)
    )

    assert not updated.is_active
    assert updated.next_occurrence_on == date(2026, 9, 20)


async def test_resuming_does_not_charge_the_months_of_the_pause(user: User) -> None:
    """Pausada desde maio, retomada em 17/9 com "dia 5": a próxima é 5/10, sem maio a setembro."""
    category = make_category(user_id=user.id)
    rule = make_rule(
        user_id=user.id,
        category=category,
        day_of_month=5,
        next_occurrence_on=date(2026, 5, 5),
        is_active=False,
    )
    setup = Setup(rules=[rule], categories=[category])

    updated = await setup.service.update(
        user, rule.id, RecurringTransactionUpdateIn(is_active=True)
    )

    assert updated.is_active
    assert updated.next_occurrence_on == date(2026, 10, 5)
    assert setup.sink.added == []


async def test_resuming_on_the_day_of_the_occurrence_registers_it(user: User) -> None:
    category = make_category(user_id=user.id)
    rule = make_rule(
        user_id=user.id,
        category=category,
        day_of_month=17,
        next_occurrence_on=date(2026, 6, 17),
        is_active=False,
    )
    setup = Setup(rules=[rule], categories=[category])

    await setup.service.update(user, rule.id, RecurringTransactionUpdateIn(is_active=True))

    assert [t.occurred_on for t in setup.sink.added] == [TODAY]
    assert rule.next_occurrence_on == date(2026, 10, 17)


async def test_resuming_never_goes_back_before_the_pending_month(user: User) -> None:
    """Setembro registrado, pausada e retomada no mesmo mês: a próxima continua em outubro."""
    category = make_category(user_id=user.id)
    rule = make_rule(
        user_id=user.id,
        category=category,
        day_of_month=25,
        next_occurrence_on=date(2026, 10, 25),
        is_active=False,
    )
    setup = Setup(rules=[rule], categories=[category])

    updated = await setup.service.update(
        user, rule.id, RecurringTransactionUpdateIn(is_active=True)
    )

    assert updated.next_occurrence_on == date(2026, 10, 25)


async def test_resuming_an_active_rule_changes_nothing(user: User) -> None:
    category = make_category(user_id=user.id)
    rule = make_rule(user_id=user.id, category=category, next_occurrence_on=date(2026, 9, 20))
    setup = Setup(rules=[rule], categories=[category])

    updated = await setup.service.update(
        user, rule.id, RecurringTransactionUpdateIn(is_active=True)
    )

    assert updated.next_occurrence_on == date(2026, 9, 20)


async def test_changing_the_category_changes_the_kind(user: User) -> None:
    despesa = make_category(user_id=user.id)
    receita = make_category(user_id=None, name="Salário", kind=CategoryKind.INCOME)
    rule = make_rule(user_id=user.id, category=despesa)
    setup = Setup(rules=[rule], categories=[despesa, receita])

    updated = await setup.service.update(
        user, rule.id, RecurringTransactionUpdateIn(category_id=receita.id)
    )

    assert updated.category is receita
    assert updated.kind is CategoryKind.INCOME


async def test_update_refuses_a_category_of_another_user(user: User) -> None:
    minha = make_category(user_id=user.id)
    alheia = make_category(user_id=uuid4())
    rule = make_rule(user_id=user.id, category=minha)
    setup = Setup(rules=[rule], categories=[minha, alheia])

    with pytest.raises(InvalidCategoryError):
        await setup.service.update(
            user, rule.id, RecurringTransactionUpdateIn(category_id=alheia.id)
        )

    assert rule.category_id == minha.id
    assert setup.work.commits == 0


# ------------------------------------------------------------------- DELETE


async def test_delete_removes_the_own_rule(user: User) -> None:
    category = make_category(user_id=user.id)
    rule = make_rule(user_id=user.id, category=category)
    setup = Setup(rules=[rule], categories=[category])

    await setup.service.delete(user, rule.id)

    assert setup.store.deleted == [rule]
    assert setup.work.commits == 1


# ------------------------------------------------------------ o agendador


async def test_a_round_registers_every_due_rule_of_every_owner() -> None:
    ana, bruno = uuid4(), uuid4()
    streaming = make_category(user_id=ana)
    aluguel = make_category(user_id=bruno, name="Aluguel")
    vencida_ha_dois_meses = make_rule(
        user_id=ana, category=streaming, day_of_month=5, next_occurrence_on=date(2026, 8, 5)
    )
    vence_hoje = make_rule(
        user_id=bruno, category=aluguel, day_of_month=17, next_occurrence_on=TODAY
    )
    futura = make_rule(user_id=ana, category=streaming, next_occurrence_on=date(2026, 9, 20))
    pausada = make_rule(
        user_id=ana, category=streaming, next_occurrence_on=date(2026, 1, 20), is_active=False
    )
    setup = Setup(rules=[vencida_ha_dois_meses, vence_hoje, futura, pausada])

    round_ = await setup.service.register_due(limit=100)

    assert round_.rules == 2
    assert round_.occurrences == 3
    assert sorted((t.user_id == ana, t.occurred_on) for t in setup.sink.added) == [
        (False, date(2026, 9, 17)),
        (True, date(2026, 8, 5)),
        (True, date(2026, 9, 5)),
    ]
    assert vencida_ha_dois_meses.next_occurrence_on == date(2026, 10, 5)
    assert vence_hoje.next_occurrence_on == date(2026, 10, 17)
    assert futura.next_occurrence_on == date(2026, 9, 20)
    assert setup.work.commits == 1


async def test_a_round_respects_the_batch_size() -> None:
    owner = uuid4()
    category = make_category(user_id=owner)
    rules = [
        make_rule(user_id=owner, category=category, next_occurrence_on=TODAY) for _ in range(3)
    ]
    setup = Setup(rules=rules)

    first = await setup.service.register_due(limit=2)
    second = await setup.service.register_due(limit=2)
    third = await setup.service.register_due(limit=2)

    assert (first.rules, second.rules, third.rules) == (2, 1, 0)
    assert setup.store.due_limit == 2
    assert len(setup.sink.added) == 3
