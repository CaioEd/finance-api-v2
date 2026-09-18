"""Busca de ativos: qual provedor responde por qual tipo, pela travessia HTTP.

Os clientes são os de verdade sobre `httpx.MockTransport`, como em
`test_investment_quotes.py`: o que se troca é a rede. O serviço é injetado em
`app.state.market`, que é de onde a dependência o lê — sem `dependency_overrides`,
porque o que se quer verificar inclui a fiação.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from services.market_service import MarketService
from tests.api.client import ApiClient
from tests.api.factories import register_user
from tests.factories import RegisteredUser

ASSETS = "/api/v1/investments/assets"

BRAPI_SEARCH = {
    "indexes": [],
    "stocks": [
        {
            "stock": "PETR4",
            "name": "PETROLEO BRASILEIRO S.A. PETROBRAS",
            "close": 48.61,
            "sector": "Energy Minerals",
            "logo": "https://icons.brapi.dev/icons/PETR4.svg",
        }
    ],
}

TWELVE_SEARCH = {
    "data": [
        {
            "symbol": "AAPL",
            "instrument_name": "Apple Inc",
            "exchange": "NASDAQ",
            "country": "United States",
            "currency": "USD",
        },
        {
            "symbol": "AAPL",
            "instrument_name": "Apple Inc. CEDEAR",
            "exchange": "BCBA",
            "country": "Argentina",
            "currency": "ARS",
        },
    ],
    "status": "ok",
}


@pytest.fixture
def market(client: ApiClient) -> Iterator[list[httpx.Request]]:
    """Põe no `app.state` um `MarketService` cujos provedores não saem à rede."""
    from providers.brapi import BrapiClient
    from providers.twelve_data import TwelveDataClient

    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        payload = BRAPI_SEARCH if "brapi.dev" in request.url.host else TWELVE_SEARCH
        return httpx.Response(200, json=payload)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    previous = client.app.state.market  # type: ignore[attr-defined]
    client.app.state.market = MarketService(  # type: ignore[attr-defined]
        brapi=BrapiClient(http=http, token="token"),
        twelve_data=TwelveDataClient(http=http, api_key="chave"),
    )
    yield sent
    client.app.state.market = previous  # type: ignore[attr-defined]


def search(client: ApiClient, user: RegisteredUser, **params: Any) -> Any:
    response = client.get(ASSETS, headers=user.auth, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_brazilian_stock_is_searched_at_brapi(
    client: ApiClient, market: list[httpx.Request]
) -> None:
    user = register_user(client)

    hits = search(client, user, type="br_stock", query="petr")

    assert hits == [
        {
            "type": "br_stock",
            "symbol": "PETR4",
            "name": "PETROLEO BRASILEIRO S.A. PETROBRAS",
            "currency": "BRL",
            "exchange": "Energy Minerals",
            "price": "48.61",
            "logo_url": "https://icons.brapi.dev/icons/PETR4.svg",
        }
    ]
    assert "brapi.dev" in market[0].url.host


def test_an_american_stock_is_searched_at_twelve_data(
    client: ApiClient, market: list[httpx.Request]
) -> None:
    """E o mesmo código listado em pesos fica de fora: este sistema não converte peso."""
    user = register_user(client)

    hits = search(client, user, type="us_stock", query="AAPL")

    assert [(hit["symbol"], hit["currency"], hit["exchange"]) for hit in hits] == [
        ("AAPL", "USD", "NASDAQ")
    ]
    assert "twelvedata" in market[0].url.host


def test_cryptocurrencies_are_a_closed_list_and_cost_no_request(
    client: ApiClient, market: list[httpx.Request]
) -> None:
    """Cotar as nove para montar um seletor gastaria o orçamento de um minuto inteiro."""
    user = register_user(client)

    hits = search(client, user, type="crypto")

    assert {hit["symbol"] for hit in hits} == {
        "BTC",
        "ETH",
        "SOL",
        "DOT",
        "LTC",
        "USDC",
        "DOGE",
        "USDT",
        "ADA",
    }
    assert all(hit["price"] is None for hit in hits)
    assert all(hit["currency"] == "USD" for hit in hits)
    assert market == [], "a lista fechada saiu à rede"


def test_a_cryptocurrency_can_be_filtered_by_symbol_or_by_name(
    client: ApiClient, market: list[httpx.Request]
) -> None:
    user = register_user(client)

    by_symbol = search(client, user, type="crypto", query="btc")
    by_name = search(client, user, type="crypto", query="cardano")

    assert [hit["symbol"] for hit in by_symbol] == ["BTC"]
    assert [hit["symbol"] for hit in by_name] == ["ADA"]


def test_fixed_income_has_nothing_to_search(client: ApiClient) -> None:
    """CDB e Tesouro não têm símbolo; lista vazia faria a tela parecer quebrada."""
    user = register_user(client)

    response = client.get(ASSETS, headers=user.auth, params={"type": "cdb"})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "investment_type_not_searchable"


def test_the_type_is_required(client: ApiClient) -> None:
    user = register_user(client)

    response = client.get(ASSETS, headers=user.auth, params={"query": "petr"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_a_provider_without_credentials_returns_an_empty_list(client: ApiClient) -> None:
    """Sem `BRAPI_TOKEN` a busca some, mas a aplicação sobe e o resto funciona.

    É o que permite rodar a suíte e um ambiente de revisão sem as chaves de
    ninguém — recusar a subida por falta de chave de cotação seria desproporcional.
    """
    user = register_user(client)
    previous = client.app.state.market  # type: ignore[attr-defined]
    client.app.state.market = MarketService(brapi=None, twelve_data=None)  # type: ignore[attr-defined]
    try:
        assert search(client, user, type="br_stock", query="petr") == []
        assert search(client, user, type="us_stock", query="aapl") == []
        # Cripto não depende de provedor nenhum e continua respondendo.
        assert search(client, user, type="crypto") != []
    finally:
        client.app.state.market = previous  # type: ignore[attr-defined]
