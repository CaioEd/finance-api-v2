"""Regra de saldos — sem banco e sem HTTP.

O serviço conversa com um `Protocol` (`BalanceStore`), não com o repositório
concreto, e o duble guarda o recorte que recebeu. É isso que permite cobrir
aqui o que de fato é regra e não SQL:

- de onde sai "mês corrente" (do `Clock`, no fuso da aplicação);
- que janela o serviço monta quando o pedido não a delimita, ou delimita só uma
  ponta;
- que a série sai **sem buraco**, com o mês vazio zerado;
- que o total é a soma da série, e não um número apurado por fora;
- o que vira 422.

Que a consulta realmente filtre por dono e some certo é assunto do repositório,
e está em `tests/integration/test_balance.py` — o duble reproduz a regra, e
reproduzir não é verificar.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from core.clock import Clock, month_range
from core.errors import InvalidPeriodError, PeriodTooLongError
from models.user import Role, User
from repositories.balance_repository import Totals
from services.balance_service import (
    DEFAULT_MONTHS,
    MAX_MONTHS,
    BalanceService,
    BalanceStore,
)

SAO_PAULO = ZoneInfo("America/Sao_Paulo")

MEIO_DE_SETEMBRO = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
VIRADA_DO_MES = datetime(2026, 9, 1, 2, 0, tzinfo=UTC)
"""1º de setembro às 2h em UTC — ainda 31 de agosto, às 23h, em São Paulo."""


def make_user() -> User:
    return User(
        id=uuid4(),
        email="ana@exemplo.com",
        username="ana",
        password_hash="$argon2id$hash-de-mentira",
        first_name="Ana",
        last_name="Ribeiro",
        role=Role.USER,
        is_active=True,
    )


def clock_at(moment: datetime) -> Clock:
    """Relógio parado num instante. O tempo é parâmetro, não leitura global."""
    return Clock(tz=SAO_PAULO, instant=lambda: moment)


def totals(income: str = "0.00", expense: str = "0.00") -> Totals:
    return Totals(income=Decimal(income), expense=Decimal(expense))


class FakeBalanceStore:
    """Implementa `BalanceStore` em memória, guardando o recorte que recebeu.

    Os totais são declarados por mês; `totals_in` soma os meses que o intervalo
    pedido cobre. Não é o SQL de verdade — é a regra de que o recorte pedido é o
    recorte somado, que é o que este arquivo tem como afirmar.
    """

    def __init__(self, by_month: dict[str, Totals] | None = None) -> None:
        self.by_month = {
            month_range(int(key[:4]), int(key[5:])).first_day: value
            for key, value in (by_month or {}).items()
        }
        self.last_query: dict[str, object] | None = None

    async def totals_in(self, user_id: UUID, *, first_day: date, last_day: date) -> Totals:
        self._record(user_id, first_day, last_day)
        found = self._within(first_day, last_day)
        return sum(found.values(), Totals())

    async def monthly_totals(
        self, user_id: UUID, *, first_day: date, last_day: date
    ) -> dict[date, Totals]:
        self._record(user_id, first_day, last_day)
        return self._within(first_day, last_day)

    def _record(self, user_id: UUID, first_day: date, last_day: date) -> None:
        self.last_query = {"user_id": user_id, "first_day": first_day, "last_day": last_day}

    def _within(self, first_day: date, last_day: date) -> dict[date, Totals]:
        return {
            month: value for month, value in self.by_month.items() if first_day <= month <= last_day
        }


def build_service(store: FakeBalanceStore, clock: Clock | None = None) -> BalanceService:
    # A anotação força a checagem de que o duble satisfaz o Protocol.
    balances: BalanceStore = store
    return BalanceService(balances=balances, clock=clock or clock_at(MEIO_DE_SETEMBRO))


@pytest.fixture
def user() -> User:
    return make_user()


# ------------------------------------------------------------------- CORRENTE


async def test_current_month_asks_for_the_month_of_the_clock(user: User) -> None:
    store = FakeBalanceStore({"2026-09": totals(income="1000.00", expense="250.50")})
    service = build_service(store)

    balance = await service.current_month(user)

    assert balance.month.key == "2026-09"
    assert store.last_query == {
        "user_id": user.id,
        "first_day": date(2026, 9, 1),
        "last_day": date(2026, 9, 30),
    }
    assert balance.totals.income == Decimal("1000.00")
    assert balance.totals.expense == Decimal("250.50")
    assert balance.totals.net == Decimal("749.50")


async def test_current_month_follows_the_application_timezone(user: User) -> None:
    """Às 23h de 31 de agosto em São Paulo o mês corrente é agosto, não setembro.

    É o mesmo caso que `test_clock.py` cobre no relógio, verificado aqui no
    degrau em que ele decide o recorte de uma resposta.
    """
    service = build_service(FakeBalanceStore(), clock=clock_at(VIRADA_DO_MES))

    balance = await service.current_month(user)

    assert balance.month.key == "2026-08"


async def test_current_month_without_transactions_is_zero_not_null(user: User) -> None:
    service = build_service(FakeBalanceStore())

    balance = await service.current_month(user)

    assert balance.totals.income == Decimal(0)
    assert balance.totals.expense == Decimal(0)
    assert balance.totals.net == Decimal(0)


# ---------------------------------------------------------------- MÊS A MÊS


async def test_monthly_without_bounds_ends_in_the_current_month(user: User) -> None:
    service = build_service(FakeBalanceStore())

    series = await service.monthly(user)

    assert len(series.months) == DEFAULT_MONTHS
    assert series.months[-1].month.key == "2026-09"
    assert series.months[0].month.key == "2025-10"


async def test_monthly_with_only_the_start_does_not_depend_on_today(user: User) -> None:
    """A janela é ancorada na ponta informada, e não no relógio.

    Se `to_month` ausente significasse "até hoje", a mesma pergunta devolveria
    um número diferente de meses a cada mês que passasse.
    """
    service = build_service(FakeBalanceStore())

    series = await service.monthly(user, first=month_range(2020, 1))

    assert series.months[0].month.key == "2020-01"
    assert series.months[-1].month.key == "2020-12"


async def test_monthly_with_only_the_end_closes_the_window_backwards(user: User) -> None:
    service = build_service(FakeBalanceStore())

    series = await service.monthly(user, last=month_range(2026, 3))

    assert series.months[0].month.key == "2025-04"
    assert series.months[-1].month.key == "2026-03"


async def test_monthly_fills_empty_months_with_zero(user: User) -> None:
    """A série não tem buraco: o mês sem lançamento aparece zerado, não some.

    Omiti-lo obrigaria quem consome a distinguir "não gastei nada" de "o
    servidor não me contou" — a mesma resposta com duas leituras.
    """
    store = FakeBalanceStore(
        {
            "2026-07": totals(income="100.00"),
            "2026-09": totals(expense="40.00"),
        }
    )
    service = build_service(store)

    series = await service.monthly(user, first=month_range(2026, 7), last=month_range(2026, 9))

    assert [month.month.key for month in series.months] == ["2026-07", "2026-08", "2026-09"]
    august = series.months[1]
    assert august.totals.income == Decimal(0)
    assert august.totals.expense == Decimal(0)


async def test_monthly_total_is_the_sum_of_the_series(user: User) -> None:
    store = FakeBalanceStore(
        {
            "2026-07": totals(income="100.00", expense="30.00"),
            "2026-08": totals(income="200.00", expense="70.00"),
            "2026-09": totals(income="50.00", expense="100.00"),
        }
    )
    service = build_service(store)

    series = await service.monthly(user, first=month_range(2026, 7), last=month_range(2026, 9))

    assert series.totals.income == Decimal("350.00")
    assert series.totals.expense == Decimal("200.00")
    assert series.totals.net == Decimal("150.00")
    assert series.totals.income == sum(month.totals.income for month in series.months)


async def test_monthly_asks_the_store_for_the_whole_window_at_once(user: User) -> None:
    """Uma consulta para a série inteira, não uma por mês."""
    store = FakeBalanceStore()
    service = build_service(store)

    await service.monthly(user, first=month_range(2026, 1), last=month_range(2026, 3))

    assert store.last_query == {
        "user_id": user.id,
        "first_day": date(2026, 1, 1),
        "last_day": date(2026, 3, 31),
    }


async def test_monthly_rejects_an_inverted_window(user: User) -> None:
    service = build_service(FakeBalanceStore())

    with pytest.raises(InvalidPeriodError):
        await service.monthly(user, first=month_range(2026, 9), last=month_range(2026, 8))


async def test_monthly_accepts_the_window_at_the_limit(user: User) -> None:
    service = build_service(FakeBalanceStore())
    last = month_range(2026, 12)
    first = month_range(2026 - MAX_MONTHS // 12 + 1, 1)

    series = await service.monthly(user, first=first, last=last)

    assert len(series.months) == MAX_MONTHS


async def test_monthly_rejects_a_window_past_the_limit(user: User) -> None:
    """O teto existe porque a série cresce com o intervalo, inclusive de zeros."""
    service = build_service(FakeBalanceStore())
    last = month_range(2026, 12)
    first = month_range(2026 - MAX_MONTHS // 12, 12)

    with pytest.raises(PeriodTooLongError) as raised:
        await service.monthly(user, first=first, last=last)

    assert str(MAX_MONTHS) in raised.value.message


async def test_monthly_of_a_single_month_is_one_row(user: User) -> None:
    service = build_service(FakeBalanceStore())

    series = await service.monthly(user, first=month_range(2026, 9), last=month_range(2026, 9))

    assert len(series.months) == 1


# ---------------------------------------------------------------- INTERVALO


async def test_in_range_passes_the_dates_through_untouched(user: User) -> None:
    """Intervalo é de datas, não de meses: as pontas chegam ao banco como vieram."""
    store = FakeBalanceStore({"2026-09": totals(income="80.00")})
    service = build_service(store)

    balance = await service.in_range(user, first_day=date(2026, 9, 10), last_day=date(2026, 9, 20))

    assert balance.first_day == date(2026, 9, 10)
    assert balance.last_day == date(2026, 9, 20)
    assert store.last_query == {
        "user_id": user.id,
        "first_day": date(2026, 9, 10),
        "last_day": date(2026, 9, 20),
    }


async def test_in_range_accepts_a_single_day(user: User) -> None:
    service = build_service(FakeBalanceStore())

    balance = await service.in_range(user, first_day=date(2026, 9, 5), last_day=date(2026, 9, 5))

    assert balance.first_day == balance.last_day


async def test_in_range_rejects_an_inverted_period(user: User) -> None:
    service = build_service(FakeBalanceStore())

    with pytest.raises(InvalidPeriodError):
        await service.in_range(user, first_day=date(2026, 9, 30), last_day=date(2026, 9, 1))


async def test_in_range_has_no_month_ceiling(user: User) -> None:
    """Sem teto de propósito: o resultado é uma linha só, do tamanho que for o intervalo."""
    service = build_service(FakeBalanceStore())

    balance = await service.in_range(user, first_day=date(1900, 1, 1), last_day=date(2100, 12, 31))

    assert balance.totals.net == Decimal(0)


# ------------------------------------------------------------------- TOTAIS


def test_net_is_derived_and_can_be_negative() -> None:
    assert totals(income="10.00", expense="25.50").net == Decimal("-15.50")


def test_totals_add_up_exactly_in_decimal() -> None:
    """`NUMERIC`/`Decimal` de ponta a ponta: 0,1 + 0,2 é 0,3 e não 0,30000000000000004."""
    somados = totals(income="0.10") + totals(income="0.20")

    assert somados.income == Decimal("0.30")
