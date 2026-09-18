"""Cliente da BRAPI: ações e fundos imobiliários da B3.

Endpoint de cotação conforme `docs/brapi_api.md`:
`GET /api/v2/stocks/quote?symbols=B3SA3`, com `Authorization: Bearer <token>`,
lendo `results[0].data`.

**Um ativo por requisição.** Não é escolha: o plano gratuito recusa dois ou mais
(`QUOTES_PER_REQUEST_EXCEEDED`), e o teto é 20 por minuto. Por isso `quote` é
singular e não existe `quotes` — uma assinatura plural convidaria a montar um
lote que o provedor recusa.

A busca é `GET /api/quote/list?search=<termo>`. **Não** é `/api/available?search=`,
que existe, responde 200 e devolve lista vazia.

Cripto e os índices (CDI, SELIC, IPCA) são plano pago aqui; vêm da Twelve Data e
do Banco Central. Ver `docs/investimentos.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from providers.base import AssetHit, Quote, get_json, to_decimal

BASE_URL = "https://brapi.dev/api"
CURRENCY = "BRL"

MAX_SYMBOLS_PER_REQUEST = 1  # o limite do plano gratuito

SEARCH_LIMIT = 10


@dataclass(frozen=True, slots=True)
class BrapiClient:
    """Cliente sem estado: a sessão HTTP entra por parâmetro.

    O `AsyncClient` é do processo (mora no `app.state`), para as conexões serem
    reaproveitadas entre rodadas em vez de reabertas a cada ativo.
    """

    http: httpx.AsyncClient
    token: str

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def quote(self, symbol: str) -> Quote | None:
        """A cotação de **um** papel; `None` quando a BRAPI não conhece o símbolo.

        `None` e não exceção: o agendador precisa distinguir símbolo inexistente
        de "o provedor caiu", porque só o segundo vale tentar de novo.
        """
        payload = await get_json(
            self.http,
            f"{BASE_URL}/v2/stocks/quote",
            params={"symbols": symbol},
            headers=self._headers,
        )
        results = payload.get("results") if isinstance(payload, dict) else None
        if not results:
            return None
        data = results[0].get("data") or {}
        price = to_decimal(data.get("regularMarketPrice"))
        if price is None:
            return None
        return Quote(
            symbol=str(results[0].get("symbol") or symbol).upper(),
            price=price,
            currency=str(data.get("currency") or CURRENCY),
            name=data.get("longName") or data.get("shortName"),
            previous_close=to_decimal(data.get("regularMarketPreviousClose")),
            change_percent=to_decimal(data.get("regularMarketChangePercent")),
            quoted_at=_parse_instant(data.get("regularMarketTime")),
            logo_url=data.get("logourl"),
        )

    async def search(self, term: str, *, limit: int = SEARCH_LIMIT) -> list[AssetHit]:
        """Papéis cujo código ou nome casem com o termo.

        O `close` vem de graça nesta resposta e é aproveitado: a tela mostra o
        preço sem uma segunda requisição por linha.
        """
        payload = await get_json(
            self.http,
            f"{BASE_URL}/quote/list",
            params={"search": term, "limit": limit},
            headers=self._headers,
        )
        stocks = payload.get("stocks") if isinstance(payload, dict) else None
        if not stocks:
            return []
        return [hit for hit in (_hit_from(row) for row in stocks[:limit]) if hit is not None]


def _hit_from(row: dict[str, Any]) -> AssetHit | None:
    symbol = row.get("stock")
    if not symbol:
        return None
    return AssetHit(
        symbol=str(symbol).upper(),
        name=str(row.get("name") or symbol),
        currency=CURRENCY,
        exchange=row.get("sector"),
        price=to_decimal(row.get("close")),
        logo_url=row.get("logo"),
    )


def _parse_instant(value: object) -> datetime | None:
    """`"2026-09-18T13:16:30.000Z"` para um `datetime` ciente.

    A coluna é `TIMESTAMPTZ`: um instante ingênuo aqui viraria comparação
    impossível lá na frente, e o ruff (`DTZ`) barra isso no código.
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
