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

from core.clock import (
    Clock,
    month_range,
    months_between,
    parse_month,
    shift_month,
)

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


# ------------------------------------------------- aritmética de meses (saldos)
#
# A série mês a mês é montada sobre estas três funções. O que se cobre aqui são
# as bordas onde somar mês escorrega: a virada do ano, fevereiro e o mês de 31
# dias que não existe no seguinte.


def test_parse_month_reads_the_api_format() -> None:
    september = parse_month("2026-09")

    assert september.first_day == date(2026, 9, 1)
    assert september.last_day == date(2026, 9, 30)
    assert september.key == "2026-09"


def test_parse_month_is_the_inverse_of_key() -> None:
    """Ida e volta sem perda: é o que permite o mês viajar como string na API."""
    for key in ("2024-02", "2026-01", "2026-12", "1999-07"):
        assert parse_month(key).key == key


def test_shift_month_crosses_the_year_forward_and_backward() -> None:
    december = month_range(2026, 12)

    assert shift_month(december, 1).key == "2027-01"
    assert shift_month(december, -12).key == "2025-12"
    assert shift_month(december, 0).key == "2026-12"


def test_shift_month_does_not_slip_on_short_months() -> None:
    """Somar mês não é somar 30 dias: de 31 de janeiro chega-se a fevereiro inteiro."""
    january = month_range(2026, 1)

    february = shift_month(january, 1)

    assert february.first_day == date(2026, 2, 1)
    assert february.last_day == date(2026, 2, 28)


def test_months_between_includes_both_ends() -> None:
    months = months_between(month_range(2026, 11), month_range(2027, 2))

    assert [month.key for month in months] == ["2026-11", "2026-12", "2027-01", "2027-02"]


def test_months_between_a_single_month_is_that_month() -> None:
    months = months_between(month_range(2026, 9), month_range(2026, 9))

    assert [month.key for month in months] == ["2026-09"]


def test_months_between_inverted_ends_is_empty() -> None:
    """Vazio, não erro: quem recebeu o pedido do usuário é que decide se isso é 422."""
    assert months_between(month_range(2026, 9), month_range(2026, 8)) == []
