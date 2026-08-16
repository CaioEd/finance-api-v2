"""O relógio existe para que "hoje" seja testável — então ele é testado.

Cada teste constrói um `Clock` com um instante fixo: nenhum estado global,
nenhuma biblioteca de viagem no tempo, dá para ler de cima a baixo.

O caso que importa é a virada do mês. Às 23h30 do dia 31 em São Paulo já é
dia 1 do mês seguinte em UTC; um relógio que lesse a data em UTC viraria o
"mês corrente" três horas cedo demais, e todo saldo mensal sairia errado
nessas três horas.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from core.clock import Clock, month_range

SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def clock_at(moment_utc: datetime) -> Clock:
    """Relógio de São Paulo parado num instante UTC conhecido."""
    return Clock(tz=SAO_PAULO, instant=lambda: moment_utc)


# 1 de setembro, 02h30 UTC == 31 de agosto, 23h30 em São Paulo (UTC-3).
VIRADA_DO_MES = datetime(2026, 9, 1, 2, 30, tzinfo=UTC)


def test_today_uses_the_application_timezone_not_utc() -> None:
    assert VIRADA_DO_MES.date() == date(2026, 9, 1)

    assert clock_at(VIRADA_DO_MES).today() == date(2026, 8, 31)


def test_current_month_follows_the_local_date() -> None:
    current = clock_at(VIRADA_DO_MES).current_month()

    assert current.key == "2026-08"
    assert current.first_day == date(2026, 8, 1)
    assert current.last_day == date(2026, 8, 31)


def test_now_utc_is_always_aware_and_in_utc() -> None:
    now = clock_at(VIRADA_DO_MES).now_utc()

    assert now == VIRADA_DO_MES
    assert now.tzinfo is UTC


def test_midday_is_the_same_date_in_both_zones() -> None:
    midday = datetime(2026, 8, 16, 15, 0, tzinfo=UTC)

    assert clock_at(midday).today() == date(2026, 8, 16)


def test_default_clock_reads_the_system_time() -> None:
    """Sem `instant`, o relógio de produção anda sozinho."""
    now = Clock(tz=SAO_PAULO).now_utc()

    assert now.tzinfo is UTC


def test_month_range_handles_february_in_a_leap_year() -> None:
    february = month_range(2028, 2)

    assert february.first_day == date(2028, 2, 1)
    assert february.last_day == date(2028, 2, 29)
    assert february.key == "2028-02"


def test_month_range_handles_december() -> None:
    december = month_range(2026, 12)

    assert december.last_day == date(2026, 12, 31)
