"""Cliente do Banco Central (SGS): os índices que avaliam a renda fixa.

Entra como terceira fonte porque a BRAPI fechou CDI, SELIC e IPCA atrás do plano
pago (`canAccessInflationOrPrimeRate`). A API SGS é pública, não pede token e
não tem limite prático — o custo de não a usar seria pedir ao usuário que
digitasse o CDI à mão, e essa taxa envelheceria em silêncio.

    GET https://api.bcb.gov.br/dados/serie/bcdata.sgs.{serie}/dados/ultimos/1?formato=json

**Todo índice sai daqui anualizado em porcento**, e é isso que permite a um
cálculo só avaliar as quatro modalidades. Duas séries são diárias (CDI e SELIC,
em % por dia útil) e duas são mensais (IPCA e poupança); a conversão está logo
abaixo do número que ela converte, e não espalhada em quem acrua.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx

from models.investment import RateIndex
from providers.base import IndexRate, get_json, to_decimal

BASE_URL = "https://api.bcb.gov.br/dados/serie"

BUSINESS_DAYS_PER_YEAR = 252
"""Convenção do mercado brasileiro para série diária: CDI e SELIC rendem em dia útil."""

MONTHS_PER_YEAR = 12

SERIES: dict[RateIndex, int] = {
    RateIndex.CDI: 12,
    RateIndex.SELIC: 11,
    RateIndex.IPCA: 433,
    RateIndex.SAVINGS: 195,
}
"""Código da série no SGS. `PREFIXED` não está aqui: a taxa dele é a contratada,
e não há índice a consultar."""

DAILY_SERIES = frozenset({RateIndex.CDI, RateIndex.SELIC})
"""As que vêm em % por dia útil; as demais vêm em % ao mês."""


@dataclass(frozen=True, slots=True)
class BcbClient:
    """Cliente sem estado e sem credencial: a API SGS é aberta."""

    http: httpx.AsyncClient

    async def latest(self, index: RateIndex) -> IndexRate | None:
        """A última leitura da série, já anualizada. `None` se a série veio vazia.

        `PREFIXED` não tem série e levanta `KeyError` de propósito: pedir ao
        Banco Central a taxa de um contrato prefixado é um erro de programação,
        não um dado que falta.
        """
        payload = await get_json(
            self.http,
            f"{BASE_URL}/bcdata.sgs.{SERIES[index]}/dados/ultimos/1",
            params={"formato": "json"},
        )
        if not isinstance(payload, list) or not payload:
            return None
        return _rate_from(payload[0], index)


def _rate_from(row: dict[str, Any], index: RateIndex) -> IndexRate | None:
    reference = _parse_date(row.get("data"))
    value = to_decimal(row.get("valor"))
    if reference is None or value is None:
        return None
    return IndexRate(reference_date=reference, annual_percent=annualize(value, index))


def annualize(value: Decimal, index: RateIndex) -> Decimal:
    """A taxa do período da série, capitalizada até virar taxa ao ano.

    `0,050788% ao dia útil` vira `~13,6% ao ano` por `(1 + i)^252 - 1`, e
    `0,62% ao mês` vira `~7,7% ao ano` por `(1 + i)^12 - 1`. Somar a taxa diária
    252 vezes daria um número maior e errado — juro compõe.
    """
    periods = BUSINESS_DAYS_PER_YEAR if index in DAILY_SERIES else MONTHS_PER_YEAR
    factor = (Decimal(1) + value / 100) ** periods
    return (factor - 1) * 100


def _parse_date(value: object) -> date | None:
    """`"17/09/2026"` — o SGS devolve data em formato brasileiro, não ISO."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()  # noqa: DTZ007
    except ValueError:
        return None
