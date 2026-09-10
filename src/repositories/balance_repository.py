"""Acesso a dados de saldos: agrega, nunca lista.

O escopo por dono é imposto **aqui**, como nos demais repositórios: toda
consulta nasce de `Transaction.user_id == user_id`, e não existe método que
some o lançamento de outra pessoa. Num agregado o esquecimento seria pior que
numa listagem — vazaria como um número, sem nada na resposta que denunciasse de
onde ele veio.

Duas decisões de consulta que valem a pena registrar:

- **Um `SELECT` com `FILTER`, não dois.** Receita e despesa saem da mesma
  varredura (`SUM(amount) FILTER (WHERE categories.kind = ...)`). Duas
  consultas percorreriam exatamente o mesmo recorte, e ainda poderiam observar
  estados diferentes se algo fosse gravado entre elas.
- **`JOIN` com `categories` sempre.** O `kind` do lançamento não é coluna de
  `transactions` — é da categoria (ver `models.transaction`), e é dela que sai
  o sinal de cada parcela. O JOIN é interno porque `category_id` é `NOT NULL`.

O `NULL` que o `SUM` devolve quando nada casa com o filtro é resolvido no
`coalesce`, e não em Python: assim o mês sem lançamento nenhum já chega como
`0.00`, e quem consome não precisa distinguir "não gastei" de "não sei".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ColumnElement, Date, Numeric, Select, cast, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.category import Category, CategoryKind
from models.transaction import AMOUNT_DECIMAL_PLACES, AMOUNT_MAX_DIGITS, Transaction

ZERO = Decimal("0.00")

MONEY = Numeric(AMOUNT_MAX_DIGITS, AMOUNT_DECIMAL_PLACES)
"""O mesmo tipo da coluna `amount`, para o zero do `coalesce` não virar `INTEGER`."""


@dataclass(frozen=True, slots=True)
class Totals:
    """Quanto entrou e quanto saiu num recorte.

    Os dois são positivos — `amount` é sempre positivo, e o que separa receita
    de despesa é o `kind` da categoria. Quem tem sinal é `net`, e ele é
    **derivado**: guardar o saldo ao lado das parcelas criaria uma segunda
    fonte para o mesmo fato, que é a decisão que `models.transaction` já recusa.
    """

    income: Decimal = ZERO
    expense: Decimal = ZERO

    @property
    def net(self) -> Decimal:
        """Saldo do recorte. Negativo quando se gastou mais do que entrou."""
        return self.income - self.expense

    def __add__(self, other: Totals) -> Totals:
        """Somar recortes disjuntos é somar as parcelas.

        É como o total de um período sai da série mês a mês, sem uma segunda
        consulta que percorreria de novo as mesmas linhas.
        """
        return Totals(income=self.income + other.income, expense=self.expense + other.expense)


def _sum_of(kind: CategoryKind) -> ColumnElement[Decimal]:
    """`coalesce(SUM(amount) FILTER (WHERE categories.kind = <kind>), 0.00)`."""
    total: ColumnElement[Decimal] = func.coalesce(
        func.sum(Transaction.amount).filter(Category.kind == kind),
        literal(ZERO, MONEY),
    )
    return total


def _scoped(user_id: UUID, first_day: date, last_day: date) -> Select[tuple[Decimal, Decimal]]:
    """As duas somas do recorte de um dono, entre duas datas inclusive.

    O intervalo é fechado nas duas pontas — `>=` e `<=`, nunca `<` no fim.
    Fosse aberto, o lançamento do último dia do mês sumiria do saldo daquele
    mês sem aparecer no do seguinte.
    """
    return (
        select(_sum_of(CategoryKind.INCOME), _sum_of(CategoryKind.EXPENSE))
        .select_from(Transaction)
        .join(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.user_id == user_id,
            Transaction.occurred_on >= first_day,
            Transaction.occurred_on <= last_day,
        )
    )


class BalanceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def totals_in(self, user_id: UUID, *, first_day: date, last_day: date) -> Totals:
        """Os totais do intervalo inteiro, numa linha só.

        Agregado sem `GROUP BY` sempre devolve exatamente uma linha — inclusive
        quando não há lançamento nenhum, e é por isso que `one()` aqui não tem
        como estourar.
        """
        income, expense = (await self._session.execute(_scoped(user_id, first_day, last_day))).one()
        return Totals(income=income, expense=expense)

    async def monthly_totals(
        self, user_id: UUID, *, first_day: date, last_day: date
    ) -> dict[date, Totals]:
        """Os totais agrupados por mês de competência, indexados pelo dia 1º.

        Devolve **só os meses que têm lançamento** — o resultado é esparso de
        propósito. Completar a série com os meses vazios é decisão de
        apresentação, e mora no serviço (`BalanceService.monthly`), onde o
        intervalo pedido é conhecido.
        """
        month = cast(func.date_trunc("month", Transaction.occurred_on), Date).label("month")
        statement = (
            _scoped(user_id, first_day, last_day).add_columns(month).group_by(month).order_by(month)
        )
        rows = (await self._session.execute(statement)).all()
        return {
            first_of_month: Totals(income=income, expense=expense)
            for income, expense, first_of_month in rows
        }
