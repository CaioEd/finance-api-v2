"""Acesso a dados de investimentos.

O escopo por dono é imposto **aqui**, como nos demais repositórios: toda
consulta de posição nasce de `Investment.user_id == user_id`, e não existe
método que alcance a posição de outra pessoa.

O catálogo (`investment_assets`) é a exceção deliberada, e é global de
propósito: a cotação da PETR4 não é de ninguém. Nenhum método dele recebe
`user_id` porque não há o que filtrar — e nenhum devolve posição, então não há
como vazar por ele o que outra pessoa tem.

Toda consulta de posição traz o ativo junto (`contains_eager`) num **LEFT**
JOIN: renda fixa não tem ativo, e um JOIN interno sumiria com metade da
carteira da listagem. O model declara `lazy="raise"` justamente para que faltar
o eager load falhe aqui, e não no meio da serialização.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ColumnElement, Numeric, Select, func, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from core.errors import DomainError, InvalidInvestmentAssetError, UnprocessableError
from models.investment import (
    CLASS_OF_TYPE,
    FK_INVESTMENT_ASSET,
    MONEY_DECIMAL_PLACES,
    MONEY_MAX_DIGITS,
    Investment,
    InvestmentAsset,
    InvestmentClass,
    InvestmentType,
)

ZERO = Decimal("0.00")

MONEY = Numeric(MONEY_MAX_DIGITS, MONEY_DECIMAL_PLACES)
"""O mesmo tipo da coluna, para o zero do `coalesce` não virar `INTEGER`."""


@dataclass(frozen=True, slots=True)
class InvestmentFilters:
    """O recorte pedido na listagem. Ausente (`None`) é "não filtre por isto"."""

    investment_class: InvestmentClass | None = None
    type: InvestmentType | None = None


@dataclass(frozen=True, slots=True)
class TypeTotals:
    """Quanto um tipo de investimento soma na carteira de alguém."""

    type: InvestmentType
    value: Decimal
    invested: Decimal
    count: int
    value_updated_at: datetime | None


def _conditions(user_id: UUID, filters: InvestmentFilters) -> list[ColumnElement[bool]]:
    """Uma função só para os dois lados da paginação.

    A contagem precisa exatamente do mesmo `WHERE` da listagem; duplicar a
    construção é como o `total` acaba mentindo sobre o número de páginas.
    """
    conditions: list[ColumnElement[bool]] = [Investment.user_id == user_id]
    if filters.investment_class is not None:
        # A classe não é coluna — é derivada do tipo (ver `models.investment`),
        # e filtrar por ela é enumerar os tipos daquela metade.
        members = [
            kind for kind, family in CLASS_OF_TYPE.items() if family is filters.investment_class
        ]
        conditions.append(Investment.type.in_(members))
    if filters.type is not None:
        conditions.append(Investment.type == filters.type)
    return conditions


def _with_asset() -> Select[tuple[Investment]]:
    """`SELECT` de posição com o ativo já carregado, quando houver um."""
    return (
        select(Investment)
        .outerjoin(InvestmentAsset, Investment.asset_id == InvestmentAsset.id)
        .options(contains_eager(Investment.asset))
    )


class InvestmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------- posições

    async def list_investments(
        self, user_id: UUID, *, filters: InvestmentFilters, limit: int, offset: int
    ) -> Sequence[Investment]:
        statement = (
            _with_asset()
            .where(*_conditions(user_id, filters))
            # O desempate por id mantém a paginação estável: sem ele, duas
            # posições de mesmo valor podem trocar de página entre duas
            # requisições e uma delas nunca ser vista.
            .order_by(Investment.current_value.desc(), Investment.id)
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.scalars(statement)
        return result.all()

    async def count_investments(self, user_id: UUID, *, filters: InvestmentFilters) -> int:
        statement = (
            select(func.count()).select_from(Investment).where(*_conditions(user_id, filters))
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def get_owned(
        self, investment_id: UUID, user_id: UUID, *, lock: bool = False
    ) -> Investment | None:
        """Devolve `None` para posição de terceiro — que a borda traduz em 404.

        Não é `session.get`: aquele traria a linha de qualquer dono, e o escopo
        passaria a depender de quem chamou lembrar de conferir.

        `lock=True` trava só a posição, até o commit: dois aportes simultâneos
        na mesma posição não podem calcular o preço médio sobre a mesma
        quantidade antiga e gravar um por cima do outro.
        """
        statement = _with_asset().where(
            Investment.id == investment_id, Investment.user_id == user_id
        )
        if lock:
            statement = statement.with_for_update(of=Investment)
        result = await self._session.scalars(statement)
        return result.first()

    def add(self, investment: Investment) -> None:
        self._session.add(investment)

    async def delete(self, investment: Investment) -> None:
        await self._session.delete(investment)

    async def totals_by_type(self, user_id: UUID) -> list[TypeTotals]:
        """Uma varredura para a tela inteira de resumo.

        Agrupa por tipo e não por classe: a classe se obtém somando os tipos
        dela em Python, enquanto o caminho contrário exigiria uma segunda
        consulta sobre exatamente as mesmas linhas — que ainda poderia observar
        um estado diferente se o agendador gravasse entre as duas.
        """
        statement = (
            select(
                Investment.type,
                func.coalesce(func.sum(Investment.current_value), literal(ZERO, MONEY)),
                func.coalesce(func.sum(Investment.invested_amount), literal(ZERO, MONEY)),
                func.count(),
                func.max(Investment.value_updated_at),
            )
            .where(Investment.user_id == user_id)
            .group_by(Investment.type)
        )
        rows = (await self._session.execute(statement)).all()
        return [
            TypeTotals(
                type=row[0],
                value=row[1],
                invested=row[2],
                count=int(row[3]),
                value_updated_at=row[4],
            )
            for row in rows
        ]

    # -------------------------------------------------------------- catálogo

    async def get_asset(self, kind: InvestmentType, symbol: str) -> InvestmentAsset | None:
        """O ativo do catálogo, comparado sem caixa — como o índice único."""
        statement = select(InvestmentAsset).where(
            InvestmentAsset.type == kind,
            func.upper(InvestmentAsset.symbol) == symbol.upper(),
        )
        result = await self._session.scalars(statement)
        return result.first()

    def add_asset(self, asset: InvestmentAsset) -> None:
        self._session.add(asset)


def translate_integrity_error(exc: IntegrityError) -> DomainError:
    """Traduz a violação da FK do ativo, vista de quem grava a posição.

    O serviço já resolveu o ativo antes de gravar, então chegar aqui significa
    que ele sumiu entre a resolução e o INSERT. Quem decide é o banco, porque
    um SELECT prévio sempre terá essa janela — a checagem existe para a recusa
    ser legível, não para ser autoridade.
    """
    detail = str(exc.orig)
    if FK_INVESTMENT_ASSET in detail:
        return InvalidInvestmentAssetError()
    return UnprocessableError()
