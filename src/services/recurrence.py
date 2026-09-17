"""O calendário das recorrências: quando cada ocorrência cai, e o que já venceu.

Funções puras sobre datas, mais `register_due`, que aplica o calendário a uma
regra. Moram fora dos dois serviços porque os dois precisam delas — o de
recorrências, no CRUD e no agendador; o de lançamentos, quando um lançamento
nasce já pedindo para se repetir — e nenhum dos dois deve importar o outro para
isso.

Três regras, e só elas:

1. **Uma ocorrência por mês, no dia pedido — ou no último dia, se o mês for mais
   curto.** Cada data é calculada do dia da regra e do mês, nunca da data
   anterior: "todo dia 31" cai em 28 de fevereiro e volta a ser 31 em março.
   Somar um mês à ocorrência anterior prenderia a regra no dia 28 para sempre.
2. **O que venceu é registrado inteiro.** Um agendador fora do ar por três meses
   deve três lançamentos, cada um com a data dele — não um só, e não nenhum.
3. **"Hoje" é parâmetro.** Quem chama resolve pelo `Clock`; aqui não existe
   relógio.
"""

from __future__ import annotations

from datetime import date

from core.clock import MonthRange, month_range, shift_month
from models.recurring_transaction import RecurringTransaction
from models.transaction import Transaction


def month_of(day: date) -> MonthRange:
    return month_range(day.year, day.month)


def occurrence_in(month: MonthRange, day_of_month: int) -> date:
    """A ocorrência daquele mês: o dia pedido, ou o último do mês se ele não existir."""
    return month.first_day.replace(day=min(day_of_month, month.last_day.day))


def first_occurrence_from(start: date, day_of_month: int) -> date:
    """A primeira ocorrência em `start` ou depois — no próprio mês, se ainda der."""
    month = month_of(start)
    candidate = occurrence_in(month, day_of_month)
    if candidate >= start:
        return candidate
    return occurrence_in(shift_month(month, 1), day_of_month)


def following_occurrence(occurrence: date, day_of_month: int) -> date:
    """A ocorrência do mês seguinte ao de `occurrence`."""
    return occurrence_in(shift_month(month_of(occurrence), 1), day_of_month)


def register_due(rule: RecurringTransaction, today: date) -> list[Transaction]:
    """Os lançamentos que a regra deve até `today`, com `next_occurrence_on` já avançada.

    Não grava nada: devolve as linhas para quem tem a unidade de trabalho, que
    as acrescenta e comita **junto** com a data avançada. Separar as duas
    escritas é como um mês acaba registrado duas vezes — o INSERT entra, o
    avanço da data não, e a próxima rodada encontra a mesma ocorrência vencida.

    Regra pausada não deve nada: o que venceu durante a pausa não volta.
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
