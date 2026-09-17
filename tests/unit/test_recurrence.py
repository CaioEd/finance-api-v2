"""Calendário das recorrências: dia 31 em mês curto, meses perdidos, regra pausada."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from core.clock import month_range
from models.category import Category, CategoryKind
from models.recurring_transaction import RecurringTransaction
from services.recurrence import (
    first_occurrence_from,
    following_occurrence,
    occurrence_in,
    register_due,
)


def a_rule(
    *, day_of_month: int, next_occurrence_on: date, is_active: bool = True
) -> RecurringTransaction:
    user_id = uuid4()
    category = Category(id=uuid4(), user_id=user_id, name="Streaming", kind=CategoryKind.EXPENSE)
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


# ----------------------------------------------------------- em que dia cai


@pytest.mark.parametrize(
    ("year", "month", "day", "expected"),
    [
        (2026, 9, 5, date(2026, 9, 5)),
        (2026, 2, 31, date(2026, 2, 28)),
        (2028, 2, 31, date(2028, 2, 29)),  # bissexto
        (2026, 4, 31, date(2026, 4, 30)),
        (2026, 2, 29, date(2026, 2, 28)),
        (2026, 1, 31, date(2026, 1, 31)),
    ],
)
def test_the_occurrence_falls_on_the_last_day_when_the_month_is_shorter(
    year: int, month: int, day: int, expected: date
) -> None:
    assert occurrence_in(month_range(year, month), day) == expected


def test_the_first_occurrence_is_still_this_month_when_the_day_has_not_passed() -> None:
    assert first_occurrence_from(date(2026, 9, 3), 5) == date(2026, 9, 5)


def test_the_start_day_itself_counts() -> None:
    assert first_occurrence_from(date(2026, 9, 5), 5) == date(2026, 9, 5)


def test_the_first_occurrence_moves_to_next_month_when_the_day_has_passed() -> None:
    assert first_occurrence_from(date(2026, 9, 17), 5) == date(2026, 10, 5)


def test_the_first_occurrence_crosses_the_year() -> None:
    assert first_occurrence_from(date(2026, 12, 20), 10) == date(2027, 1, 10)


def test_day_31_comes_back_to_31_after_february() -> None:
    """Cada data sai do dia da regra, não da anterior — senão fevereiro prenderia o 28."""
    dates = [date(2026, 1, 31)]
    for _ in range(3):
        dates.append(following_occurrence(dates[-1], 31))

    assert dates == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]


# ------------------------------------------------------------- o que venceu


def test_nothing_is_due_before_the_date() -> None:
    rule = a_rule(day_of_month=5, next_occurrence_on=date(2026, 10, 5))

    assert register_due(rule, date(2026, 10, 4)) == []
    assert rule.next_occurrence_on == date(2026, 10, 5)


def test_the_occurrence_is_due_on_its_own_day() -> None:
    rule = a_rule(day_of_month=5, next_occurrence_on=date(2026, 10, 5))

    [transaction] = register_due(rule, date(2026, 10, 5))

    assert transaction.occurred_on == date(2026, 10, 5)
    assert rule.next_occurrence_on == date(2026, 11, 5), "a data avança junto"


def test_the_occurrence_copies_the_rule_and_points_back_to_it() -> None:
    rule = a_rule(day_of_month=5, next_occurrence_on=date(2026, 10, 5))

    [transaction] = register_due(rule, date(2026, 10, 5))

    assert transaction.user_id == rule.user_id
    assert transaction.category_id == rule.category_id
    assert transaction.category is rule.category
    assert transaction.kind is CategoryKind.EXPENSE
    assert transaction.amount == Decimal("39.90")
    assert transaction.description == "Netflix"
    assert transaction.recurring_transaction_id == rule.id


def test_every_missed_month_is_registered_with_its_own_date() -> None:
    """Agendador fora do ar de janeiro a abril: quatro meses devidos, quatro lançamentos."""
    rule = a_rule(day_of_month=31, next_occurrence_on=date(2026, 1, 31))

    due = register_due(rule, date(2026, 4, 30))

    assert [t.occurred_on for t in due] == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
    ]
    assert rule.next_occurrence_on == date(2026, 5, 31)


def test_registering_twice_on_the_same_day_does_not_repeat_the_month() -> None:
    rule = a_rule(day_of_month=5, next_occurrence_on=date(2026, 10, 5))

    first = register_due(rule, date(2026, 10, 20))
    second = register_due(rule, date(2026, 10, 20))

    assert len(first) == 1
    assert second == []


def test_a_paused_rule_owes_nothing() -> None:
    rule = a_rule(day_of_month=5, next_occurrence_on=date(2026, 1, 5), is_active=False)

    assert register_due(rule, date(2026, 10, 20)) == []
    assert rule.next_occurrence_on == date(2026, 1, 5)
