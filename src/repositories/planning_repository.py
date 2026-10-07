"""Consultas de planejamento sempre restritas ao usuário autenticado."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.category import Category, CategoryKind
from models.investment import Investment
from models.investment_goal import InvestmentGoal
from models.spending_limit import SpendingLimit
from models.transaction import Transaction


class PlanningRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def limits(self, user_id: UUID) -> list[SpendingLimit]:
        return list(
            (
                await self.session.scalars(
                    select(SpendingLimit)
                    .where(SpendingLimit.user_id == user_id)
                    .order_by(SpendingLimit.starts_on.desc())
                )
            ).all()
        )

    async def limit(self, user_id: UUID, resource_id: UUID) -> SpendingLimit | None:
        result = await self.session.scalars(
            select(SpendingLimit).where(
                SpendingLimit.user_id == user_id, SpendingLimit.id == resource_id
            )
        )
        return result.first()

    async def category(self, user_id: UUID, category_id: UUID) -> Category | None:
        result = await self.session.scalars(
            select(Category).where(
                Category.id == category_id,
                (Category.user_id == user_id) | (Category.user_id.is_(None)),
                Category.kind == CategoryKind.EXPENSE,
            )
        )
        return result.first()

    async def spent(
        self, user_id: UUID, first: date, last: date, category_id: UUID | None
    ) -> Decimal:
        statement = (
            select(func.coalesce(func.sum(Transaction.amount), 0))
            .join(Category, Category.id == Transaction.category_id)
            .where(
                Transaction.user_id == user_id,
                Category.kind == CategoryKind.EXPENSE,
                Transaction.occurred_on >= first,
                Transaction.occurred_on <= last,
            )
        )
        if category_id is not None:
            statement = statement.where(Transaction.category_id == category_id)
        return Decimal(await self.session.scalar(statement) or 0)

    async def goals(self, user_id: UUID) -> list[InvestmentGoal]:
        return list(
            (
                await self.session.scalars(
                    select(InvestmentGoal)
                    .where(or_(InvestmentGoal.user_id == user_id, InvestmentGoal.user_id.is_(None)))
                    .order_by(InvestmentGoal.created_at.desc())
                )
            ).all()
        )

    async def goal(self, user_id: UUID, resource_id: UUID) -> InvestmentGoal | None:
        result = await self.session.scalars(
            select(InvestmentGoal).where(
                InvestmentGoal.user_id == user_id, InvestmentGoal.id == resource_id
            )
        )
        return result.first()

    async def global_goals(self) -> list[InvestmentGoal]:
        result = await self.session.scalars(
            select(InvestmentGoal)
            .where(InvestmentGoal.user_id.is_(None))
            .order_by(InvestmentGoal.created_at.desc())
        )
        return list(result.all())

    async def global_goal(self, resource_id: UUID) -> InvestmentGoal | None:
        result = await self.session.scalars(
            select(InvestmentGoal).where(
                InvestmentGoal.id == resource_id, InvestmentGoal.user_id.is_(None)
            )
        )
        return result.first()

    async def investment(self, user_id: UUID, investment_id: UUID) -> Investment | None:
        result = await self.session.scalars(
            select(Investment).where(Investment.user_id == user_id, Investment.id == investment_id)
        )
        return result.first()

    async def portfolio_value(self, user_id: UUID) -> Decimal:
        value = await self.session.scalar(
            select(func.coalesce(func.sum(Investment.current_value), 0)).where(
                Investment.user_id == user_id
            )
        )
        return Decimal(value or 0)
