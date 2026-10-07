"""Regras de criação e alteração de orçamentos e objetivos."""

from __future__ import annotations

from uuid import UUID

from core.errors import NotFoundError, UnprocessableError
from models.investment_goal import InvestmentGoal
from models.spending_limit import SpendingLimit
from repositories.planning_repository import PlanningRepository
from schemas.planning import GlobalGoalIn, GlobalGoalPatch, GoalIn, GoalPatch, LimitIn, LimitPatch


class PlanningService:
    def __init__(self, repo: PlanningRepository) -> None:
        self.repo = repo

    async def validate_limit(self, user_id: UUID, item: SpendingLimit) -> None:
        if item.starts_on > item.ends_on:
            raise UnprocessableError("A data inicial deve ser anterior ou igual à final.")
        if item.category_id and not await self.repo.category(user_id, item.category_id):
            raise UnprocessableError("Selecione uma categoria de despesa disponível.")

    async def create_limit(self, user_id: UUID, data: LimitIn) -> SpendingLimit:
        item = SpendingLimit(user_id=user_id, **data.model_dump())
        await self.validate_limit(user_id, item)
        self.repo.session.add(item)
        await self.repo.session.commit()
        await self.repo.session.refresh(item)
        return item

    async def update_limit(self, user_id: UUID, limit_id: UUID, data: LimitPatch) -> SpendingLimit:
        item = await self.repo.limit(user_id, limit_id)
        if item is None:
            raise NotFoundError()
        for key, value in data.changes().items():
            if key == "clear_category":
                if value:
                    item.category_id = None
            else:
                setattr(item, key, value)
        await self.validate_limit(user_id, item)
        await self.repo.session.commit()
        await self.repo.session.refresh(item)
        return item

    async def delete_limit(self, user_id: UUID, limit_id: UUID) -> None:
        item = await self.repo.limit(user_id, limit_id)
        if item is None:
            raise NotFoundError()
        await self.repo.session.delete(item)
        await self.repo.session.commit()

    async def validate_goal(self, user_id: UUID, item: InvestmentGoal) -> None:
        if item.investment_id and not await self.repo.investment(user_id, item.investment_id):
            raise UnprocessableError("Selecione um investimento disponível.")

    async def create_goal(self, user_id: UUID, data: GoalIn) -> InvestmentGoal:
        item = InvestmentGoal(user_id=user_id, **data.model_dump())
        await self.validate_goal(user_id, item)
        self.repo.session.add(item)
        await self.repo.session.commit()
        await self.repo.session.refresh(item)
        return item

    async def update_goal(self, user_id: UUID, goal_id: UUID, data: GoalPatch) -> InvestmentGoal:
        item = await self.repo.goal(user_id, goal_id)
        if item is None:
            raise NotFoundError()
        for key, value in data.changes().items():
            if key == "clear_investment":
                if value:
                    item.investment_id = None
            elif key == "clear_target_on":
                if value:
                    item.target_on = None
            else:
                setattr(item, key, value)
        await self.validate_goal(user_id, item)
        await self.repo.session.commit()
        await self.repo.session.refresh(item)
        return item

    async def delete_goal(self, user_id: UUID, goal_id: UUID) -> None:
        item = await self.repo.goal(user_id, goal_id)
        if item is None:
            raise NotFoundError()
        await self.repo.session.delete(item)
        await self.repo.session.commit()

    async def create_global_goal(self, data: GlobalGoalIn) -> InvestmentGoal:
        item = InvestmentGoal(user_id=None, **data.model_dump())
        self.repo.session.add(item)
        await self.repo.session.commit()
        await self.repo.session.refresh(item)
        return item

    async def update_global_goal(self, goal_id: UUID, data: GlobalGoalPatch) -> InvestmentGoal:
        item = await self.repo.global_goal(goal_id)
        if item is None:
            raise NotFoundError()
        for key, value in data.changes().items():
            if key == "clear_target_on":
                if value:
                    item.target_on = None
            else:
                setattr(item, key, value)
        await self.repo.session.commit()
        await self.repo.session.refresh(item)
        return item

    async def delete_global_goal(self, goal_id: UUID) -> None:
        item = await self.repo.global_goal(goal_id)
        if item is None:
            raise NotFoundError()
        await self.repo.session.delete(item)
        await self.repo.session.commit()
