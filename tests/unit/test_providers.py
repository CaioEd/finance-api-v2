"""Clientes dos provedores — sem rede, contra as respostas que eles realmente dão.

Os corpos abaixo **não são inventados**: são o formato medido contra a BRAPI, a
Twelve Data e o SGS do Banco Central com os tokens do projeto, em 18/09/2026
(ver `docs/investimentos.md`). É o que dá valor a este arquivo: um teste contra
um JSON imaginado só prova que o parser lê o que o autor do teste achou que
viria.

A rede é substituída por `httpx.MockTransport`, e não por um mock do cliente:
o que se afirma é o que a aplicação **mandaria** — caminho, query string e
cabeçalho de autorização — e o que ela faz com o que volta.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest

from models.investment import RateIndex
from providers.base import MarketDataUnavailableError, to_decimal
from providers.bcb import BcbClient, accumulate_monthly, annualize_daily
from providers.brapi import BrapiClient
from providers.twelve_data import (
    CRYPTOCURRENCIES,
    MAX_SYMBOLS_PER_REQUEST,
    TwelveDataClient,
    crypto_pair,
)

TOKEN = "token-de-teste"

# --------------------------------------------------- corpos reais, abreviados

BRAPI_QUOTE = {
    "results": [
        {
            "requestedSymbol": "B3SA3",
            "symbol": "B3SA3",
            "changed": False,
            "data": {
                "shortName": "B3SA3",
                "longName": "B3 SA - Brasil, Bolsa, Balcao",
                "currency": "BRL",
                "regularMarketPrice": 17.6,
                "regularMarketChange": 0.02,
                "regularMarketChangePercent": 0.11,
                "regularMarketTime": "2026-09-18T13:17:30.000Z",
                "regularMarketPreviousClose": 17.7,
                "logourl": "https://icons.brapi.dev/icons/B3SA3.svg",
            },
        }
    ],
    "requestedAt": "2026-09-18T13:17:31.092Z",
}

BRAPI_SEARCH = {
    "indexes": [],
    "stocks": [
        {
            "stock": "PETR4",
            "name": "PETROLEO BRASILEIRO S.A. PETROBRAS",
            "close": 48.61,
            "change": -0.08,
            "sector": "Energy Minerals",
            "logo": "https://icons.brapi.dev/icons/PETR4.svg",
            "type": "stock",
        },
        {
            "stock": "PETR3",
            "name": "PETROLEO BRASILEIRO S.A. PETROBRAS",
            "close": 54,
            "change": 0.32,
            "sector": "Energy Minerals",
            "logo": "https://icons.brapi.dev/icons/PETR3.svg",
            "type": "stock",
        },
    ],
}

# O plano gratuito devolve 200 com um corpo de erro, e não um status de erro.
BRAPI_PAID_FEATURE = {
    "error": True,
    "message": "Criptomoedas requer o plano Startup. Seu plano atual: Gratuito.",
    "code": "FEATURE_NOT_AVAILABLE",
}

TWELVE_BATCH = {
    "BTC/USD": {"price": "78058.65"},
    "ETH/USD": {"price": "2500"},
    "AAPL": {"price": "337.079987"},
}

TWELVE_SINGLE = {"price": "5.14683"}

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

BCB_CDI = [{"data": "17/09/2026", "valor": "0.050788"}]

# Série mensal: o SGS devolve as doze leituras pedidas, da mais antiga para a
# mais recente. Os valores são os do IPCA de 2026 — e agosto fechou em deflação,
# que é justamente o caso que quebrava a estimativa antiga.
BCB_IPCA_12 = [
    {"data": "01/09/2025", "valor": "0.44"},
    {"data": "01/10/2025", "valor": "0.56"},
    {"data": "01/11/2025", "valor": "0.39"},
    {"data": "01/12/2025", "valor": "0.52"},
    {"data": "01/01/2026", "valor": "0.42"},
    {"data": "01/02/2026", "valor": "0.83"},
    {"data": "01/03/2026", "valor": "0.88"},
    {"data": "01/04/2026", "valor": "0.67"},
    {"data": "01/05/2026", "valor": "0.58"},
    {"data": "01/06/2026", "valor": "0.16"},
    {"data": "01/07/2026", "valor": "0.07"},
    {"data": "01/08/2026", "valor": "-0.32"},
]
BCB_SAVINGS = [{"data": "17/09/2026", "dataFim": "17/10/2026", "valor": "0.6695"}]


# ------------------------------------------------------------------- apoio


def client_returning(
    payload: Any, *, status: int = 200
) -> tuple[httpx.AsyncClient, list[httpx.Request]]:
    """Um `AsyncClient` que responde sempre o mesmo, guardando o que recebeu."""
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(status, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), sent


def client_returning_text(body: str, *, status: int = 200) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, text=body))
    )


# -------------------------------------------------------------------- BRAPI


async def test_brapi_reads_the_price_from_results_data() -> None:
    """O caminho é `results[0].data`, como o `docs/brapi_api.md` manda."""
    http, sent = client_returning(BRAPI_QUOTE)
    async with http:
        quote = await BrapiClient(http=http, token=TOKEN).quote("B3SA3")

    assert quote is not None
    assert quote.symbol == "B3SA3"
    assert quote.price == Decimal("17.6")
    assert quote.currency == "BRL"
    assert quote.name == "B3 SA - Brasil, Bolsa, Balcao"
    assert quote.previous_close == Decimal("17.7")
    assert quote.change_percent == Decimal("0.11")
    assert quote.quoted_at == datetime(2026, 9, 18, 13, 17, 30, tzinfo=UTC)
    assert sent[0].url.params["symbols"] == "B3SA3"


async def test_brapi_sends_the_token_as_a_bearer_header() -> None:
    """A chave nunca vai na query string: ela apareceria em log de proxy."""
    http, sent = client_returning(BRAPI_QUOTE)
    async with http:
        await BrapiClient(http=http, token=TOKEN).quote("B3SA3")

    assert sent[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(sent[0].url)


async def test_brapi_asks_for_one_symbol_at_a_time() -> None:
    """O plano gratuito recusa dois — ver `MAX_SYMBOLS_PER_REQUEST`."""
    http, sent = client_returning(BRAPI_QUOTE)
    async with http:
        await BrapiClient(http=http, token=TOKEN).quote("B3SA3")

    assert "," not in sent[0].url.params["symbols"]


async def test_brapi_returns_none_for_an_unknown_symbol() -> None:
    """Símbolo inexistente é resposta legítima, não queda do provedor.

    A distinção importa para o agendador: tentar de novo na rodada seguinte não
    faria o papel passar a existir.
    """
    http, _ = client_returning({"results": []})
    async with http:
        assert await BrapiClient(http=http, token=TOKEN).quote("NAOEXISTE") is None


async def test_brapi_treats_a_paid_feature_body_as_no_quote() -> None:
    """Recurso de plano pago vem como 200 com `error: true`, e não como 4xx."""
    http, _ = client_returning(BRAPI_PAID_FEATURE)
    async with http:
        assert await BrapiClient(http=http, token=TOKEN).quote("BTC") is None


async def test_brapi_search_uses_quote_list_and_brings_the_price() -> None:
    """É `/quote/list?search=`; `/available?search=` responde 200 e lista vazia."""
    http, sent = client_returning(BRAPI_SEARCH)
    async with http:
        hits = await BrapiClient(http=http, token=TOKEN).search("petr")

    assert sent[0].url.path.endswith("/quote/list")
    assert sent[0].url.params["search"] == "petr"
    assert [hit.symbol for hit in hits] == ["PETR4", "PETR3"]
    assert hits[0].price == Decimal("48.61")
    assert hits[0].currency == "BRL"


async def test_brapi_search_respects_the_limit() -> None:
    http, _ = client_returning(BRAPI_SEARCH)
    async with http:
        hits = await BrapiClient(http=http, token=TOKEN).search("petr", limit=1)

    assert len(hits) == 1


# --------------------------------------------------------------- Twelve Data


async def test_twelve_data_reads_a_batch_by_symbol() -> None:
    http, sent = client_returning(TWELVE_BATCH)
    async with http:
        prices = await TwelveDataClient(http=http, api_key=TOKEN).prices(
            ["BTC/USD", "ETH/USD", "AAPL"]
        )

    assert prices == {
        "BTC/USD": Decimal("78058.65"),
        "ETH/USD": Decimal("2500"),
        "AAPL": Decimal("337.079987"),
    }
    assert sent[0].url.params["symbol"] == "BTC/USD,ETH/USD,AAPL"


async def test_twelve_data_reads_a_single_symbol_without_the_outer_key() -> None:
    """Com um símbolo só a resposta é `{"price": ...}`, sem a chave por símbolo."""
    http, _ = client_returning(TWELVE_SINGLE)
    async with http:
        prices = await TwelveDataClient(http=http, api_key=TOKEN).prices(["USD/BRL"])

    assert prices == {"USD/BRL": Decimal("5.14683")}


async def test_twelve_data_skips_the_symbol_it_does_not_know() -> None:
    """Um símbolo ruim não pode derrubar o lote: os outros sete já custaram crédito."""
    http, _ = client_returning(
        {
            "BTC/USD": {"price": "78058.65"},
            "DOT/BRL": {"code": 404, "message": "**symbol** not found", "status": "error"},
        }
    )
    async with http:
        prices = await TwelveDataClient(http=http, api_key=TOKEN).prices(["BTC/USD", "DOT/BRL"])

    assert prices == {"BTC/USD": Decimal("78058.65")}


async def test_a_batch_above_the_free_plan_limit_is_refused_before_the_request() -> None:
    """O lote que estoura **gasta os créditos e devolve 429**: recusar aqui é de graça."""
    http, sent = client_returning(TWELVE_BATCH)
    symbols = [f"S{n}" for n in range(MAX_SYMBOLS_PER_REQUEST + 1)]
    async with http:
        with pytest.raises(ValueError, match="crédito"):
            await TwelveDataClient(http=http, api_key=TOKEN).prices(symbols)

    assert sent == [], "a requisição chegou a sair"


async def test_the_api_key_travels_as_a_parameter_because_the_provider_demands_it() -> None:
    http, sent = client_returning(TWELVE_SINGLE)
    async with http:
        await TwelveDataClient(http=http, api_key=TOKEN).usd_brl()

    assert sent[0].url.params["apikey"] == TOKEN
    assert sent[0].url.params["symbol"] == "USD/BRL"


async def test_twelve_data_search_keeps_only_what_is_quoted_in_dollars() -> None:
    """O mesmo `AAPL` volta listado em Buenos Aires, em pesos — e este sistema não converte peso."""
    http, _ = client_returning(TWELVE_SEARCH)
    async with http:
        hits = await TwelveDataClient(http=http, api_key=TOKEN).search("AAPL")

    assert [(hit.symbol, hit.exchange) for hit in hits] == [("AAPL", "NASDAQ")]


@pytest.mark.parametrize("symbol", sorted(CRYPTOCURRENCIES))
def test_every_offered_cryptocurrency_is_queried_against_the_dollar(symbol: str) -> None:
    """`BTC/BRL` existe, mas `DOT/BRL`, `USDC/BRL` e `DOGE/BRL` não.

    Um caminho só para as nove é melhor que sete em dólar e duas em real; a
    conversão sai do câmbio da rodada.
    """
    assert crypto_pair(symbol) == f"{symbol}/USD"


def test_the_offered_cryptocurrencies_are_the_nine_of_the_scope() -> None:
    assert sorted(CRYPTOCURRENCIES) == [
        "ADA",
        "BTC",
        "DOGE",
        "DOT",
        "ETH",
        "LTC",
        "SOL",
        "USDC",
        "USDT",
    ]


# ---------------------------------------------------------------------- BCB


async def test_the_daily_series_is_annualised_over_business_days() -> None:
    """`0,050788% ao dia útil` vira ~13,6% ao ano por `(1+i)^252 - 1`."""
    http, sent = client_returning(BCB_CDI)
    async with http:
        rate = await BcbClient(http=http).latest(RateIndex.CDI)

    assert rate is not None
    assert rate.reference_date == date(2026, 9, 17)
    assert Decimal("13") < rate.annual_percent < Decimal("14")
    assert "bcdata.sgs.12" in sent[0].url.path


async def test_a_monthly_series_is_the_accumulated_of_twelve_months() -> None:
    """O acumulado do ano, e não a última leitura elevada a 12.

    A soma dos doze meses de 2026 dá ~5,3%, que é a inflação do período. Um mês
    de deflação no fim da janela não pode virar "-3,8% ao ano" — e viraria, se a
    conta fosse pela última leitura.
    """
    http, sent = client_returning(BCB_IPCA_12)
    async with http:
        rate = await BcbClient(http=http).latest(RateIndex.IPCA)

    assert rate is not None
    assert Decimal("5") < rate.annual_percent < Decimal("6")
    assert "bcdata.sgs.433" in sent[0].url.path
    # A referência é a leitura mais recente, e não o começo da janela.
    assert rate.reference_date == date(2026, 8, 1)


async def test_a_monthly_series_asks_for_the_whole_year_at_once() -> None:
    """Doze leituras não custam mais que uma: o SGS é público e responde tudo junto."""
    http, sent = client_returning(BCB_IPCA_12)
    async with http:
        await BcbClient(http=http).latest(RateIndex.IPCA)

    assert sent[0].url.path.endswith("/ultimos/12")


async def test_a_daily_series_asks_for_one_reading_only() -> None:
    """A taxa diária é estável: a última basta, e capitalizá-la por 252 dá o ano."""
    http, sent = client_returning(BCB_CDI)
    async with http:
        await BcbClient(http=http).latest(RateIndex.CDI)

    assert sent[0].url.path.endswith("/ultimos/1")


async def test_a_partial_monthly_window_is_projected_to_a_full_year() -> None:
    """Menos de doze meses não pode virar uma taxa anual menor do que é.

    `1%` ao mês por seis meses é ~6,15% no período e ~12,7% ao ano; devolver os
    6,15% como taxa anual faria a posição render metade do que rende.
    """
    six = [{"data": f"01/0{month}/2026", "valor": "1.0"} for month in range(1, 7)]
    http, _ = client_returning(six)
    async with http:
        rate = await BcbClient(http=http).latest(RateIndex.SAVINGS)

    assert rate is not None
    assert Decimal("12") < rate.annual_percent < Decimal("13")


async def test_the_bcb_needs_no_credential() -> None:
    """A API SGS é aberta — é por isso que ela entrou como terceira fonte."""
    http, sent = client_returning(BCB_CDI)
    async with http:
        await BcbClient(http=http).latest(RateIndex.CDI)

    assert "Authorization" not in sent[0].headers


async def test_an_empty_series_is_no_rate_rather_than_an_error() -> None:
    http, _ = client_returning([])
    async with http:
        assert await BcbClient(http=http).latest(RateIndex.CDI) is None


def test_compounding_beats_multiplying_the_daily_rate() -> None:
    """Somar a taxa diária 252 vezes daria um número menor e errado — juro compõe."""
    daily = Decimal("0.050788")

    compounded = annualize_daily(daily)

    assert compounded > daily * 252


def test_a_deflationary_month_does_not_erase_a_year_of_inflation() -> None:
    """O caso que motivou a mudança, isolado da rede.

    Onze meses de 0,5% e um de -0,32% somam ~5,2% no ano. Pela última leitura
    elevada a 12, o mesmo ano viraria -3,8% — e um Tesouro IPCA+5,8% passaria a
    render menos que a poupança.
    """
    values = [Decimal("0.5")] * 11 + [Decimal("-0.32")]

    accumulated = accumulate_monthly(values)

    assert Decimal("5") < accumulated < Decimal("6")
    assert accumulated > annualize_monthly_naively(values[-1])


def annualize_monthly_naively(value: Decimal) -> Decimal:
    """A conta antiga, guardada só para o teste acima mostrar a diferença."""
    return ((Decimal(1) + value / 100) ** 12 - 1) * 100


async def test_a_rate_for_a_prefixed_contract_is_a_programming_error() -> None:
    """Não há série a consultar: a taxa do prefixado é a que foi contratada.

    `KeyError` e não `None`: pedir ao Banco Central a taxa de um contrato
    prefixado é engano de quem chama, não dado que falta — e um `None` aqui
    passaria por "o SGS não respondeu", que é outra coisa.
    """
    http, sent = client_returning(BCB_CDI)
    async with http:
        with pytest.raises(KeyError):
            await BcbClient(http=http).latest(RateIndex.PREFIXED)

    assert sent == [], "chegou a consultar o SGS por uma série que não existe"


# ------------------------------------------------------------- falhas de fora


@pytest.mark.parametrize("status", [401, 429, 500, 503])
async def test_any_provider_failure_becomes_the_same_domain_error(status: int) -> None:
    """Uma porta só para timeout, 4xx, 5xx e corpo ilegível.

    Quem chama trata uma exceção, não seis, e a resposta nunca é 500: a rede de
    outra pessoa cair não é defeito deste código.
    """
    http, _ = client_returning({"message": "não"}, status=status)
    async with http:
        with pytest.raises(MarketDataUnavailableError):
            await BrapiClient(http=http, token=TOKEN).quote("PETR4")


async def test_a_body_that_is_not_json_is_a_provider_failure() -> None:
    http = client_returning_text("<html>manutenção</html>")
    async with http:
        with pytest.raises(MarketDataUnavailableError):
            await BcbClient(http=http).latest(RateIndex.CDI)


async def test_a_network_error_is_a_provider_failure() -> None:
    def explode(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("sem resposta")

    async with httpx.AsyncClient(transport=httpx.MockTransport(explode)) as http:
        with pytest.raises(MarketDataUnavailableError):
            await TwelveDataClient(http=http, api_key=TOKEN).usd_brl()


def test_a_price_keeps_every_decimal_place_the_provider_sent() -> None:
    """Via `str`: `Decimal(float)` arrastaria o erro do double para dentro do `NUMERIC`."""
    assert to_decimal(json.loads('{"p": 0.05078812345}')["p"]) == Decimal("0.05078812345")
    assert to_decimal("337.079987") == Decimal("337.079987")
    assert to_decimal(None) is None
    assert to_decimal("não é número") is None
    assert to_decimal(True) is None


# ------------------------------------------- respostas que não são o esperado


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="corpo-vazio"),
        pytest.param([], id="lista-em-vez-de-objeto"),
        pytest.param({"results": [{"symbol": "X", "data": {}}]}, id="sem-preço"),
        pytest.param({"results": [{"symbol": "X"}]}, id="sem-data"),
    ],
)
async def test_brapi_treats_an_unexpected_body_as_no_quote(payload: Any) -> None:
    """Provedor que muda o formato não pode virar 500 nem cotação inventada."""
    http, _ = client_returning(payload)
    async with http:
        assert await BrapiClient(http=http, token=TOKEN).quote("X") is None


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="sem-stocks"),
        pytest.param({"stocks": []}, id="stocks-vazio"),
        pytest.param({"stocks": [{"name": "sem código"}]}, id="linha-sem-symbol"),
        pytest.param([], id="lista-em-vez-de-objeto"),
    ],
)
async def test_brapi_search_survives_an_unexpected_body(payload: Any) -> None:
    http, _ = client_returning(payload)
    async with http:
        assert await BrapiClient(http=http, token=TOKEN).search("x") == []


async def test_an_empty_batch_costs_no_request() -> None:
    """Zero símbolo é zero crédito: nem chega a sair."""
    http, sent = client_returning(TWELVE_BATCH)
    async with http:
        assert await TwelveDataClient(http=http, api_key=TOKEN).prices([]) == {}

    assert sent == []


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param([], id="lista-em-vez-de-objeto"),
        pytest.param({"code": 404, "status": "error"}, id="erro-no-lugar-do-preço"),
    ],
)
async def test_twelve_data_treats_an_unexpected_body_as_no_price(payload: Any) -> None:
    http, _ = client_returning(payload)
    async with http:
        assert await TwelveDataClient(http=http, api_key=TOKEN).prices(["USD/BRL"]) == {}


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="sem-data"),
        pytest.param({"data": []}, id="data-vazio"),
        pytest.param({"data": [{"currency": "USD"}]}, id="linha-sem-symbol"),
        pytest.param("texto", id="nem-objeto-é"),
    ],
)
async def test_twelve_data_search_survives_an_unexpected_body(payload: Any) -> None:
    http, _ = client_returning(payload)
    async with http:
        assert await TwelveDataClient(http=http, api_key=TOKEN).search("x") == []


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"erro": "manutenção"}, id="objeto-em-vez-de-lista"),
        pytest.param(["texto solto"], id="linha-que-não-é-objeto"),
        pytest.param([{"valor": "0.05"}], id="sem-data"),
        pytest.param([{"data": "17/09/2026"}], id="sem-valor"),
        pytest.param([{"data": "2026-09-17", "valor": "0.05"}], id="data-em-iso"),
    ],
)
async def test_the_bcb_treats_an_unexpected_body_as_no_rate(payload: Any) -> None:
    """A data do SGS é `DD/MM/AAAA`; um ISO aqui é mudança de formato, não dado."""
    http, _ = client_returning(payload)
    async with http:
        assert await BcbClient(http=http).latest(RateIndex.CDI) is None


async def test_a_monthly_window_with_a_broken_row_uses_the_readable_ones() -> None:
    """Uma leitura ilegível no meio da janela não pode zerar o índice do ano.

    Doze meses viram onze, e o expoente `12/n` projeta o que sobrou — melhor do
    que devolver `None` e deixar a renda fixa inteira sem render.
    """
    with_a_hole = [*BCB_IPCA_12[:5], {"data": "01/02/2026"}, *BCB_IPCA_12[6:]]
    http, _ = client_returning(with_a_hole)
    async with http:
        rate = await BcbClient(http=http).latest(RateIndex.IPCA)

    assert rate is not None
    assert Decimal("4") < rate.annual_percent < Decimal("6")


@pytest.mark.parametrize(
    ("time_value", "id_"),
    [
        pytest.param(None, "sem-horário", id="sem-horário"),
        pytest.param("ontem à tarde", "não-é-data", id="não-é-data"),
        pytest.param(1789739198, "número", id="número"),
    ],
)
async def test_a_quote_without_a_readable_instant_still_has_a_price(
    time_value: object, id_: str
) -> None:
    """Sem instante legível a cotação vale; quem a grava usa o relógio da rodada.

    Descartar o preço porque o carimbo veio estranho seria perder o dado que
    importa por causa do que não importa.
    """
    http, _ = client_returning(
        {
            "results": [
                {
                    "symbol": "PETR4",
                    "data": {"regularMarketPrice": 48.61, "regularMarketTime": time_value},
                }
            ]
        }
    )
    async with http:
        quote = await BrapiClient(http=http, token=TOKEN).quote("PETR4")

    assert quote is not None
    assert quote.price == Decimal("48.61")
    assert quote.quoted_at is None
