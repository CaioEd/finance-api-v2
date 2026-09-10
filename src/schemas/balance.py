"""Contrato público do domínio de saldos.

Três decisões de contrato, e as três repetem escolhas já feitas no projeto:

- **Dinheiro sai como string**, sempre com duas casas, pela razão de
  `schemas.transaction`: `10.10` em JSON é um double, e quem consome o
  desserializa como float. Aqui a serialização mora no tipo `Amount` e não num
  `field_serializer` por classe — são nove campos de dinheiro em três schemas,
  e repetir o decorador seria repetir a decisão.
- **`net` é o único que pode ser negativo.** `income` e `expense` são somas de
  `amount`, que o banco garante positivo; o sinal aparece só na diferença.
- **Mês é `YYYY-MM`, não data.** O par `first_day`/`last_day` vai junto para
  que quem monta um gráfico não tenha de reimplementar "quantos dias tem
  fevereiro" só para posicionar a barra.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, PlainSerializer

from models.transaction import AMOUNT_DECIMAL_PLACES


def _as_string(amount: Decimal) -> str:
    return f"{amount:.{AMOUNT_DECIMAL_PLACES}f}"


type Amount = Annotated[Decimal, PlainSerializer(_as_string, return_type=str, when_used="json")]
"""Dinheiro no JSON: string de duas casas.

`when_used="json"` de propósito — em Python o valor continua `Decimal`, e só a
travessia para JSON o formata. Um `model_dump()` que devolvesse string forçaria
qualquer código que fizesse conta com a saída a reconverter.
"""


class BalanceOut(BaseModel):
    """Os três números de um recorte qualquer."""

    income: Amount
    expense: Amount
    net: Amount


class MonthBalanceOut(BalanceOut):
    """O saldo de um mês civil, com o mês que ele descreve."""

    month: str
    """Competência no formato `YYYY-MM`."""

    first_day: date
    last_day: date


class MonthlyBalanceOut(BaseModel):
    """Série mês a mês.

    `months` vem **sem buraco**: mês sem lançamento nenhum aparece zerado, na
    ordem cronológica. `total` é o período inteiro, e é sempre a soma da série
    — não um número apurado por outro caminho que pudesse discordar dela.
    """

    months: list[MonthBalanceOut]
    total: BalanceOut


class RangeBalanceOut(BalanceOut):
    """O saldo de um intervalo de datas arbitrário, que pode não casar com meses."""

    first_day: date
    last_day: date
