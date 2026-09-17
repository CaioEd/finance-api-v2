"""Calendário das recorrências: funções puras sobre datas.

Fora dos serviços porque os dois (lançamentos e recorrências) o usam. Cada data
sai do dia da regra, nunca da anterior — senão "todo dia 31" ficaria preso no 28
depois de fevereiro. "Hoje" é parâmetro; quem chama resolve pelo `Clock`.
"""

from __future__ import annotations

from datetime import date

from core.clock import MonthRange, month_range, shift_month
from models.recurring_transaction import RecurringTransaction
from models.transaction import Transaction


def month_of(day: date) -> MonthRange:
    return month_range(day.year, day.month)


def occurrence_in(month: MonthRange, day_of_month: int) -> date:
    """O dia pedido naquele mês, ou o último dia se o mês for mais curto."""
    return month.first_day.replace(day=min(day_of_month, month.last_day.day))


def first_occurrence_from(start: date, day_of_month: int) -> date:
    """A primeira ocorrência em `start` ou depois."""
    month = month_of(start)
    candidate = occurrence_in(month, day_of_month)
    if candidate >= start:
        return candidate
    return occurrence_in(shift_month(month, 1), day_of_month)


def following_occurrence(occurrence: date, day_of_month: int) -> date:
    return occurrence_in(shift_month(month_of(occurrence), 1), day_of_month)


def register_due(rule: RecurringTransaction, today: date) -> list[Transaction]:
    """Lançamentos que a regra deve até `today`, já avançando `next_occurrence_on`.

    Não grava: quem chama comita as linhas junto com a data avançada, senão o
    mês pode entrar duas vezes. Vários meses vencidos geram um lançamento cada;
    regra pausada não deve nada.
    """
    due: list[Transaction] = []
    if not rule.is_active:
        return due
    while rule.next_occurrence_on <= today:
        due.append(
            Transaction(
                user_id=rule.user_id,
                category_id=rule.category_id,
                category=rule.category,
                amount=rule.amount,
                occurred_on=rule.next_occurrence_on,
                description=rule.description,
                recurring_transaction_id=rule.id,
            )
        )
        rule.next_occurrence_on = following_occurrence(rule.next_occurrence_on, rule.day_of_month)
    return due
