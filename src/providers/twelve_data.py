"""Cliente da Twelve Data: ações americanas, criptomoedas e o câmbio USD/BRL.

Três endpoints:

- `GET /price?symbol=A,B,C` — lote. **Um crédito por símbolo**, e o plano
  gratuito dá 8 por minuto e 800 por dia. O lote que estoura o limite **gasta
  os créditos e não devolve nada**, então `MAX_SYMBOLS_PER_REQUEST` não é
  sugestão: passar dele é pagar por um 429.
- `GET /symbol_search?symbol=<termo>` — busca. Não consome crédito, e é de onde
  sai o nome do ativo: quem escolhe a linha na busca já manda o nome no cadastro,
  então o agendador não precisa gastar um crédito de `/quote` para descobri-lo.

**Cripto é cotada em dólar**, e não em real, mesmo quando o par BRL existiria:
`BTC/BRL` responde, mas `DOT/BRL`, `USDC/BRL` e `DOGE/BRL` não. Um caminho só
para as nove moedas é melhor que sete em dólar e duas em real, e a conversão
sai de `usd_brl()` — um crédito por rodada, não um por moeda.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from providers.base import AssetHit, get_json, to_decimal

BASE_URL = "https://api.twelvedata.com"

MAX_SYMBOLS_PER_REQUEST = 8
"""O teto de créditos por minuto do plano gratuito. Ver o docstring do módulo."""

SEARCH_LIMIT = 10

USD = "USD"
BRL = "BRL"
USD_BRL = "USD/BRL"

CRYPTO_QUOTE_CURRENCY = USD
"""Os pares em dólar existem para todas as nove moedas; os em real, não."""


CRYPTOCURRENCIES: dict[str, str] = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "SOL": "Solana",
    "DOT": "Polkadot",
    "LTC": "Litecoin",
    "USDC": "USD Coin",
    "DOGE": "Dogecoin",
    "USDT": "Tether",
    "ADA": "Cardano",
}
"""As criptomoedas que o produto oferece, pela abreviação com que são cotadas.

Lista fechada, e não busca livre: são nove das dez do escopo, todas conferidas
contra o provedor. Uma busca aberta devolveria milhares de pares, a maioria sem
liquidez, e cada um deles viraria uma linha de catálogo a cotar toda rodada —
dentro de um orçamento de oito créditos por minuto.
"""


def crypto_pair(symbol: str) -> str:
    """`BTC` para `BTC/USD` — o símbolo de consulta, que não é o de exibição.

    A tela mostra `BTC`, e é `BTC` que está no catálogo. Guardar o par cru
    prenderia o dado ao provedor da vez: outro cotaria `BTCUSDT`, e migrar
    passaria a exigir reescrever linha de banco.
    """
    return f"{symbol.upper()}/{CRYPTO_QUOTE_CURRENCY}"


@dataclass(frozen=True, slots=True)
class TwelveDataClient:
    """Cliente sem estado: a sessão HTTP entra por parâmetro."""

    http: httpx.AsyncClient
    api_key: str

    async def prices(self, symbols: list[str]) -> dict[str, Decimal]:
        """Preço de até `MAX_SYMBOLS_PER_REQUEST` símbolos, numa requisição.

        Símbolo que o provedor não conhece simplesmente **não aparece** no
        resultado — a Twelve Data devolve um objeto de erro no lugar do preço
        daquela chave, e nunca falha o lote inteiro por causa de um. Quem chama
        trata a ausência; recusar tudo por um símbolo ruim desperdiçaria os
        créditos dos outros sete.
        """
        if not symbols:
            return {}
        if len(symbols) > MAX_SYMBOLS_PER_REQUEST:
            raise ValueError(
                f"a Twelve Data cobra um crédito por símbolo e o plano dá "
                f"{MAX_SYMBOLS_PER_REQUEST} por minuto; recebidos {len(symbols)}"
            )
        payload = await get_json(
            self.http,
            f"{BASE_URL}/price",
            params={"symbol": ",".join(symbols), "apikey": self.api_key},
        )
        if not isinstance(payload, dict):
            return {}
        # Um símbolo só: a resposta é o preço direto, sem a chave por símbolo.
        if len(symbols) == 1:
            price = to_decimal(payload.get("price"))
            return {symbols[0]: price} if price is not None else {}
        prices: dict[str, Decimal] = {}
        for symbol in symbols:
            entry = payload.get(symbol)
            price = to_decimal(entry.get("price")) if isinstance(entry, dict) else None
            if price is not None:
                prices[symbol] = price
        return prices

    async def usd_brl(self) -> Decimal | None:
        """Quantos reais vale um dólar. Um crédito por rodada, não por ativo."""
        prices = await self.prices([USD_BRL])
        return prices.get(USD_BRL)

    async def search(self, term: str, *, limit: int = SEARCH_LIMIT) -> list[AssetHit]:
        """Busca de ações americanas. Não consome crédito.

        Filtra pelo país porque o mesmo `AAPL` volta listado em Buenos Aires e
        em Bogotá, em pesos: o usuário que escolhesse a linha errada teria uma
        posição cotada numa moeda que este sistema não converte.
        """
        payload = await get_json(
            self.http,
            f"{BASE_URL}/symbol_search",
            params={"symbol": term, "outputsize": limit},
        )
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not rows:
            return []
        hits = [hit for hit in (_hit_from(row) for row in rows) if hit is not None]
        return hits[:limit]


def _hit_from(row: dict[str, Any]) -> AssetHit | None:
    if row.get("currency") != USD:
        return None
    symbol = row.get("symbol")
    if not symbol:
        return None
    return AssetHit(
        symbol=str(symbol).upper(),
        name=str(row.get("instrument_name") or symbol),
        currency=USD,
        exchange=row.get("exchange"),
    )
