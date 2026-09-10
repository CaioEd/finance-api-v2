"""Regra de saldos: mês corrente, mês a mês e intervalo de datas.

Python puro, como os demais serviços: nada aqui conhece HTTP nem FastAPI, e a
dependência de dados é um `Protocol` — o que permite exercitar a regra sem
Postgres (`tests/unit/test_balance_service.py`).

Saldo não tem CRUD: as três consultas são leitura, não existe `add`, nem
`commit`, nem `UnitOfWork`. O que existe de regra é a **resolução do recorte**,
e é ela que mora aqui:

1. **"Mês corrente" vem do `Clock`**, nunca de `date.today()`. É o que prende a
   resposta ao fuso da aplicação: às 23h de 31 de agosto em São Paulo o mês
   corrente ainda é agosto, embora em UTC já seja setembro.
2. **Toda janela tem as duas pontas resolvidas antes da consulta.** O pedido
   pode trazer nenhuma, uma ou as duas; o serviço completa o que faltar sempre
   com `DEFAULT_MONTHS` meses, ancorados na ponta que veio. Assim `?from_month=`
   sozinho não depende de que mês é hoje para saber quanto devolver.
3. **A série mês a mês não tem buraco.** Mês sem lançamento sai zerado, e não
   omitido — omitir obrigaria quem consome a distinguir "não gastei nada" de
   "o servidor não me contou", que é a mesma informação com duas leituras.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from functools import reduce
from operator import add
from typing import Protocol
from uuid import UUID

from core.clock import Clock, MonthRange, months_between, shift_month
from core.errors import InvalidPeriodError, PeriodTooLongError
from models.user import User
from repositories.balance_repository import Totals

DEFAULT_MONTHS = 12
"""Tamanho da janela quando o pedido não a delimita: um ano, a comparação óbvia."""

MAX_MONTHS = 120
"""Teto da série mês a mês: dez anos. Ver `PeriodTooLongError`."""


class BalanceStore(Protocol):
    """O que este serviço precisa de um repositório de saldos.

    Só leitura agregada. Pedir o repositório concreto deixaria o serviço a um
    atributo de distância de consultar lançamento a lançamento, que é
    exatamente o que este domínio existe para não fazer.
    """

    async def totals_in(self, user_id: UUID, *, first_day: date, last_day: date) -> Totals: ...

    async def monthly_totals(
        self, user_id: UUID, *, first_day: date, last_day: date
    ) -> dict[date, Totals]: ...


@dataclass(frozen=True, slots=True)
class MonthBalance:
    """O saldo de um mês civil, com o mês que ele descreve junto."""

    month: MonthRange
    totals: Totals


@dataclass(frozen=True, slots=True)
class MonthlySeries:
    """A série mês a mês e o total do período que ela cobre.

    `totals` é a soma dos meses, não uma segunda consulta: as parcelas são
    disjuntas e exatas em `Decimal`, então somá-las dá o mesmo número que o
    banco daria — sem percorrer as mesmas linhas duas vezes.
    """

    months: Sequence[MonthBalance]
    totals: Totals


@dataclass(frozen=True, slots=True)
class PeriodBalance:
    """O saldo de um intervalo de datas arbitrário, que pode não casar com meses."""

    first_day: date
    last_day: date
    totals: Totals


class BalanceService:
    def __init__(self, *, balances: BalanceStore, clock: Clock) -> None:
        self._balances = balances
        self._clock = clock

    async def current_month(self, user: User) -> MonthBalance:
        """O saldo do mês em que estamos, no fuso da aplicação."""
        month = self._clock.current_month()
        return MonthBalance(month=month, totals=await self._totals_of(user, month))

    async def monthly(
        self, user: User, *, first: MonthRange | None = None, last: MonthRange | None = None
    ) -> MonthlySeries:
        """A série de um mês a outro, inclusive nas duas pontas e sem buraco."""
        first, last = self._window(first, last)
        by_month = await self._balances.monthly_totals(
            user.id, first_day=first.first_day, last_day=last.last_day
        )
        months = [
            MonthBalance(month=month, totals=by_month.get(month.first_day, Totals()))
            for month in months_between(first, last)
        ]
        return MonthlySeries(
            months=months,
            totals=reduce(add, (month.totals for month in months), Totals()),
        )

    async def in_range(self, user: User, *, first_day: date, last_day: date) -> PeriodBalance:
        """O saldo entre duas datas quaisquer — sem teto, porque é uma linha só.

        A série mês a mês cresce com o intervalo e por isso tem `MAX_MONTHS`;
        aqui o resultado tem o mesmo tamanho para um dia ou para um século.
        """
        if first_day > last_day:
            raise InvalidPeriodError()
        totals = await self._balances.totals_in(user.id, first_day=first_day, last_day=last_day)
        return PeriodBalance(first_day=first_day, last_day=last_day, totals=totals)

    def _window(
        self, first: MonthRange | None, last: MonthRange | None
    ) -> tuple[MonthRange, MonthRange]:
        """Completa o que o pedido não trouxe, e recusa o que não dá para responder.

        Cada ponta ausente é resolvida a partir da que veio, e não do relógio:
        só quando **nenhuma** vem é que "hoje" entra, fechando os últimos
        `DEFAULT_MONTHS` meses. Ancorar na ponta informada é o que faz
        `?from_month=2020-01` devolver sempre o mesmo ano, independentemente da
        data em que a pergunta foi feita.
        """
        if last is not None:
            start = first if first is not None else shift_month(last, -(DEFAULT_MONTHS - 1))
            end = last
        elif first is not None:
            start, end = first, shift_month(first, DEFAULT_MONTHS - 1)
        else:
            end = self._clock.current_month()
            start = shift_month(end, -(DEFAULT_MONTHS - 1))

        if start.first_day > end.first_day:
            raise InvalidPeriodError()

        span = len(months_between(start, end))
        if span > MAX_MONTHS:
            raise PeriodTooLongError(
                f"O período pedido tem {span} meses, e o máximo por consulta é {MAX_MONTHS}."
            )
        return start, end

    async def _totals_of(self, user: User, month: MonthRange) -> Totals:
        return await self._balances.totals_in(
            user.id, first_day=month.first_day, last_day=month.last_day
        )
