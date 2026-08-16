"""Fonte única de "agora".

Toda data de negócio passa por aqui. `date.today()` espalhado pelo código é o
que torna "mês corrente" impossível de testar e sensível ao fuso do servidor:
o relógio é injetado, então congelá-lo no teste é trivial.

Convenção do projeto (ver documento de arquitetura, §1.7):
  - instantes (`created_at`, `expires_at`) são sempre UTC;
  - datas de competência são `date` puro, resolvidas no fuso da aplicação.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class MonthRange:
    """Um mês civil como intervalo fechado de datas."""

    first_day: date
    last_day: date

    @property
    def key(self) -> str:
        """Identificador do mês na API: `YYYY-MM`."""
        return self.first_day.strftime("%Y-%m")


def month_range(year: int, month: int) -> MonthRange:
    last = calendar.monthrange(year, month)[1]
    return MonthRange(first_day=date(year, month, 1), last_day=date(year, month, last))


@dataclass(frozen=True, slots=True)
class Clock:
    """Relógio da aplicação, atrelado a um fuso."""

    tz: ZoneInfo

    def now_utc(self) -> datetime:
        """Instante atual, sempre com timezone e sempre em UTC."""
        return datetime.now(UTC)

    def today(self) -> date:
        """Data de hoje no fuso da aplicação."""
        return datetime.now(self.tz).date()

    def current_month(self) -> MonthRange:
        today = self.today()
        return month_range(today.year, today.month)
