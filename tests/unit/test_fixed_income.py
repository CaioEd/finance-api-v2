"""O rendimento da renda fixa: aritmética pura, sem banco e sem provedor.

Irmão de `test_recurrence.py`. O que se cobre aqui é a conta — as quatro
modalidades reduzidas a uma taxa efetiva, e a capitalização por dia corrido.
Quem aplica isso sobre as posições é `jobs.investment_quotes`, testado à parte.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from models.investment import RateIndex
from services.fixed_income import accrue, days_between, effective_annual_percent

CDI_ANNUAL = Decimal("13.65")
IPCA_ANNUAL = Decimal("4.00")


# --------------------------------------------------------- a taxa efetiva


def test_a_percentage_of_the_index_multiplies_it() -> None:
    """102% do CDI a 13,65% ao ano dá 13,923% — é assim que o produto é vendido."""
    effective = effective_annual_percent(RateIndex.CDI, Decimal("102"), CDI_ANNUAL)

    assert effective == Decimal("13.9230")


def test_selic_reads_the_rate_the_same_way_as_cdi() -> None:
    assert effective_annual_percent(
        RateIndex.SELIC, Decimal("100"), CDI_ANNUAL
    ) == effective_annual_percent(RateIndex.CDI, Decimal("100"), CDI_ANNUAL)


def test_ipca_compounds_the_spread_instead_of_adding_it() -> None:
    """ "IPCA + 5,8%" com IPCA de 4% rende 10,032% ao ano, não 9,8%.

    A diferença é o termo cruzado, e ele não é arredondamento: some ao longo de
    um Tesouro de cinco anos e vira dinheiro de verdade.
    """
    effective = effective_annual_percent(RateIndex.IPCA, Decimal("5.8"), IPCA_ANNUAL)

    assert effective == Decimal("10.0320")
    assert effective > IPCA_ANNUAL + Decimal("5.8")


def test_savings_uses_the_central_bank_rule_whole() -> None:
    """Poupança não tem percentual a aplicar: a regra é a do Banco Central."""
    assert effective_annual_percent(RateIndex.SAVINGS, Decimal("100"), Decimal("8.34")) == Decimal(
        "8.34"
    )


def test_a_prefixed_contract_does_not_depend_on_any_index() -> None:
    """Sem índice, e por isso nunca `None`: a taxa é a que foi contratada."""
    assert effective_annual_percent(RateIndex.PREFIXED, Decimal("11.45"), None) == Decimal("11.45")


@pytest.mark.parametrize(
    "index", [RateIndex.CDI, RateIndex.SELIC, RateIndex.IPCA, RateIndex.SAVINGS]
)
def test_an_unknown_index_yields_no_rate_rather_than_zero(index: RateIndex) -> None:
    """Sem a leitura do Banco Central não há o que capitalizar.

    Zero afirmaria que o dinheiro parou de render, que é uma coisa diferente de
    não saber quanto ele rendeu.
    """
    assert effective_annual_percent(index, Decimal("100"), None) is None


# --------------------------------------------------------- a capitalização


def test_a_year_at_ten_percent_grows_by_ten_percent() -> None:
    assert accrue(Decimal("1000.00"), Decimal("10"), 365) == Decimal("1100.00")


def test_half_a_year_grows_by_less_than_half_the_rate() -> None:
    """Capitalização composta: a metade do tempo não rende a metade do juro."""
    half = accrue(Decimal("1000.00"), Decimal("10"), 182)

    assert Decimal("1048.00") < half < Decimal("1049.00")


def test_two_periods_compound_into_the_same_as_one_long_one() -> None:
    """É o que torna a rodada de 15 min segura: acruar por partes dá o mesmo total.

    Sem isto, o valor da carteira dependeria de quantas vezes o agendador rodou
    — e um restart no meio do dia mudaria o patrimônio de quem estivesse olhando.
    """
    direto = accrue(Decimal("10000.00"), Decimal("12"), 60)
    em_duas = accrue(accrue(Decimal("10000.00"), Decimal("12"), 30), Decimal("12"), 30)

    assert abs(direto - em_duas) <= Decimal("0.01")


def test_a_run_inside_the_same_day_does_not_move_the_value() -> None:
    """O agendador roda 96 vezes por dia; 95 delas não têm juro a lançar."""
    assert accrue(Decimal("5000.00"), Decimal("10"), 0) == Decimal("5000.00")


def test_a_date_in_the_future_never_discounts_interest() -> None:
    """Relógio torto ou aplicação lançada para amanhã não podem tirar dinheiro de ninguém."""
    assert accrue(Decimal("5000.00"), Decimal("10"), -5) == Decimal("5000.00")


def test_the_result_lands_on_the_cent() -> None:
    """A coluna tem duas casas; meio para cima, e não o arredondamento do banqueiro."""
    accrued = accrue(Decimal("1000.00"), Decimal("13.65"), 37)

    assert accrued.as_tuple().exponent == -2


def test_a_rate_of_zero_leaves_the_money_where_it_is() -> None:
    assert accrue(Decimal("777.77"), Decimal("0"), 900) == Decimal("777.77")


def test_days_are_calendar_days_between_the_two_dates() -> None:
    """Dia corrido, não dia útil: os 252 dias úteis já foram para a anualização."""
    assert days_between(date(2026, 1, 1), date(2026, 1, 31)) == 30
    assert days_between(date(2026, 3, 1), date(2026, 3, 1)) == 0
    assert days_between(date(2026, 3, 2), date(2026, 3, 1)) == -1
