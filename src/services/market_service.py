"""Busca de ativos: qual provedor responde por qual tipo.

A regra inteira deste serviço é um roteamento, e ele existe para que a rota não
o faça: ação brasileira é BRAPI, ação americana é Twelve Data, e cripto não é
busca nenhuma — é uma lista fechada de nove moedas. O que devolve é `AssetHit`,
que ainda não é posição de ninguém.

**Cripto não vem com preço**: cotar as nove moedas para montar uma lista de
escolha custaria mais que os oito créditos por minuto do plano gratuito.

**Provedor sem credencial é provedor ausente**, não erro: `BRAPI_TOKEN` vazio faz
a busca devolver lista vazia, e a aplicação sobe do mesmo jeito.
"""

from __future__ import annotations

from dataclasses import dataclass

from models.investment import InvestmentType
from providers.base import AssetHit
from providers.brapi import BrapiClient
from providers.twelve_data import CRYPTO_QUOTE_CURRENCY, CRYPTOCURRENCIES, TwelveDataClient

SEARCH_LIMIT = 10


@dataclass(frozen=True, slots=True)
class MarketService:
    brapi: BrapiClient | None
    twelve_data: TwelveDataClient | None

    async def search_assets(
        self, kind: InvestmentType, query: str, *, limit: int = SEARCH_LIMIT
    ) -> list[AssetHit]:
        """Os ativos daquele tipo que casam com o termo.

        Tipo de renda fixa devolve lista vazia, e não erro: a rota já recusa isso
        antes de chegar aqui, e a lista vazia é só a rede de baixo.
        """
        if kind is InvestmentType.CRYPTO:
            return _cryptocurrencies_matching(query, limit=limit)
        if kind is InvestmentType.BR_STOCK and self.brapi is not None:
            return await self.brapi.search(query, limit=limit)
        if kind is InvestmentType.US_STOCK and self.twelve_data is not None:
            return await self.twelve_data.search(query, limit=limit)
        return []


def _cryptocurrencies_matching(query: str, *, limit: int) -> list[AssetHit]:
    """As nove moedas, filtradas pela abreviação ou pelo nome. Termo vazio
    devolve a lista inteira, que é o seletor recém-aberto."""
    term = query.strip().upper()
    hits = [
        AssetHit(symbol=symbol, name=name, currency=CRYPTO_QUOTE_CURRENCY)
        for symbol, name in CRYPTOCURRENCIES.items()
        if not term or term in symbol or term in name.upper()
    ]
    return hits[:limit]
