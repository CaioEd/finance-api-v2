"""Contrato de investimentos: o que o corpo aceita e a forma do que sai.

O validador de `InvestmentCreateIn` é a fronteira entre as duas metades da
tabela — ele decide, a partir do `type`, quais campos são obrigatórios e quais
não se aplicam. Errar aqui é gravar um CDB com quantidade de cotas, ou uma ação
com data de vencimento; nos dois casos a coluna existe e aceitaria o valor.

A serialização também é contrato: dinheiro sai com duas casas e quantidade sai
normalizada, sem notação científica.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from models.investment import Investment, InvestmentAsset, InvestmentType, RateIndex
from schemas.investment import (
    InvestmentCreateIn,
    InvestmentOut,
    InvestmentUpdateIn,
    unit_string,
)

A_STOCK = {
    "type": "br_stock",
    "symbol": "PETR4",
    "quantity": "100",
    "average_price": "30.00",
}
A_CDB = {
    "type": "cdb",
    "name": "CDB Liquidez",
    "invested_amount": "5000.00",
    "rate_index": "cdi",
    "rate_percent": "102",
    "applied_on": "2026-01-15",
}


def message_of(refusal: pytest.ExceptionInfo[ValidationError]) -> str:
    return str(refusal.value)


# --------------------------------------------------------- as duas metades


def test_variable_income_demands_symbol_quantity_and_price() -> None:
    with pytest.raises(ValidationError) as refusal:
        InvestmentCreateIn.model_validate({"type": "br_stock"})

    assert "symbol" in message_of(refusal)
    assert "quantity" in message_of(refusal)
    assert "average_price" in message_of(refusal)


def test_variable_income_refuses_the_fields_of_fixed_income() -> None:
    """Ação não tem índice nem vencimento; aceitar e ignorar mentiria no 201."""
    with pytest.raises(ValidationError) as refusal:
        InvestmentCreateIn.model_validate({**A_STOCK, "rate_index": "cdi"})

    assert "rate_index" in message_of(refusal)


def test_fixed_income_demands_the_amount_and_the_date() -> None:
    with pytest.raises(ValidationError) as refusal:
        InvestmentCreateIn.model_validate({"type": "cdb", "name": "CDB"})

    assert "invested_amount" in message_of(refusal)
    assert "applied_on" in message_of(refusal)


def test_fixed_income_refuses_the_fields_of_variable_income() -> None:
    with pytest.raises(ValidationError) as refusal:
        InvestmentCreateIn.model_validate({**A_CDB, "quantity": "10"})

    assert "quantity" in message_of(refusal)


def test_savings_fills_the_index_and_the_rate_by_itself() -> None:
    """Poupança não tem taxa a contratar: a regra é a do Banco Central."""
    body = InvestmentCreateIn.model_validate(
        {
            "type": "savings",
            "name": "Poupança",
            "invested_amount": "1000.00",
            "applied_on": "2026-01-01",
        }
    )

    assert body.rate_index is RateIndex.SAVINGS
    assert body.rate_percent == Decimal("100")


def test_a_maturity_before_the_application_is_refused() -> None:
    with pytest.raises(ValidationError) as refusal:
        InvestmentCreateIn.model_validate({**A_CDB, "matures_on": "2025-01-01"})

    assert "matures_on" in message_of(refusal)


# ------------------------------------------------------------------ o símbolo


def test_the_symbol_is_stripped_and_upper_cased() -> None:
    """O índice único compara `upper(symbol)`: deixar a caixa passar duplicaria o ativo."""
    body = InvestmentCreateIn.model_validate({**A_STOCK, "symbol": "  petr4 "})

    assert body.symbol == "PETR4"


def test_an_asset_in_dollars_demands_what_was_paid_in_reais() -> None:
    with pytest.raises(ValidationError) as refusal:
        InvestmentCreateIn.model_validate(
            {"type": "us_stock", "symbol": "AAPL", "quantity": "5", "average_price": "200"}
        )

    assert "invested_amount" in message_of(refusal)


def test_an_unknown_field_is_refused_instead_of_ignored() -> None:
    """`extra="forbid"`: um `200` não pode afirmar que gravou o que descartou."""
    with pytest.raises(ValidationError):
        InvestmentCreateIn.model_validate({**A_STOCK, "investment_class": "fixed_income"})


# ------------------------------------------------------------------- o PATCH


def test_a_patch_of_nulls_changes_nothing() -> None:
    """Campo ausente e campo nulo significam a mesma coisa — ver `schemas.base`."""
    patch = InvestmentUpdateIn.model_validate(
        dict.fromkeys(InvestmentUpdateIn.model_fields) | {"name": "Novo nome"}
    )

    assert patch.changes() == {"name": "Novo nome"}


def test_a_patch_does_not_accept_the_type_nor_the_symbol() -> None:
    """Trocar o ativo não é editar a posição: o preço médio ficaria de outra compra."""
    with pytest.raises(ValidationError):
        InvestmentUpdateIn.model_validate({"type": "crypto"})
    with pytest.raises(ValidationError):
        InvestmentUpdateIn.model_validate({"symbol": "VALE3"})


# ------------------------------------------------------------- a serialização


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("320.00000000"), "320"),
        (Decimal("0.01200000"), "0.012"),
        (Decimal("48.61370000"), "48.6137"),
        (Decimal("0"), "0"),
    ],
)
def test_a_quantity_comes_out_without_trailing_zeros_or_exponent(
    value: Decimal, expected: str
) -> None:
    """`normalize()` sozinho devolveria `3.2E+2`, e a tela mostraria isso."""
    assert unit_string(value) == expected


def test_the_output_derives_the_class_and_the_profit() -> None:
    asset = InvestmentAsset(
        id=uuid4(), type=InvestmentType.BR_STOCK, symbol="PETR4", name="PETR4", currency="BRL"
    )
    investment = Investment(
        id=uuid4(),
        user_id=uuid4(),
        type=InvestmentType.BR_STOCK,
        asset=asset,
        asset_id=asset.id,
        name="PETR4",
        quantity=Decimal("100"),
        average_price=Decimal("30"),
        invested_amount=Decimal("3000.00"),
        current_value=Decimal("3300.00"),
        created_at=datetime(2026, 9, 18, tzinfo=UTC),
    )

    body = InvestmentOut.model_validate(investment).model_dump(mode="json")

    assert body["investment_class"] == "variable_income"
    assert body["invested_amount"] == "3000.00"
    assert body["current_value"] == "3300.00"
    assert body["profit"] == "300.00"
    assert body["profit_percent"] == "10.0000"


def test_a_position_worth_nothing_has_no_percentage() -> None:
    """Dividir por zero devolveria `Infinity`, que não é JSON válido."""
    investment = Investment(
        id=uuid4(),
        user_id=uuid4(),
        type=InvestmentType.CDB,
        asset=None,
        name="CDB zerado",
        invested_amount=Decimal("0.00"),
        current_value=Decimal("0.00"),
        created_at=datetime(2026, 9, 18, tzinfo=UTC),
    )

    body = InvestmentOut.model_validate(investment).model_dump(mode="json")

    assert body["profit_percent"] is None
    assert body["asset"] is None


def test_money_with_more_than_two_places_is_refused_at_the_contract() -> None:
    """A coluna tem duas casas, e quem as impõe é o schema — não o serviço.

    Arredondar no serviço seria uma segunda regra para a mesma coisa, e a
    diferença entre as duas só apareceria para quem consome.
    """
    with pytest.raises(ValidationError):
        InvestmentUpdateIn.model_validate({"invested_amount": "1234.567"})
    with pytest.raises(ValidationError):
        InvestmentCreateIn.model_validate({**A_CDB, "invested_amount": "5000.001"})
