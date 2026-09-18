"""Cliente do Banco Central (SGS): os índices que avaliam a renda fixa.

Entra como terceira fonte porque a BRAPI fechou CDI, SELIC e IPCA atrás do plano
pago (`canAccessInflationOrPrimeRate`). A API SGS é pública, não pede token e não
tem limite prático.

    GET https://api.bcb.gov.br/dados/serie/bcdata.sgs.{serie}/dados/ultimos/{n}?formato=json

**Todo índice sai daqui anualizado em porcento**, e é isso que permite a um
cálculo só avaliar as quatro modalidades. As duas famílias de série pedem
tratamentos diferentes, e a diferença não é estética:

- **diária** (CDI, SELIC, em % por dia útil): a última leitura basta, e
  capitalizá-la por 252 dá a taxa ao ano.
- **mensal** (IPCA, poupança, em % ao mês): vale o **acumulado dos últimos 12
  meses**, não a última leitura elevada a 12. Inflação é sazonal, e um mês de
  deflação faria um Tesouro IPCA+ render menos que a poupança — agosto/2026
  fechou em `-0,32%`, que anualizado vira `-3,77% a.a.`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import httpx

from models.investment import RateIndex
from providers.base import IndexRate, get_json, to_decimal

BASE_URL = "https://api.bcb.gov.br/dados/serie"

# Convenção do mercado brasileiro: CDI e SELIC rendem em dia útil.
BUSINESS_DAYS_PER_YEAR = 252

MONTHS_PER_YEAR = 12
MONTHLY_WINDOW = MONTHS_PER_YEAR  # leituras pedidas de uma série mensal

# Código da série no SGS. `PREFIXED` não está aqui: a taxa dele é a contratada.
SERIES: dict[RateIndex, int] = {
    RateIndex.CDI: 12,
    RateIndex.SELIC: 11,
    RateIndex.IPCA: 433,
    RateIndex.SAVINGS: 195,
}

# As que vêm em % por dia útil; as demais vêm em % ao mês.
DAILY_SERIES = frozenset({RateIndex.CDI, RateIndex.SELIC})


@dataclass(frozen=True, slots=True)
class BcbClient:
    """Cliente sem estado e sem credencial: a API SGS é aberta."""

    http: httpx.AsyncClient

    async def latest(self, index: RateIndex) -> IndexRate | None:
        """A taxa do índice ao ano. `None` se a série veio vazia.

        Uma leitura para as séries diárias e doze para as mensais. `PREFIXED` não
        tem série e levanta `KeyError` de propósito: pedir ao Banco Central a taxa
        de um contrato prefixado é erro de programação, não dado que falta.
        """
        daily = index in DAILY_SERIES
        payload = await get_json(
            self.http,
            f"{BASE_URL}/bcdata.sgs.{SERIES[index]}/dados/ultimos/{1 if daily else MONTHLY_WINDOW}",
            params={"formato": "json"},
        )
        if not isinstance(payload, list) or not payload:
            return None
        readings = [reading for reading in map(_reading, payload) if reading is not None]
        if not readings:
            return None
        # A data de referência é a da leitura mais recente, e não a do começo da
        # janela: é ela que responde "de quando é este número".
        reference = readings[-1][0]
        annual = (
            annualize_daily(readings[-1][1])
            if daily
            else accumulate_monthly([value for _, value in readings])
        )
        return IndexRate(reference_date=reference, annual_percent=annual)


def _reading(row: object) -> tuple[date, Decimal] | None:
    if not isinstance(row, dict):
        return None
    reference = _parse_date(row.get("data"))
    value = to_decimal(row.get("valor"))
    return None if reference is None or value is None else (reference, value)


def annualize_daily(value: Decimal) -> Decimal:
    """`0,050788% ao dia útil` vira `~13,6% ao ano`, por `(1 + i)^252 - 1`."""
    return ((Decimal(1) + value / 100) ** BUSINESS_DAYS_PER_YEAR - 1) * 100


def accumulate_monthly(values: list[Decimal]) -> Decimal:
    """O acumulado das leituras mensais, projetado para doze meses.

    `∏(1 + i)` é o acumulado do período; o expoente `12/n` o normaliza quando
    vieram menos de doze meses. **Não** é a última leitura elevada a 12 — ver o
    docstring do módulo.
    """
    compounded = Decimal(1)
    for value in values:
        compounded *= Decimal(1) + value / 100
    return (compounded ** (Decimal(MONTHS_PER_YEAR) / len(values)) - 1) * 100


def _parse_date(value: object) -> date | None:
    """`"17/09/2026"` — o SGS devolve data em formato brasileiro, não ISO."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()  # noqa: DTZ007
    except ValueError:
        return None
