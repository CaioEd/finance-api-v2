"""Contrato público das recorrências.

Herda as duas decisões de `schemas.transaction` — `amount` sai como string e
`kind` não é campo de entrada — e acrescenta uma, própria: **`next_occurrence_on`
só sai, nunca entra.** A próxima data é consequência do dia do mês, da data de
início e do que já foi registrado; aceitá-la no corpo abriria caminho para
registrar o mesmo mês duas vezes, ou para pular um sem ninguém ver.
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
    """Derivado da categoria, nunca gravado — ver `models.recurring_transaction`."""

    description: str
    category: TransactionCategoryOut
    day_of_month: int
    next_occurrence_on: date
    """A próxima data em que o lançamento será registrado, se a regra estiver ativa."""

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
    """A partir de quando registrar. Ausente, ou no passado, vale como hoje.

    Recorrência não lança meses que já passaram: quem quer o histórico lança o
    histórico. Sem essa trava, `starts_on=2001-01-01` criaria trezentos
    lançamentos numa requisição.
    """


class RecurringTransactionUpdateIn(PatchIn):
    """Todos os campos opcionais: é PATCH — ver `schemas.base.PatchIn`.

    O que muda aqui vale para as próximas ocorrências; o que já foi registrado
    é lançamento comum e se edita em `/transactions`.
    """

    amount: Money | None = None
    category_id: UUID | None = None
    description: Description | None = None
    day_of_month: DayOfMonth | None = None
    is_active: bool | None = None
    """`false` pausa, `true` retoma — da próxima data a partir de hoje, sem cobrar a pausa."""


class RecurringTransactionPageOut(BaseModel):
    """Página de listagem: `total` é do filtro inteiro, não do que veio nesta."""

    items: list[RecurringTransactionOut]
    total: int
    limit: int
    offset: int
