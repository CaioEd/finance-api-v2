"""Contrato de entrada e saída de transação.

Sem I/O: o que se testa aqui é resolvido antes de qualquer sessão de banco
existir. Esta validação existe para a recusa sair como 422 com o campo
apontado, e não como 500 na violação de constraint — mas quem tem a palavra
final é o banco, com `CHECK (amount > 0)` e `NUMERIC(14,2)`. Que os dois
concordem ainda não tem teste de integração.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from models.category import CategoryKind
from schemas.transaction import (
    TransactionCategoryOut,
    TransactionCreateIn,
    TransactionOut,
    TransactionUpdateIn,
)

CATEGORY_ID = uuid4()


def create(**overrides: object) -> TransactionCreateIn:
    payload: dict[str, object] = {"amount": Decimal("10.00"), "category_id": CATEGORY_ID}
    return TransactionCreateIn(**(payload | overrides))  # type: ignore[arg-type]


# ------------------------------------------------------------------ entrada


@pytest.mark.parametrize("amount", ["0", "0.00", "-1", "-0.01"])
def test_an_amount_that_is_not_positive_is_rejected(amount: str) -> None:
    """O sinal do lançamento é o `kind` da categoria; valor negativo seria uma segunda fonte."""
    with pytest.raises(ValidationError):
        create(amount=Decimal(amount))


def test_more_than_two_decimal_places_is_rejected() -> None:
    """A coluna é `NUMERIC(14,2)`: aceitar centavos de centavo é arredondar em silêncio."""
    with pytest.raises(ValidationError):
        create(amount=Decimal("10.001"))


def test_an_amount_beyond_the_column_is_rejected() -> None:
    with pytest.raises(ValidationError):
        create(amount=Decimal("1234567890123.00"))  # 13 dígitos inteiros, a coluna aceita 12


def test_the_amount_accepts_a_string() -> None:
    """O cliente manda string justamente para não passar por float no caminho."""
    assert create(amount="1500.75").amount == Decimal("1500.75")


def test_kind_is_not_an_input_field() -> None:
    """O tipo vem da categoria. Aceitá-lo aqui abriria caminho para contradizê-la."""
    with pytest.raises(ValidationError):
        create(kind="income")


def test_user_id_is_not_an_input_field() -> None:
    """O dono vem do token, nunca do corpo."""
    with pytest.raises(ValidationError):
        create(user_id=uuid4())


def test_the_description_is_optional_and_trimmed() -> None:
    assert create().description == ""
    assert create(description="  Feira da semana  ").description == "Feira da semana"


def test_a_description_beyond_the_column_is_rejected() -> None:
    with pytest.raises(ValidationError):
        create(description="x" * 201)


def test_occurred_on_is_optional() -> None:
    """Ausente significa "hoje", e quem resolve isso é o serviço, com o `Clock`."""
    assert create().occurred_on is None
    assert create(occurred_on=date(2026, 9, 3)).occurred_on == date(2026, 9, 3)


def test_the_update_leaves_out_what_was_not_sent() -> None:
    data = TransactionUpdateIn(amount=Decimal("12.00"))

    assert data.changes() == {"amount": Decimal("12.00")}


def test_the_update_leaves_out_what_came_as_null() -> None:
    """Nulo é "não mexa", igual a ausente — ver `schemas.base.PatchIn`."""
    data = TransactionUpdateIn(
        amount=Decimal("12.00"), category_id=None, occurred_on=None, description=None
    )

    assert data.changes() == {"amount": Decimal("12.00")}


def test_an_empty_update_changes_nothing() -> None:
    assert TransactionUpdateIn().changes() == {}


# -------------------------------------------------------------------- saída


def out(amount: Decimal) -> TransactionOut:
    return TransactionOut(
        id=uuid4(),
        amount=amount,
        kind=CategoryKind.EXPENSE,
        occurred_on=date(2026, 9, 3),
        description="Feira",
        category=TransactionCategoryOut(
            id=CATEGORY_ID, name="Mercado", kind=CategoryKind.EXPENSE, is_global=False
        ),
        created_at=datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("stored", "serialized"),
    [
        (Decimal("10.50"), "10.50"),
        (Decimal("10.5"), "10.50"),
        (Decimal("10"), "10.00"),
        (Decimal("1234567890.99"), "1234567890.99"),
    ],
)
def test_the_amount_leaves_as_a_string_with_two_places(stored: Decimal, serialized: str) -> None:
    """Número em JSON é double na ponta de quem consome; string atravessa intacta.

    As duas casas são sempre escritas, para o cliente não ter de formatar —
    `"10.5"` num extrato é um centavo desaparecendo da tela.
    """
    payload = json.loads(out(stored).model_dump_json())

    assert payload["amount"] == serialized
    assert isinstance(payload["amount"], str)
