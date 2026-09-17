"""Contrato de entrada e saída das recorrências — e o pedaço dele que mora no lançamento.

Sem I/O. A validação daqui repete de propósito os CHECKs da tabela (`amount >
0`, `day_of_month BETWEEN 1 AND 31`) para a recusa sair como 422 com o campo
apontado; quem tem a palavra final é o banco.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from models.category import CategoryKind
from schemas.recurring_transaction import (
    RecurringTransactionCreateIn,
    RecurringTransactionOut,
    RecurringTransactionUpdateIn,
)
from schemas.transaction import TransactionCategoryOut, TransactionCreateIn

CATEGORY_ID = uuid4()


def create(**overrides: object) -> RecurringTransactionCreateIn:
    payload: dict[str, object] = {"amount": "39.90", "category_id": CATEGORY_ID, "day_of_month": 5}
    return RecurringTransactionCreateIn.model_validate(payload | overrides)


@pytest.mark.parametrize("day", [0, -1, 32])
def test_a_day_outside_the_month_is_rejected(day: int) -> None:
    with pytest.raises(ValidationError):
        create(day_of_month=day)


@pytest.mark.parametrize("day", [1, 28, 31])
def test_every_day_from_1_to_31_is_accepted(day: int) -> None:
    """31 é válido: "todo dia 31" é o último dia de todo mês."""
    assert create(day_of_month=day).day_of_month == day


def test_the_day_is_required() -> None:
    with pytest.raises(ValidationError):
        RecurringTransactionCreateIn.model_validate({"amount": "1.00", "category_id": CATEGORY_ID})


@pytest.mark.parametrize("amount", ["0", "-1", "10.001"])
def test_the_amount_follows_the_rule_of_the_transaction(amount: str) -> None:
    with pytest.raises(ValidationError):
        create(amount=amount)


@pytest.mark.parametrize("field", ["next_occurrence_on", "kind", "is_active", "user_id"])
def test_what_the_service_decides_is_not_an_input_field(field: str) -> None:
    """A próxima data é consequência do dia e do início; aceitá-la pularia ou repetiria mês.

    `is_active` também não entra na criação: regra nasce ativa, e pausa é edição.
    """
    with pytest.raises(ValidationError):
        create(**{field: "2026-10-05" if "occurrence" in field else "x"})


def test_the_start_date_and_the_description_are_optional() -> None:
    data = create()

    assert data.starts_on is None
    assert data.description == ""
    assert create(description="  Netflix  ").description == "Netflix"


def test_the_update_leaves_out_what_came_as_null() -> None:
    data = RecurringTransactionUpdateIn.model_validate(
        {
            "amount": None,
            "category_id": None,
            "description": None,
            "day_of_month": 10,
            "is_active": None,
        }
    )

    assert data.changes() == {"day_of_month": 10}


def test_pausing_is_a_change_even_though_it_is_false() -> None:
    """`false` não é ausente: `exclude_none` tira nulo, não falso."""
    assert RecurringTransactionUpdateIn(is_active=False).changes() == {"is_active": False}


def test_the_update_rejects_a_day_outside_the_month_and_the_next_date() -> None:
    with pytest.raises(ValidationError):
        RecurringTransactionUpdateIn.model_validate({"day_of_month": 32})
    with pytest.raises(ValidationError):
        RecurringTransactionUpdateIn.model_validate({"next_occurrence_on": "2026-10-05"})


def test_the_amount_leaves_as_a_string_with_two_places() -> None:
    out = RecurringTransactionOut(
        id=uuid4(),
        amount=Decimal("39.9"),
        kind=CategoryKind.EXPENSE,
        description="Netflix",
        category=TransactionCategoryOut(
            id=CATEGORY_ID, name="Streaming", kind=CategoryKind.EXPENSE, is_global=False
        ),
        day_of_month=5,
        next_occurrence_on=date(2026, 10, 5),
        is_active=True,
        created_at=datetime(2026, 9, 17, 12, 0, tzinfo=UTC),
    )

    payload = json.loads(out.model_dump_json())

    assert payload["amount"] == "39.90"
    assert payload["next_occurrence_on"] == "2026-10-05"


# ------------------------------------------------------ dentro do lançamento


def test_a_transaction_may_ask_to_repeat_every_month() -> None:
    data = TransactionCreateIn.model_validate(
        {"amount": "39.90", "category_id": CATEGORY_ID, "recurrence": {"day_of_month": 5}}
    )

    assert data.recurrence is not None
    assert data.recurrence.day_of_month == 5


def test_the_recurrence_of_a_transaction_is_optional() -> None:
    assert (
        TransactionCreateIn.model_validate(
            {"amount": "1.00", "category_id": CATEGORY_ID}
        ).recurrence
        is None
    )


@pytest.mark.parametrize(
    "recurrence",
    [{"day_of_month": 0}, {"day_of_month": 32}, {}, {"day_of_month": 5, "starts_on": "2026-10-01"}],
)
def test_the_recurrence_of_a_transaction_is_validated(recurrence: dict[str, object]) -> None:
    """Sem `starts_on` aqui: a regra de um lançamento começa no mês seguinte ao dele."""
    with pytest.raises(ValidationError):
        TransactionCreateIn.model_validate(
            {"amount": "1.00", "category_id": CATEGORY_ID, "recurrence": recurrence}
        )
