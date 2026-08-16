"""O relógio existe para que "hoje" seja testável — então ele é testado.

O caso que importa é a virada do mês: às 23h30 do dia 31 em São Paulo já é dia
1 do mês seguinte em UTC. Um `date.today()` no servidor faria o "mês corrente"
virar três horas cedo demais.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import time_machine

from finance_api.core.clock import Clock, month_range

SAO_PAULO = ZoneInfo("America/Sao_Paulo")


@time_machine.travel(datetime(2026, 9, 1, 2, 30, tzinfo=UTC), tick=False)
def test_today_uses_the_application_timezone_not_utc() -> None:
    assert datetime.now(UTC).date() == date(2026, 9, 1)

    assert Clock(tz=SAO_PAULO).today() == date(2026, 8, 31)


@time_machine.travel(datetime(2026, 9, 1, 2, 30, tzinfo=UTC), tick=False)
def test_current_month_follows_the_local_date() -> None:
    current = Clock(tz=SAO_PAULO).current_month()

    assert current.key == "2026-08"
    assert current.first_day == date(2026, 8, 1)
    assert current.last_day == date(2026, 8, 31)


@time_machine.travel(datetime(2026, 9, 1, 2, 30, tzinfo=UTC), tick=False)
def test_now_utc_is_always_aware_and_in_utc() -> None:
    now = Clock(tz=SAO_PAULO).now_utc()

    assert now.tzinfo is UTC
    assert now == datetime(2026, 9, 1, 2, 30, tzinfo=UTC)


def test_month_range_handles_february_in_a_leap_year() -> None:
    february = month_range(2028, 2)

    assert february.first_day == date(2028, 2, 1)
    assert february.last_day == date(2028, 2, 29)
    assert february.key == "2028-02"


def test_month_range_handles_december() -> None:
    december = month_range(2026, 12)

    assert december.last_day == date(2026, 12, 31)
