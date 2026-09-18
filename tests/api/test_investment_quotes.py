"""O agendador de cotações, da posição cadastrada ao valor corrigido na tela.

Os provedores são os clientes **de verdade**, sobre um `httpx.MockTransport`:
o que se troca é a rede, não o código que interpreta a resposta. Um duble do
cliente provaria só que o agendador chama o que o teste mandou chamar — este
formato também reprova se o parser quebrar.

O relógio é parado, porque o acrual da renda fixa conta dias corridos e um teste
que dependesse do dia da execução renderia um valor diferente a cada rodada.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import partial
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from core.clock import Clock
from jobs.investment_quotes import (
    MarketProviders,
    QuoteBudget,
    RefreshReport,
    refresh_investment_values,
)
from providers.bcb import BcbClient
from providers.brapi import BrapiClient
from providers.twelve_data import MAX_SYMBOLS_PER_REQUEST, TwelveDataClient
from tests.api.client import ApiClient
from tests.api.conftest import APP_TIMEZONE
from tests.api.factories import register_user
from tests.factories import RegisteredUser

INVESTMENTS = "/api/v1/investments"

TODAY = date(2026, 9, 18)
NOON = datetime(2026, 9, 18, 15, 0, tzinfo=UTC)

BUDGET = QuoteBudget(
    brapi_symbols=15,
    twelve_data_symbols=7,
    accrual_batch=200,
    rate_max_age=timedelta(hours=12),
)


# --------------------------------------------------------- a rede de mentira


class FakeMarket:
    """Roteia por caminho e guarda o que foi pedido, para o teste afirmar o gasto."""

    def __init__(
        self,
        *,
        brl_prices: dict[str, float] | None = None,
        usd_prices: dict[str, str] | None = None,
        usd_brl: str | None = "5.00",
        cdi_daily: str = "0.050788",
        fails: bool = False,
    ) -> None:
        self.brl_prices = brl_prices or {}
        self.usd_prices = usd_prices or {}
        self.usd_brl = usd_brl
        self.cdi_daily = cdi_daily
        self.fails = fails
        self.brapi_symbols: list[str] = []
        self.twelve_batches: list[list[str]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.fails:
            return httpx.Response(503, json={"message": "fora do ar"})
        path = request.url.path
        if "brapi.dev" in request.url.host:
            return self._brapi(request, path)
        if "twelvedata" in request.url.host:
            return self._twelve(request)
        return self._bcb(path)

    def _brapi(self, request: httpx.Request, path: str) -> httpx.Response:
        symbol = request.url.params["symbols"]
        self.brapi_symbols.append(symbol)
        price = self.brl_prices.get(symbol)
        if price is None:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "symbol": symbol,
                        "data": {
                            "longName": f"{symbol} S.A.",
                            "currency": "BRL",
                            "regularMarketPrice": price,
                            "regularMarketChangePercent": 1.25,
                            "regularMarketPreviousClose": price,
                            "regularMarketTime": "2026-09-18T13:17:30.000Z",
                            "logourl": f"https://icons.brapi.dev/icons/{symbol}.svg",
                        },
                    }
                ]
            },
        )

    def _twelve(self, request: httpx.Request) -> httpx.Response:
        asked = parse_qs(request.url.query.decode())["symbol"][0].split(",")
        self.twelve_batches.append(asked)
        if asked == ["USD/BRL"]:
            if self.usd_brl is None:
                return httpx.Response(200, json={"code": 404, "status": "error"})
            return httpx.Response(200, json={"price": self.usd_brl})
        # Com um símbolo só, a Twelve Data devolve `{"price": ...}` cru, sem a
        # chave por símbolo. Reproduzir isso é metade do valor deste duble: foi
        # o que esta suíte pegou quando o formato do lote foi assumido para os dois.
        if len(asked) == 1:
            price = self.usd_prices.get(asked[0])
            if price is None:
                return httpx.Response(200, json={"code": 404, "status": "error"})
            return httpx.Response(200, json={"price": price})
        body: dict[str, Any] = {}
        for symbol in asked:
            price = self.usd_prices.get(symbol)
            body[symbol] = (
                {"price": price}
                if price is not None
                else {"code": 404, "message": "not found", "status": "error"}
            )
        return httpx.Response(200, json=body)

    def _bcb(self, path: str) -> httpx.Response:
        # A série diária (CDI, SELIC) e a mensal (IPCA, poupança) diferem só no
        # número; o cliente é que sabe por quantos períodos capitalizar.
        return httpx.Response(200, json=[{"data": "17/09/2026", "valor": self.cdi_daily}])

    def providers(self) -> MarketProviders:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        return MarketProviders(
            brapi=BrapiClient(http=http, token="token"),
            twelve_data=TwelveDataClient(http=http, api_key="chave"),
            bcb=BcbClient(http=http),
        )


def run_scheduler(
    client: ApiClient, market: FakeMarket, *, budget: QuoteBudget = BUDGET, on: date = TODAY
) -> RefreshReport:
    """Uma rodada no banco da aplicação, com o relógio parado em `on`."""
    moment = datetime(on.year, on.month, on.day, 15, 0, tzinfo=UTC)
    clock = Clock(tz=APP_TIMEZONE, instant=lambda: moment)
    database = client.app.state.database  # type: ignore[attr-defined]
    return client.portal.call(
        partial(refresh_investment_values, database, clock, market.providers(), budget)
    )


# ---------------------------------------------------------------- fixtures


def a_stock(client: ApiClient, user: RegisteredUser, **overrides: Any) -> dict[str, Any]:
    response = client.post(
        INVESTMENTS,
        headers=user.auth,
        json={
            "type": "br_stock",
            "symbol": "PETR4",
            "quantity": "100",
            "average_price": "30.00",
            **overrides,
        },
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def a_cdb(client: ApiClient, user: RegisteredUser, **overrides: Any) -> dict[str, Any]:
    response = client.post(
        INVESTMENTS,
        headers=user.auth,
        json={
            "type": "cdb",
            "name": "CDB Liquidez",
            "invested_amount": "10000.00",
            "rate_index": "cdi",
            "rate_percent": "100",
            "applied_on": "2026-09-18",
            **overrides,
        },
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def read(client: ApiClient, user: RegisteredUser, investment_id: str) -> dict[str, Any]:
    response = client.get(f"{INVESTMENTS}/{investment_id}", headers=user.auth)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# ------------------------------------------------------- a renda variável


def test_a_brazilian_stock_is_worth_quantity_times_the_quoted_price(client: ApiClient) -> None:
    user = register_user(client)
    investment = a_stock(client, user)
    market = FakeMarket(brl_prices={"PETR4": 48.61})

    report = run_scheduler(client, market)

    assert report.quoted == 1
    assert report.positions == 1
    updated = read(client, user, investment["id"])
    assert updated["current_value"] == "4861.00"
    assert updated["asset"]["price"] == "48.61"
    assert updated["asset"]["change_percent"] == "1.25"
    assert updated["value_updated_at"] is not None


def test_a_dollar_priced_asset_is_converted_by_the_rate_of_the_round(client: ApiClient) -> None:
    """Uma consulta de câmbio para a rodada inteira, não uma por posição."""
    user = register_user(client)
    apple = a_stock(
        client,
        user,
        type="us_stock",
        symbol="AAPL",
        quantity="10",
        average_price="200.00",
        invested_amount="10000.00",
    )
    market = FakeMarket(usd_prices={"AAPL": "337.00"}, usd_brl="5.00")

    run_scheduler(client, market)

    # 10 x US$ 337,00 x 5,00 = R$ 16.850,00
    assert read(client, user, apple["id"])["current_value"] == "16850.00"


def test_a_cryptocurrency_is_quoted_against_the_dollar(client: ApiClient) -> None:
    """`BTC` na tela, `BTC/USD` na consulta — ver `providers.twelve_data.crypto_pair`."""
    user = register_user(client)
    bitcoin = a_stock(
        client,
        user,
        type="crypto",
        symbol="BTC",
        quantity="0.5",
        average_price="70000.00",
        invested_amount="180000.00",
    )
    market = FakeMarket(usd_prices={"BTC/USD": "78058.65"}, usd_brl="5.00")

    run_scheduler(client, market)

    assert any("BTC/USD" in batch for batch in market.twelve_batches)
    # 0,5 x US$ 78.058,65 x 5,00 = R$ 195.146,63 (meio para cima no centavo)
    assert read(client, user, bitcoin["id"])["current_value"] == "195146.63"


def test_everyone_holding_the_same_asset_is_revalued_by_one_request(client: ApiClient) -> None:
    """É o argumento do catálogo global: cem donos, uma requisição."""
    ana = register_user(client)
    bruno = register_user(client, email="bruno@exemplo.com", username="bruno")
    first = a_stock(client, ana)
    second = a_stock(client, bruno, quantity="7")
    market = FakeMarket(brl_prices={"PETR4": 10.00})

    report = run_scheduler(client, market)

    assert market.brapi_symbols == ["PETR4"], "cotou o mesmo papel mais de uma vez"
    assert report.positions == 2
    assert read(client, ana, first["id"])["current_value"] == "1000.00"
    assert read(client, bruno, second["id"])["current_value"] == "70.00"


def test_an_asset_nobody_holds_does_not_spend_a_request(client: ApiClient) -> None:
    """O catálogo sobrevive à posição, mas não merece crédito por isso."""
    user = register_user(client)
    investment = a_stock(client, user)
    client.delete(f"{INVESTMENTS}/{investment['id']}", headers=user.auth)
    market = FakeMarket(brl_prices={"PETR4": 48.61})

    report = run_scheduler(client, market)

    assert market.brapi_symbols == []
    assert report.quoted == 0


def test_a_symbol_the_provider_does_not_know_keeps_the_last_value(client: ApiClient) -> None:
    user = register_user(client)
    investment = a_stock(client, user, symbol="NAOEXISTE")
    market = FakeMarket(brl_prices={})

    run_scheduler(client, market)

    updated = read(client, user, investment["id"])
    assert updated["current_value"] == "3000.00"
    assert updated["asset"]["price"] is None


# ------------------------------------------------------------ o orçamento


def test_a_round_never_asks_twelve_data_for_more_than_the_minute_allows(
    client: ApiClient,
) -> None:
    """O lote que estoura o limite **gasta os créditos e devolve 429**."""
    user = register_user(client)
    for index in range(9):
        a_stock(
            client,
            user,
            type="us_stock",
            symbol=f"US{index}",
            quantity="1",
            average_price="1.00",
            invested_amount="1.00",
        )
    generous = QuoteBudget(
        brapi_symbols=15,
        twelve_data_symbols=50,
        accrual_batch=200,
        rate_max_age=timedelta(hours=12),
    )
    market = FakeMarket(usd_prices={f"US{index}": "2.00" for index in range(9)})

    run_scheduler(client, market, budget=generous)

    assert all(len(batch) <= MAX_SYMBOLS_PER_REQUEST for batch in market.twelve_batches)


def test_the_budget_caps_the_round_and_the_oldest_goes_first(client: ApiClient) -> None:
    """Teto atrasa um ativo, nunca o abandona: o que não coube é o mais velho da próxima."""
    user = register_user(client)
    for symbol in ("AAA3", "BBB3", "CCC3"):
        a_stock(client, user, symbol=symbol)
    tight = QuoteBudget(
        brapi_symbols=2, twelve_data_symbols=7, accrual_batch=200, rate_max_age=timedelta(hours=12)
    )
    prices = {"AAA3": 1.0, "BBB3": 2.0, "CCC3": 3.0}

    first = FakeMarket(brl_prices=prices)
    run_scheduler(client, first, budget=tight)
    second = FakeMarket(brl_prices=prices)
    run_scheduler(client, second, budget=tight, on=date(2026, 9, 19))

    assert len(first.brapi_symbols) == 2
    # O que ficou de fora da primeira rodada é o mais desatualizado na segunda.
    esquecido = ({"AAA3", "BBB3", "CCC3"} - set(first.brapi_symbols)).pop()
    assert second.brapi_symbols[0] == esquecido


# ------------------------------------------------------------- a renda fixa


def test_fixed_income_accrues_by_the_days_since_the_last_correction(client: ApiClient) -> None:
    """100% do CDI a ~13,6% ao ano, sobre R$ 10.000, rende alguns reais em 30 dias."""
    user = register_user(client)
    cdb = a_cdb(client, user)
    market = FakeMarket()

    run_scheduler(client, market, on=date(2026, 10, 18))

    value = Decimal(read(client, user, cdb["id"])["current_value"])
    assert Decimal("10100.00") < value < Decimal("10120.00")


def test_a_second_run_on_the_same_day_does_not_pay_interest_twice(client: ApiClient) -> None:
    """95 das 96 rodadas do dia não têm juro a lançar — e nenhuma pode duplicá-lo."""
    user = register_user(client)
    cdb = a_cdb(client, user)

    run_scheduler(client, FakeMarket(), on=date(2026, 10, 18))
    after_first = read(client, user, cdb["id"])["current_value"]
    run_scheduler(client, FakeMarket(), on=date(2026, 10, 18))

    assert read(client, user, cdb["id"])["current_value"] == after_first


def test_a_position_without_a_known_index_does_not_move(client: ApiClient) -> None:
    """Sem a leitura do Banco Central não há o que capitalizar — e zero mentiria."""
    user = register_user(client)
    cdb = a_cdb(client, user)
    silent = FakeMarket()
    silent._bcb = lambda _path: httpx.Response(200, json=[])  # type: ignore[method-assign]

    run_scheduler(client, silent, on=date(2026, 10, 18))

    assert read(client, user, cdb["id"])["current_value"] == "10000.00"


def test_a_prefixed_contract_accrues_without_any_index(client: ApiClient) -> None:
    """A taxa dele é a contratada: não depende de provedor nenhum."""
    user = register_user(client)
    prefixado = a_cdb(client, user, rate_index="prefixed", rate_percent="12")
    silent = FakeMarket()
    silent._bcb = lambda _path: httpx.Response(200, json=[])  # type: ignore[method-assign]

    run_scheduler(client, silent, on=date(2027, 9, 18))

    value = Decimal(read(client, user, prefixado["id"])["current_value"])
    assert Decimal("11190.00") < value < Decimal("11210.00")


def test_savings_is_created_and_accrued_without_the_user_naming_a_rate(client: ApiClient) -> None:
    user = register_user(client)
    response = client.post(
        INVESTMENTS,
        headers=user.auth,
        json={
            "type": "savings",
            "name": "Poupança",
            "invested_amount": "1000.00",
            "applied_on": "2026-09-18",
        },
    )
    poupanca = response.json()

    run_scheduler(client, FakeMarket(), on=date(2027, 9, 18))

    assert Decimal(read(client, user, poupanca["id"])["current_value"]) > Decimal("1000.00")


# --------------------------------------------------- provedor fora do ar


def test_a_provider_that_is_down_leaves_every_value_where_it_was(client: ApiClient) -> None:
    """Uma cotação velha é estado legítimo; zerar seria inventar um prejuízo."""
    user = register_user(client)
    investment = a_stock(client, user)
    cdb = a_cdb(client, user)

    report = run_scheduler(client, FakeMarket(fails=True))

    assert report == RefreshReport()
    assert read(client, user, investment["id"])["current_value"] == "3000.00"
    assert read(client, user, cdb["id"])["current_value"] == "10000.00"


def test_the_quote_stage_failing_does_not_undo_the_accrual(client: ApiClient) -> None:
    """Commits separados: a Twelve Data fora do ar não desfaz o juro já calculado."""
    user = register_user(client)
    cdb = a_cdb(client, user)
    market = FakeMarket()
    market._twelve = lambda _request: httpx.Response(503, json={})  # type: ignore[method-assign]

    run_scheduler(client, market, on=date(2026, 10, 18))

    assert Decimal(read(client, user, cdb["id"])["current_value"]) > Decimal("10000.00")


# ---------------------------------------------------------------- o resumo


def test_the_summary_reflects_what_the_round_corrected(client: ApiClient) -> None:
    user = register_user(client)
    a_stock(client, user)

    run_scheduler(client, FakeMarket(brl_prices={"PETR4": 45.00}))

    summary = client.get(f"{INVESTMENTS}/summary", headers=user.auth).json()
    assert summary["total_value"] == "4500.00"
    assert summary["total_invested"] == "3000.00"
    assert summary["profit"] == "1500.00"
    assert summary["profit_percent"] == "50.0000"
    assert summary["value_updated_at"] is not None


@pytest.mark.parametrize("enabled", [True, False])
def test_a_round_without_providers_configured_is_a_no_op(client: ApiClient, enabled: bool) -> None:
    """Sem token, o provedor não existe — e a aplicação sobe do mesmo jeito."""
    user = register_user(client)
    investment = a_stock(client, user)
    database = client.app.state.database  # type: ignore[attr-defined]
    clock = Clock(tz=APP_TIMEZONE, instant=lambda: NOON)

    report = client.portal.call(
        partial(refresh_investment_values, database, clock, MarketProviders(), BUDGET)
    )

    assert report == RefreshReport()
    assert read(client, user, investment["id"])["current_value"] == "3000.00"


def test_the_asset_keeps_the_logo_the_provider_sends(client: ApiClient) -> None:
    """A tela desenha o ícone do papel; ele vem de graça na mesma resposta."""
    user = register_user(client)
    investment = a_stock(client, user)

    run_scheduler(client, FakeMarket(brl_prices={"PETR4": 48.61}))

    asset = read(client, user, investment["id"])["asset"]
    assert asset["logo_url"] == "https://icons.brapi.dev/icons/PETR4.svg"


def test_without_an_exchange_rate_nothing_priced_in_dollars_moves(client: ApiClient) -> None:
    """Converter com câmbio desconhecido seria inventar patrimônio."""
    user = register_user(client)
    apple = a_stock(
        client,
        user,
        type="us_stock",
        symbol="AAPL",
        quantity="10",
        average_price="200.00",
        invested_amount="10000.00",
    )
    # A etapa em dólar começa pelo câmbio; sem ele ela para antes de cotar, e
    # nenhum crédito é gasto com um preço que não teria como virar real.
    market = FakeMarket(usd_prices={"AAPL": "337.00"}, usd_brl=None)

    report = run_scheduler(client, market)

    assert report.quoted == 0
    assert market.twelve_batches == [["USD/BRL"]]
    assert read(client, user, apple["id"])["current_value"] == "10000.00"


def test_a_symbol_the_batch_did_not_price_keeps_its_value(client: ApiClient) -> None:
    """Um símbolo ruim não derruba o lote: os outros já custaram crédito."""
    user = register_user(client)
    known = a_stock(
        client,
        user,
        type="us_stock",
        symbol="AAPL",
        quantity="1",
        average_price="1.00",
        invested_amount="100.00",
    )
    unknown = a_stock(
        client,
        user,
        type="us_stock",
        symbol="XYZZY",
        quantity="1",
        average_price="1.00",
        invested_amount="200.00",
    )
    market = FakeMarket(usd_prices={"AAPL": "10.00"}, usd_brl="5.00")

    report = run_scheduler(client, market)

    assert report.quoted == 1
    assert read(client, user, known["id"])["current_value"] == "50.00"
    assert read(client, user, unknown["id"])["current_value"] == "200.00"


def test_a_budget_of_zero_spends_nothing(client: ApiClient) -> None:
    """É o botão de desligar por provedor, sem mexer no agendador inteiro."""
    user = register_user(client)
    investment = a_stock(client, user)
    zeroed = QuoteBudget(
        brapi_symbols=0, twelve_data_symbols=0, accrual_batch=0, rate_max_age=timedelta(hours=12)
    )
    market = FakeMarket(brl_prices={"PETR4": 48.61})

    report = run_scheduler(client, market, budget=zeroed)

    assert market.brapi_symbols == []
    assert report.quoted == 0
    assert read(client, user, investment["id"])["current_value"] == "3000.00"


def test_an_index_already_read_today_is_not_read_again(client: ApiClient) -> None:
    """O SGS publica uma vez por dia; reler a cada 15 min seriam 384 requisições."""
    register_user(client)

    first = FakeMarket()
    assert run_scheduler(client, first).rates == 4
    second = FakeMarket()
    assert run_scheduler(client, second).rates == 0


def test_interest_too_small_for_a_cent_accumulates_instead_of_vanishing(
    client: ApiClient,
) -> None:
    """Um dia de juro sobre R$ 1,00 não chega a um centavo — e não pode sumir.

    Marcar a posição como atualizada arredondando para o mesmo valor faria o
    rendimento se perder todo dia, e a posição nunca sairia de R$ 1,00. Por isso
    a rodada só move `value_updated_at` quando o centavo de fato mudou: o juro
    fica pendurado até valer um.
    """
    user = register_user(client)
    trocado = a_cdb(client, user, invested_amount="1.00")

    run_scheduler(client, FakeMarket(), on=date(2026, 9, 19))
    depois_de_um_dia = read(client, user, trocado["id"])
    run_scheduler(client, FakeMarket(), on=date(2027, 9, 18))

    assert depois_de_um_dia["current_value"] == "1.00"
    assert depois_de_um_dia["value_updated_at"] is None, "marcou como corrigido sem corrigir"
    assert Decimal(read(client, user, trocado["id"])["current_value"]) > Decimal("1.00")
