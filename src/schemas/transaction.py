"""Contrato público do domínio de transações.

Duas decisões de contrato que vêm do desenho:

- **`amount` sai como string**, não como número. `10.10` em JSON é um double, e
  quem consome o desserializa como float — o erro de representação entra na
  ponta do cliente mesmo que o banco guarde `NUMERIC`. Sempre com duas casas,
  para que `"10.50"` não vire `"10.5"` e o cliente não precise formatar.
- **`kind` não é campo de entrada.** O tipo do lançamento é o da categoria (ver
  `models.transaction`); aceitá-lo no corpo seria abrir caminho para
  contradizer a categoria escolhida.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_serializer

from models.category import CategoryKind
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
    created_at: datetime

    @field_serializer("amount")
    def _amount_as_string(self, amount: Decimal) -> str:
        return f"{amount:.{AMOUNT_DECIMAL_PLACES}f}"


class TransactionCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Money
    category_id: UUID
    occurred_on: date | None = None
    """Ausente é "hoje" — resolvido pelo `Clock`, no fuso da aplicação."""

    description: Description = ""


class TransactionUpdateIn(PatchIn):
    """Todos os campos opcionais: é PATCH — ver `schemas.base.PatchIn`.

    `occurred_on` é `date | None` como os demais, e `None` aqui significa "não
    mexa" — não "volte para hoje". Quem quer mudar a data manda a data.
    """

    amount: Money | None = None
    category_id: UUID | None = None
    occurred_on: date | None = None
    description: Description | None = None


class TransactionPageOut(BaseModel):
    """Página de listagem: `total` é do filtro inteiro, não do que veio nesta."""

    items: list[TransactionOut]
    total: int
    limit: int
    offset: int
