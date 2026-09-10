"""Fonte única de "agora".

Toda data de negócio passa por aqui. `date.today()` espalhado pelo código é o
que torna "mês corrente" impossível de testar e sensível ao fuso do servidor.

O instante é um parâmetro do relógio (`instant`), não uma leitura global do
sistema: em produção vale o default, que lê o relógio da máquina; em teste
passa-se uma função que devolve um instante fixo. Nenhuma mágica de
congelamento de tempo é necessária.

Convenção do projeto (ver documento de arquitetura, §1.7):
  - instantes (`created_at`, `expires_at`) são sempre UTC;
  - datas de competência são `date` puro, resolvidas no fuso da aplicação.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable
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


def system_utc_now() -> datetime:
    """Instante atual do sistema, com timezone, em UTC."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Clock:
    """Relógio da aplicação, atrelado a um fuso.

    `instant` devolve o momento presente em UTC; trocá-lo é como o teste fixa
    o tempo.
    """

    tz: ZoneInfo
    instant: Callable[[], datetime] = system_utc_now

    def now_utc(self) -> datetime:
        return self.instant()

    def today(self) -> date:
        """Data de hoje no fuso da aplicação — que pode não ser a data em UTC."""
        return self.now_utc().astimezone(self.tz).date()

    def current_month(self) -> MonthRange:
        today = self.today()
        return month_range(today.year, today.month)


MONTH_KEY_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
"""A forma de um mês na API: `YYYY-MM`.

Mora aqui, junto de `MonthRange.key`, porque quem escreve o formato e quem o lê
têm de concordar. A borda usa isto como `pattern` do parâmetro de consulta, e é
o que faz `"2026-13"` sair como 422 com o campo apontado em vez de chegar
inteiro até `parse_month`.
"""


def parse_month(key: str) -> MonthRange:
    """`"2026-09"` → o mês civil correspondente.

    A validação de forma é da borda (`MONTH_KEY_PATTERN`); o `ValueError` que
    `int` e `month_range` levantam cobre só o que escapar dela.
    """
    year, _, month = key.partition("-")
    return month_range(int(year), int(month))


def shift_month(month: MonthRange, offset: int) -> MonthRange:
    """O mês `offset` meses adiante — ou atrás, se negativo.

    Contado sobre um índice absoluto de meses, e não somando 30 dias: o
    aritmético não escorrega em fevereiro nem na virada do ano.
    """
    index = month.first_day.year * 12 + month.first_day.month - 1 + offset
    return month_range(index // 12, index % 12 + 1)


def months_between(first: MonthRange, last: MonthRange) -> list[MonthRange]:
    """Todos os meses de `first` a `last`, inclusive nas duas pontas.

    Devolve a série **sem buraco**: é o que permite ao saldo mês a mês
    apresentar o mês sem lançamento nenhum como zero, em vez de omiti-lo e
    deixar quem consome adivinhar se faltou dado ou faltou gasto.

    `first` posterior a `last` devolve lista vazia; quem recebe o pedido do
    usuário é que decide se isso é um erro (e o serviço de saldos decide que é).
    """
    months: list[MonthRange] = []
    current = first
    while current.first_day <= last.first_day:
        months.append(current)
        current = shift_month(current, 1)
    return months
