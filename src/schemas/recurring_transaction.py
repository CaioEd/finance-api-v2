"""Contrato público das recorrências.

Mesmas regras de `schemas.transaction` (`amount` string, `kind` só de saída), e
`next_occurrence_on` também só sai: aceitá-lo permitiria pular ou repetir mês.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_serializer

from models.category import CategoryKind
from models.transaction import AMOUNT_DECIMAL_PLACES
from schemas.base import PatchIn
from schemas.transaction import DayOfMonth, Description, Money, TransactionCategoryOut


class RecurringTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    amount: Decimal
    kind: CategoryKind
    description: str
    category: TransactionCategoryOut
    day_of_month: int
    next_occurrence_on: date
    is_active: bool
    created_at: datetime

    @field_serializer("amount")
    def _amount_as_string(self, amount: Decimal) -> str:
        return f"{amount:.{AMOUNT_DECIMAL_PLACES}f}"


class RecurringTransactionCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Money
    category_id: UUID
    description: Description = ""
    day_of_month: DayOfMonth
    starts_on: date | None = None
    """Ausente ou no passado vale como hoje: recorrência não lança meses que passaram."""


class RecurringTransactionUpdateIn(PatchIn):
    """PATCH (ver `schemas.base.PatchIn`); vale para as próximas ocorrências."""

    amount: Money | None = None
    category_id: UUID | None = None
    description: Description | None = None
    day_of_month: DayOfMonth | None = None
    is_active: bool | None = None
    """`false` pausa; `true` retoma a partir de hoje, sem lançar a pausa."""


class RecurringTransactionPageOut(BaseModel):
    items: list[RecurringTransactionOut]
    total: int
    limit: int
    offset: int
