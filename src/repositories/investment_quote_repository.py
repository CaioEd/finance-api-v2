"""Acesso a dados do agendador de cotações — **irrestrito por dono, de propósito**.

Repositório separado do `InvestmentRepository` pela mesma razão que
`AdminUserRepository` é separado do `UserRepository`: as consultas daqui
atravessam todos os usuários, e misturá-las com as que impõem escopo deixaria uma
rota chamar a versão sem filtro por descuido.

**Nada em `api/routes/` depende deste módulo.** Quem o usa é
`jobs.investment_quotes`, que roda fora de requisição e não tem usuário.

A escolha central é atualizar posição por **`UPDATE` em lote**: cotado o ativo,
todas as posições nele valem `quantidade x preço` na mesma instrução — uma
instrução contra cem idas ao banco.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.investment import (
    Investment,
    InvestmentAsset,
    InvestmentRate,
    InvestmentType,
    RateIndex,
)

MONEY_PLACES = 2


def _stalest_first() -> Select[tuple[InvestmentAsset]]:
    """A fila do agendador: nunca cotado primeiro, depois o mais velho.

    `nulls_first` é explícito porque o default do Postgres para `ASC` é
    `NULLS LAST`, e o ativo recém-cadastrado ficaria no fim da fila.
    """
    return select(InvestmentAsset).order_by(
        InvestmentAsset.quoted_at.asc().nulls_first(), InvestmentAsset.symbol
    )


class InvestmentQuoteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --------------------------------------------------------------- catálogo

    async def stale_assets(
        self, *, types: Collection[InvestmentType], limit: int
    ) -> Sequence[InvestmentAsset]:
        """Os `limit` ativos mais desatualizados daqueles tipos.

        O teto é o orçamento do provedor, e a ordem garante que o que não coube
        nesta rodada seja o mais velho na próxima.
        """
        if not types or limit <= 0:
            return []
        statement = _stalest_first().where(InvestmentAsset.type.in_(types)).limit(limit)
        result = await self._session.scalars(statement)
        return result.all()

    async def has_positions(self, asset_id: UUID) -> bool:
        """Se alguém ainda tem esse ativo: o catálogo sobrevive a quem o povoou,
        e ativo sem dono não merece gastar crédito de cotação."""
        statement = select(Investment.id).where(Investment.asset_id == asset_id).limit(1)
        return (await self._session.scalars(statement)).first() is not None

    async def revalue_positions(
        self, asset_id: UUID, *, price_in_brl: Decimal, moment: datetime
    ) -> int:
        """Põe `quantidade x preço` em toda posição do ativo. Devolve quantas mudaram.

        `round(..., 2)` explícito: o produto tem oito casas, e o arredondamento
        implícito faria o centavo do Postgres discordar do centavo do SQLite.
        """
        statement = (
            update(Investment)
            .where(Investment.asset_id == asset_id, Investment.quantity.is_not(None))
            .values(
                current_value=func.round(Investment.quantity * price_in_brl, MONEY_PLACES),
                value_updated_at=moment,
            )
        )
        # `execute` é tipado como `Result`, mas um `UPDATE` devolve sempre um
        # `CursorResult` — é dele que sai `rowcount`, e o cast diz isso ao mypy
        # sem afrouxar o `--strict` do arquivo inteiro.
        result = cast("CursorResult[Any]", await self._session.execute(statement))
        return int(result.rowcount)

    # ------------------------------------------------------------ renda fixa

    async def accruable_positions(self, *, limit: int) -> Sequence[Investment]:
        """Posições de renda fixa a acruar, da mais atrasada para a menos.

        Só as que têm índice e data de aplicação: sem uma das duas não há o que
        capitalizar, e o índice parcial já resolve o filtro.
        """
        if limit <= 0:
            return []
        statement = (
            select(Investment)
            .where(
                Investment.rate_index.is_not(None),
                Investment.applied_on.is_not(None),
                Investment.current_value > 0,
            )
            .order_by(Investment.value_updated_at.asc().nulls_first(), Investment.id)
            .limit(limit)
        )
        result = await self._session.scalars(statement)
        return result.all()

    # ---------------------------------------------------------- taxas do BCB

    async def rates(self) -> dict[RateIndex, InvestmentRate]:
        """As últimas leituras guardadas, por índice."""
        result = await self._session.scalars(select(InvestmentRate))
        return {rate.index: rate for rate in result.all()}

    async def save_rate(
        self,
        index: RateIndex,
        *,
        annual_percent: Decimal,
        reference_date: date,
        fetched_at: datetime,
    ) -> None:
        """Grava a leitura do índice, substituindo a anterior.

        Lê e atualiza, em vez de `INSERT ... ON CONFLICT`: os upserts do Postgres
        e do SQLite têm importes diferentes, e o projeto não tem `if` de dialeto
        no código de aplicação.

        A sessão roda com `autoflush=False`, então este `get` só enxerga o que já
        está no banco. Basta, porque a rodada grava um índice uma vez e comita no
        fim. Com duas réplicas, uma pode perder a inserção — a etapa é idempotente
        e a rodada seguinte regrava o mesmo número.
        """
        existing = await self._session.get(InvestmentRate, index)
        if existing is None:
            self._session.add(
                InvestmentRate(
                    index=index,
                    annual_percent=annual_percent,
                    reference_date=reference_date,
                    fetched_at=fetched_at,
                )
            )
            return
        existing.annual_percent = annual_percent
        existing.reference_date = reference_date
        existing.fetched_at = fetched_at
