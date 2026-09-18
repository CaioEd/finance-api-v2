"""O que todo provedor devolve, e como um erro dele vira erro deste sistema.

Os três tipos de saída (`Quote`, `AssetHit`, `IndexRate`) são deste pacote, não
do provedor: é o que permite ao agendador tratar BRAPI e Twelve Data pelo mesmo
laço, e ao domínio não saber que nenhum dos dois existe.

**Falha de provedor nunca é 500.** Ver `MarketDataUnavailableError`, que a borda
traduz em 503 e o agendador só registra no log para tentar de novo depois.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from core.errors import ServiceUnavailableError

logger = logging.getLogger(__name__)

# Curto de propósito: o agendador prefere pular um ativo a segurar a rodada
# inteira esperando um provedor que não responde.
DEFAULT_TIMEOUT_SECONDS = 10.0


class MarketDataUnavailableError(ServiceUnavailableError):
    code = "market_data_unavailable"
    message = "Não foi possível consultar o provedor de cotações agora."


@dataclass(frozen=True, slots=True)
class Quote:
    """O preço de um ativo, na moeda dele."""

    symbol: str
    price: Decimal
    currency: str
    name: str | None = None
    previous_close: Decimal | None = None
    change_percent: Decimal | None = None
    quoted_at: datetime | None = None
    logo_url: str | None = None


@dataclass(frozen=True, slots=True)
class AssetHit:
    """Um ativo achado na busca. Ainda não é posição nem linha de catálogo."""

    symbol: str
    name: str
    currency: str
    exchange: str | None = None
    price: Decimal | None = None
    logo_url: str | None = None


@dataclass(frozen=True, slots=True)
class IndexRate:
    """Uma taxa de índice, já **anualizada em porcento**.

    Anualizar no cliente é o que deixa as quatro séries do Banco Central — duas
    diárias, duas mensais — chegarem ao mesmo cálculo.
    """

    reference_date: date
    annual_percent: Decimal


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """`GET` que devolve JSON, ou levanta `MarketDataUnavailableError`.

    Uma porta só para todas as falhas de fora — timeout, DNS, 5xx, 4xx, corpo que
    não é JSON. O motivo real vai para o log.
    """
    try:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        payload: Any = response.json()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "provedor respondeu %s em %s: %s",
            exc.response.status_code,
            url,
            exc.response.text[:500],
        )
        raise MarketDataUnavailableError() from exc
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("falha ao consultar %s: %s", url, exc)
        raise MarketDataUnavailableError() from exc
    return payload


def to_decimal(value: object) -> Decimal | None:
    """Número do provedor para `Decimal`, ou `None` se não for número.

    Passa por `str` de propósito: a BRAPI manda float do JSON, e `Decimal(float)`
    arrastaria o erro de representação do double para dentro do `NUMERIC`.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None
