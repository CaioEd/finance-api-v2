"""O rendimento da renda fixa: função pura, sem banco e sem provedor.

Irmão de `services/recurrence.py`, e pela mesma razão: aqui está a aritmética
que decide quanto um CDB vale hoje, separada de quem a aplica. Testá-la não
exige sessão, relógio de sistema nem rede.

## É estimativa, e está escrito

O número que sai daqui **não é o extrato do banco**. Ele usa a última leitura do
índice projetada para o ano inteiro, capitaliza por dia corrido e não conhece
feriado, imposto de renda nem carência. Para uma tela de patrimônio isso é o
suficiente e é honesto; para conferir resgate, não é — e a alternativa seria
embutir o calendário da ANBIMA e a tabela regressiva do IR num app de finanças
pessoais.

## Uma taxa efetiva, quatro modalidades

Cada índice contrata de um jeito, e o primeiro passo é reduzir os quatro a um
número só: a taxa anual efetiva daquela posição.

| Índice | Contrato | Taxa efetiva |
|---|---|---|
| CDI, SELIC | `rate_percent`% do índice | `índice x rate_percent / 100` |
| IPCA | índice **mais** `rate_percent` ao ano | `(1+índice)(1+spread) - 1` |
| Poupança | a regra do Banco Central | o índice |
| Prefixado | taxa fixa contratada | `rate_percent` |

O IPCA compõe em vez de somar porque é assim que "IPCA + 5,8%" rende: inflação
de 4% com spread de 5,8% dá 10,03% ao ano, não 9,8%.

## O acrual anda em dia cheio

`accrue` recebe **dias corridos** e nunca uma fração deles. O agendador roda a
cada 15 minutos, e capitalizar 15 minutos de juro sobre uma coluna de duas casas
somaria zero toda vez: `R$ 5.000` a 10% ao ano rende `R$ 0,014` no período, que
arredonda para nada. Rodadas dentro do mesmo dia não mexem no valor, e a
primeira rodada de cada dia capitaliza o dia inteiro — que é como uma conta
remunerada funciona de verdade.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from models.investment import RateIndex

DAYS_PER_YEAR = Decimal(365)
"""Dia corrido, não dia útil: o expoente já foi para `providers.bcb.annualize`,
que converte a série do SGS em taxa ao ano. Misturar as duas bases aqui contaria
os 252 dias úteis duas vezes."""

CENTS = Decimal("0.01")

PERCENT_OF_INDEX = frozenset({RateIndex.CDI, RateIndex.SELIC})
"""Os índices em que `rate_percent` é percentual do índice — ver `RateIndex`."""


def effective_annual_percent(
    index: RateIndex,
    rate_percent: Decimal,
    index_annual_percent: Decimal | None,
) -> Decimal | None:
    """A taxa anual efetiva da posição, em porcento.

    `None` quando o índice é preciso e não se sabe qual é ele: sem a leitura do
    Banco Central não há o que capitalizar, e chutar zero afirmaria que o
    dinheiro parou de render. O prefixado nunca devolve `None` — a taxa dele é
    a contratada, e não depende de ninguém.
    """
    if index is RateIndex.PREFIXED:
        return rate_percent
    if index_annual_percent is None:
        return None
    if index in PERCENT_OF_INDEX:
        return index_annual_percent * rate_percent / 100
    if index is RateIndex.IPCA:
        # Composto, não somado: "IPCA + 5,8%" com IPCA de 4% dá 10,03% ao ano.
        compounded = (1 + index_annual_percent / 100) * (1 + rate_percent / 100)
        return (compounded - 1) * 100
    # Poupança: a regra do Banco Central inteira, sem percentual a aplicar.
    return index_annual_percent


def accrue(value: Decimal, annual_percent: Decimal, days: int) -> Decimal:
    """`value` capitalizado por `days` dias corridos, arredondado ao centavo.

    Devolve o próprio valor quando `days` não é positivo: rodada dentro do mesmo
    dia não rende, e data no futuro (relógio torto, aplicação lançada para
    amanhã) não pode descontar juro de ninguém.
    """
    if days <= 0:
        return value
    factor = (1 + annual_percent / 100) ** (Decimal(days) / DAYS_PER_YEAR)
    # Meio para cima, explícito: o default do `Decimal` é banqueiro, e dinheiro
    # arredondado assim não bate com o extrato de ninguém.
    return (value * factor).quantize(CENTS, rounding=ROUND_HALF_UP)


def days_between(start: date, end: date) -> int:
    """Dias corridos de `start` até `end`; negativo vira zero em `accrue`."""
    return (end - start).days
