"""O rendimento da renda fixa: função pura, sem banco e sem provedor.

Irmão de `services/recurrence.py`, e pela mesma razão: aqui está a aritmética
que decide quanto um CDB vale hoje, separada de quem a aplica. Testá-la não
exige sessão, relógio de sistema nem rede.

## É estimativa, e está escrito

O número que sai daqui **não é o extrato do banco**: usa a última leitura do
índice projetada para o ano inteiro, capitaliza por dia corrido e não conhece
feriado, imposto de renda nem carência. Para uma tela de patrimônio basta; para
conferir resgate, não.

## Uma taxa efetiva, quatro modalidades

Cada índice contrata de um jeito, e o primeiro passo é reduzir os quatro à taxa
anual efetiva daquela posição.

| Índice | Contrato | Taxa efetiva |
|---|---|---|
| CDI, SELIC | `rate_percent`% do índice | `índice x rate_percent / 100` |
| IPCA | índice **mais** `rate_percent` ao ano | `(1+índice)(1+spread) - 1` |
| Poupança | a regra do Banco Central | o índice |
| Prefixado | taxa fixa contratada | `rate_percent` |

O IPCA compõe em vez de somar porque é assim que "IPCA + 5,8%" rende: inflação
de 4% com spread de 5,8% dá 10,03% ao ano, não 9,8%.

## O acrual anda em dia cheio

`accrue` recebe **dias corridos** e nunca uma fração deles: capitalizar 15
minutos de juro sobre uma coluna de duas casas somaria zero toda vez (`R$ 5.000`
a 10% ao ano rende `R$ 0,014` no período). A primeira rodada de cada dia
capitaliza o dia inteiro; as demais não mexem no valor.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from models.investment import RateIndex

# Dia corrido, não dia útil: o expoente já foi para `providers.bcb.annualize`, e
# misturar as duas bases aqui contaria os 252 dias úteis duas vezes.
DAYS_PER_YEAR = Decimal(365)

CENTS = Decimal("0.01")

# Os índices em que `rate_percent` é percentual do índice — ver `RateIndex`.
PERCENT_OF_INDEX = frozenset({RateIndex.CDI, RateIndex.SELIC})


def effective_annual_percent(
    index: RateIndex,
    rate_percent: Decimal,
    index_annual_percent: Decimal | None,
) -> Decimal | None:
    """A taxa anual efetiva da posição, em porcento.

    `None` quando o índice é preciso e não se sabe qual é ele: chutar zero
    afirmaria que o dinheiro parou de render. O prefixado nunca devolve `None`.
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

    `days` não positivo devolve o próprio valor: rodada no mesmo dia não rende, e
    data no futuro não pode descontar juro de ninguém.
    """
    if days <= 0:
        return value
    factor = (1 + annual_percent / 100) ** (Decimal(days) / DAYS_PER_YEAR)
    # Meio para cima, explícito: o default do `Decimal` é banqueiro, e dinheiro
    # arredondado assim não bate com o extrato de ninguém.
    return (value * factor).quantize(CENTS, rounding=ROUND_HALF_UP)


def days_between(start: date, end: date) -> int:
    return (end - start).days
