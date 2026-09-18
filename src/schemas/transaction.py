"""Contrato público do domínio de transações.

Duas decisões de contrato que vêm do desenho:

- **`amount` sai como string**, não como número. `10.10` em JSON é um double, e
  quem consome o desserializa como float — o erro de representação entra na
  ponta do cliente mesmo que o banco guarde `NUMERIC`. Sempre com duas casas,
  para que `"10.50"` não vire `"10.5"` e o cliente não precise formatar.
- **`kind` não é campo de entrada.** O tipo do lançamento é o da categoria (ver
  `models.transaction`); aceitá-lo no corpo seria abrir caminho para
  contradizer a categoria escolhida.

`recurrence` (criação e edição) pede que o lançamento se repita todo mês; a
regra em si se edita em `/recurring-transactions`.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_serializer

from models.category import CategoryKind
from models.recurring_transaction import DAY_OF_MONTH_MAX, DAY_OF_MONTH_MIN
from models.transaction import (
    AMOUNT_DECIMAL_PLACES,
    AMOUNT_MAX_DIGITS,
    DESCRIPTION_MAX_LENGTH,
)
from schemas.base import PatchIn

type Money = Annotated[
    Decimal,
    Field(gt=0, max_digits=AMOUNT_MAX_DIGITS, decimal_places=AMOUNT_DECIMAL_PLACES),
]
"""Valor de lançamento: positivo e nos limites do `NUMERIC(14,2)` da coluna.

`gt=0` repete de propósito o CHECK do banco. O banco é a autoridade; esta
validação existe para a recusa sair como 422 com o campo apontado, em vez de
500 na violação de constraint.
"""

type Description = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=DESCRIPTION_MAX_LENGTH),
]

type DayOfMonth = Annotated[int, Field(ge=DAY_OF_MONTH_MIN, le=DAY_OF_MONTH_MAX)]
"""De 1 a 31, como o CHECK da coluna; em mês mais curto, cai no último dia."""


class TransactionCategoryOut(BaseModel):
    """A categoria como ela aparece dentro de um lançamento.

    Aninhada em vez de um `category_id` solto: listar 50 lançamentos e ter de
    buscar cada categoria à parte para escrever o nome na tela é o N+1 saindo
    do servidor e virando problema de quem consome.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    kind: CategoryKind
    is_global: bool


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    amount: Decimal
    kind: CategoryKind
    """Derivado da categoria, nunca gravado — ver `models.transaction`."""

    occurred_on: date
    description: str
    category: TransactionCategoryOut
    recurring_transaction_id: UUID | None = None
    """A recorrência ligada ao lançamento; `None` no avulso."""

    # O investimento que originou o lançamento; `None` no lançamento comum, e
    # também depois de a posição ser excluída (`ON DELETE SET NULL`). Sai no
    # contrato para o extrato marcar a linha e levar de volta à posição.
    investment_id: UUID | None = None

    created_at: datetime

    @field_serializer("amount")
    def _amount_as_string(self, amount: Decimal) -> str:
        return f"{amount:.{AMOUNT_DECIMAL_PLACES}f}"


class RecurrenceIn(BaseModel):
    """Pedido de repetir o lançamento todo mês."""

    model_config = ConfigDict(extra="forbid")

    day_of_month: DayOfMonth


class TransactionCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Money
    category_id: UUID
    occurred_on: date | None = None
    """Ausente é "hoje" — resolvido pelo `Clock`, no fuso da aplicação."""

    description: Description = ""
    recurrence: RecurrenceIn | None = None
    """Cria a recorrência no mesmo commit; ela começa no mês seguinte ao do lançamento."""


class TransactionUpdateIn(PatchIn):
    """Todos os campos opcionais: é PATCH — ver `schemas.base.PatchIn`.

    `occurred_on` é `date | None` como os demais, e `None` aqui significa "não
    mexa" — não "volte para hoje". Quem quer mudar a data manda a data.
    """

    amount: Money | None = None
    category_id: UUID | None = None
    occurred_on: date | None = None
    description: Description | None = None
    recurrence: RecurrenceIn | None = None
    """Torna recorrente um lançamento avulso; se ele já tiver recorrência, 409.

    Nulo é "não mexa": pausar ou excluir a regra é em `/recurring-transactions`.
    """


class TransactionPageOut(BaseModel):
    """Página de listagem: `total` é do filtro inteiro, não do que veio nesta."""

    items: list[TransactionOut]
    total: int
    limit: int
    offset: int
