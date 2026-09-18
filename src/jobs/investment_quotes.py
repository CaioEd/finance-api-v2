"""Corrige o valor dos investimentos de todos os usuários, de 15 em 15 minutos.

Uma rodada faz quatro coisas, nesta ordem e **cada uma no seu commit**:

1. lê os índices do Banco Central (grátis, quatro chamadas, só se envelheceram);
2. acrua a renda fixa — aritmética local, sem gastar crédito de provedor;
3. cota as ações brasileiras na BRAPI, **uma requisição por papel**;
4. cota ações americanas e cripto na Twelve Data, em lote, e converte o dólar.

Commits separados porque as etapas são independentes: a Twelve Data fora do ar
não pode desfazer o acrual da renda fixa que já foi calculado. Uma etapa que
falha só vai para o log, e a rodada seguinte tenta de novo — `current_value`
continua valendo o que valia, que é a resposta certa para "não sei quanto vale
agora".

## O orçamento não é detalhe

Os planos gratuitos decidem o desenho (medições em `docs/investimentos.md`):
a BRAPI aceita **um ativo por requisição**, 20 por minuto; a Twelve Data dá
**8 créditos por minuto e 800 por dia**, um por símbolo, e o lote que estoura o
limite gasta os créditos sem devolver nada. Com 96 rodadas por dia, sobram cerca
de oito créditos de Twelve Data por rodada.

Daí duas regras:

- **a fila é por idade da cotação** (`stale_assets`), então o teto por rodada
  atrasa um ativo, nunca o abandona: o que não coube é o mais velho da próxima;
- **só se cota o que alguém tem.** Ativo que ficou no catálogo sem dono nenhum
  é pulado — o catálogo sobrevive à posição, mas não merece crédito por isso.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from core.clock import Clock
from core.database import Database
from models.investment import (
    Investment,
    InvestmentAsset,
    InvestmentType,
    RateIndex,
)
from providers.base import MarketDataUnavailableError, Quote
from providers.bcb import SERIES, BcbClient
from providers.brapi import BrapiClient
from providers.twelve_data import MAX_SYMBOLS_PER_REQUEST, TwelveDataClient, crypto_pair
from repositories.investment_quote_repository import InvestmentQuoteRepository
from services.fixed_income import accrue, days_between, effective_annual_percent

logger = logging.getLogger(__name__)

BRL = "BRL"
USD = "USD"

TWELVE_DATA_TYPES = (InvestmentType.US_STOCK, InvestmentType.CRYPTO)
"""Cripto é plano pago na BRAPI, e ação americana ela não cobre."""


@dataclass(frozen=True, slots=True)
class QuoteBudget:
    """Quantas consultas uma rodada pode gastar em cada provedor.

    Vem da configuração, e não de constante, porque quem paga um plano melhor
    muda o número sem mexer no código — e quem não paga precisa que o default
    caiba nos 800 créditos diários.
    """

    brapi_symbols: int
    twelve_data_symbols: int
    accrual_batch: int
    rate_max_age: timedelta


@dataclass(frozen=True, slots=True)
class MarketProviders:
    """Os clientes disponíveis. `None` é provedor sem credencial configurada."""

    brapi: BrapiClient | None = None
    twelve_data: TwelveDataClient | None = None
    bcb: BcbClient | None = None


@dataclass(frozen=True, slots=True)
class RefreshReport:
    """O que a rodada fez. Devolvido para teste e log, não para a API."""

    rates: int = 0
    accrued: int = 0
    quoted: int = 0
    positions: int = 0

    def __add__(self, other: RefreshReport) -> RefreshReport:
        return RefreshReport(
            rates=self.rates + other.rates,
            accrued=self.accrued + other.accrued,
            quoted=self.quoted + other.quoted,
            positions=self.positions + other.positions,
        )

    @property
    def touched(self) -> bool:
        return bool(self.rates or self.accrued or self.quoted)


async def refresh_investment_values(
    database: Database,
    clock: Clock,
    providers: MarketProviders,
    budget: QuoteBudget,
) -> RefreshReport:
    """Uma rodada inteira. Etapa que falha vira log; as outras seguem."""
    report = RefreshReport()
    for stage in (_refresh_rates, _accrue_fixed_income, _quote_brazilian_stocks, _quote_abroad):
        try:
            report += await stage(database, clock, providers, budget)
        except MarketDataUnavailableError:
            logger.warning(
                "provedor indisponível em %s; a próxima rodada tenta de novo", stage.__name__
            )
        except Exception:
            logger.exception("etapa %s falhou", stage.__name__)
    if report.touched:
        logger.info(
            "investimentos: %d índice(s), %d posição(ões) acruada(s), %d ativo(s) cotado(s), "
            "%d posição(ões) revalorizada(s)",
            report.rates,
            report.accrued,
            report.quoted,
            report.positions,
        )
    return report


# ------------------------------------------------------------ 1. os índices


async def _refresh_rates(
    database: Database, clock: Clock, providers: MarketProviders, budget: QuoteBudget
) -> RefreshReport:
    """Lê CDI, SELIC, IPCA e poupança — só os que envelheceram.

    O SGS publica uma vez por dia; buscar os quatro a cada 15 minutos seriam 384
    requisições diárias para quatro números que não mudaram. O teto de idade é
    configurável e o default é meio dia.
    """
    if providers.bcb is None:
        return RefreshReport()
    now = clock.now_utc()
    async with database.sessionmaker() as session:
        repository = InvestmentQuoteRepository(session)
        known = await repository.rates()
        stale = [
            index
            for index in SERIES
            if index not in known or now - known[index].fetched_at >= budget.rate_max_age
        ]
        if not stale:
            return RefreshReport()
        saved = 0
        for index in stale:
            rate = await providers.bcb.latest(index)
            if rate is None:
                logger.warning("série do Banco Central para %s veio vazia", index)
                continue
            await repository.save_rate(
                index,
                annual_percent=rate.annual_percent,
                reference_date=rate.reference_date,
                fetched_at=now,
            )
            saved += 1
        await session.commit()
    return RefreshReport(rates=saved)


# --------------------------------------------------------- 2. a renda fixa


async def _accrue_fixed_income(
    database: Database, clock: Clock, providers: MarketProviders, budget: QuoteBudget
) -> RefreshReport:
    """Capitaliza cada posição pelos dias corridos desde a última correção.

    Nenhuma chamada externa: as taxas já estão na tabela. O que manda a posição
    não render é não saber o índice — nunca um zero, que afirmaria que o
    dinheiro parou.
    """
    today = clock.today()
    now = clock.now_utc()
    async with database.sessionmaker() as session:
        repository = InvestmentQuoteRepository(session)
        rates = await repository.rates()
        positions = await repository.accruable_positions(limit=budget.accrual_batch)
        annual = {index: rate.annual_percent for index, rate in rates.items()}
        accrued = sum(
            1 for position in positions if _accrue_one(position, annual, today=today, now=now)
        )
        if accrued:
            await session.commit()
    return RefreshReport(accrued=accrued)


def _accrue_one(
    position: Investment,
    rates: dict[RateIndex, Decimal],
    *,
    today: date,
    now: datetime,
) -> bool:
    """Devolve se a posição mudou de valor. Ver `services.fixed_income`."""
    if position.rate_index is None or position.rate_percent is None:
        return False
    annual = effective_annual_percent(
        position.rate_index, position.rate_percent, rates.get(position.rate_index)
    )
    if annual is None:
        return False
    last = position.value_updated_at.date() if position.value_updated_at else position.applied_on
    if last is None:
        return False
    days = days_between(last, today)
    if days <= 0:
        return False
    updated = accrue(position.current_value, annual, days)
    if updated == position.current_value:
        # Valor pequeno demais para mexer no centavo: não marcar como atualizado
        # deixa o juro se acumular até virar um centavo, em vez de sumir todo dia.
        return False
    position.current_value = updated
    position.value_updated_at = now
    return True


# ---------------------------------------------------- 3. as ações brasileiras


async def _quote_brazilian_stocks(
    database: Database, clock: Clock, providers: MarketProviders, budget: QuoteBudget
) -> RefreshReport:
    """Uma requisição por papel — é o que o plano gratuito da BRAPI permite."""
    if providers.brapi is None:
        return RefreshReport()
    async with database.sessionmaker() as session:
        repository = InvestmentQuoteRepository(session)
        assets = await repository.stale_assets(
            types=(InvestmentType.BR_STOCK,), limit=budget.brapi_symbols
        )
        report = RefreshReport()
        for asset in assets:
            if not await repository.has_positions(asset.id):
                continue
            quote = await providers.brapi.quote(asset.symbol)
            if quote is None:
                logger.info("BRAPI não conhece %s; a posição segue pelo último valor", asset.symbol)
                continue
            report += await _apply(repository, asset, quote, rate=Decimal(1), clock=clock)
        await session.commit()
    return report


# --------------------------------------------- 4. o que é cotado em dólar


async def _quote_abroad(
    database: Database, clock: Clock, providers: MarketProviders, budget: QuoteBudget
) -> RefreshReport:
    """Ações americanas e cripto, em lote, mais o câmbio da rodada.

    O `USD/BRL` é **um** crédito para a rodada inteira, e não um por ativo: é
    a mesma taxa para todos, e cotá-la por posição consumiria o orçamento
    inteiro em conversão.
    """
    if providers.twelve_data is None or budget.twelve_data_symbols <= 0:
        return RefreshReport()
    async with database.sessionmaker() as session:
        repository = InvestmentQuoteRepository(session)
        assets = [
            asset
            for asset in await repository.stale_assets(
                types=TWELVE_DATA_TYPES, limit=budget.twelve_data_symbols
            )
            if await repository.has_positions(asset.id)
        ]
        if not assets:
            return RefreshReport()

        fx = await providers.twelve_data.usd_brl()
        if fx is None:
            logger.warning("câmbio USD/BRL indisponível; as cotações em dólar ficam para a próxima")
            return RefreshReport()

        query_symbols = {_query_symbol(asset): asset for asset in assets}
        prices = await _prices_in_batches(providers.twelve_data, list(query_symbols))

        report = RefreshReport()
        for query, asset in query_symbols.items():
            price = prices.get(query)
            if price is None:
                logger.info("Twelve Data não cotou %s nesta rodada", query)
                continue
            quote = Quote(symbol=asset.symbol, price=price, currency=USD)
            report += await _apply(repository, asset, quote, rate=fx, clock=clock)
        await session.commit()
    return report


def _query_symbol(asset: InvestmentAsset) -> str:
    """O símbolo de consulta do provedor — `BTC` vira `BTC/USD`; `AAPL` é `AAPL`."""
    return crypto_pair(asset.symbol) if asset.type is InvestmentType.CRYPTO else asset.symbol


async def _prices_in_batches(client: TwelveDataClient, symbols: list[str]) -> dict[str, Decimal]:
    """Quebra em lotes de no máximo 8: acima disso a resposta é 429 **e gasta crédito**."""
    prices: dict[str, Decimal] = {}
    for start in range(0, len(symbols), MAX_SYMBOLS_PER_REQUEST):
        prices |= await client.prices(symbols[start : start + MAX_SYMBOLS_PER_REQUEST])
    return prices


# ------------------------------------------------------------------- comum


async def _apply(
    repository: InvestmentQuoteRepository,
    asset: InvestmentAsset,
    quote: Quote,
    *,
    rate: Decimal,
    clock: Clock,
) -> RefreshReport:
    """Grava a cotação no catálogo e revaloriza as posições dela, em BRL.

    `rate` é quantos reais vale uma unidade da moeda do ativo: 1 para quem já é
    cotado em real, e o câmbio da rodada para quem é cotado em dólar.
    """
    now = clock.now_utc()
    asset.price = quote.price
    asset.currency = quote.currency
    asset.previous_close = quote.previous_close or asset.previous_close
    asset.change_percent = quote.change_percent
    asset.quoted_at = quote.quoted_at or now
    if quote.name and asset.name == asset.symbol:
        # Só quando o nome ainda é o placeholder do cadastro: quem renomeou o
        # ativo não pode vê-lo voltar ao nome do provedor a cada rodada.
        asset.name = quote.name[:120]
    if quote.logo_url:
        asset.logo_url = quote.logo_url
    changed = await repository.revalue_positions(
        asset.id, price_in_brl=quote.price * rate, moment=now
    )
    return RefreshReport(quoted=1, positions=changed)
