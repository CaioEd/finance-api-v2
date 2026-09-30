"""Limites de despesa e objetivos de investimentos."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from dependencies.auth import get_current_user
from dependencies.database import get_session
from models.investment_goal import InvestmentGoal
from models.spending_limit import SpendingLimit
from models.user import User
from repositories.planning_repository import PlanningRepository
from schemas.planning import GoalIn, GoalOut, GoalPatch, LimitIn, LimitOut, LimitPatch
from services.planning_service import PlanningService

router = APIRouter(dependencies=[Depends(get_current_user)])


def repository(session: AsyncSession = Depends(get_session)) -> PlanningRepository:
    return PlanningRepository(session)


def service(repo: PlanningRepository = Depends(repository)) -> PlanningService:
    return PlanningService(repo)


async def limit_out(repo: PlanningRepository, item: SpendingLimit, user_id: UUID) -> LimitOut:
    category = await repo.category(user_id, item.category_id) if item.category_id else None
    spent = await repo.spent(user_id, item.starts_on, item.ends_on, item.category_id)
    return LimitOut(
        id=item.id,
        name=item.name,
        amount=item.amount,
        starts_on=item.starts_on,
        ends_on=item.ends_on,
        category_id=item.category_id,
        category_name=category.name if category else None,
        spent=spent,
        exceeded=spent > item.amount,
        created_at=item.created_at,
    )


@router.get("/spending-limits", tags=["spending-limits"])
async def list_limits(
    user: User = Depends(get_current_user), repo: PlanningRepository = Depends(repository)
) -> list[LimitOut]:
    return [await limit_out(repo, item, user.id) for item in await repo.limits(user.id)]


@router.post("/spending-limits", tags=["spending-limits"], status_code=status.HTTP_201_CREATED)
async def create_limit(
    data: LimitIn,
    user: User = Depends(get_current_user),
    planner: PlanningService = Depends(service),
) -> LimitOut:
    return await limit_out(planner.repo, await planner.create_limit(user.id, data), user.id)


@router.patch("/spending-limits/{limit_id}", tags=["spending-limits"])
async def update_limit(
    limit_id: UUID,
    data: LimitPatch,
    user: User = Depends(get_current_user),
    planner: PlanningService = Depends(service),
) -> LimitOut:
    return await limit_out(
        planner.repo, await planner.update_limit(user.id, limit_id, data), user.id
    )


@router.delete("/spending-limits/{limit_id}", tags=["spending-limits"], status_code=204)
async def delete_limit(
    limit_id: UUID,
    user: User = Depends(get_current_user),
    planner: PlanningService = Depends(service),
) -> None:
    await planner.delete_limit(user.id, limit_id)


async def goal_out(repo: PlanningRepository, item: InvestmentGoal, user_id: UUID) -> GoalOut:
    investment = await repo.investment(user_id, item.investment_id) if item.investment_id else None
    current = investment.current_value if investment else await repo.portfolio_value(user_id)
    return GoalOut(
        id=item.id,
        name=item.name,
        target_amount=item.target_amount,
        target_on=item.target_on,
        investment_id=item.investment_id,
        investment_name=investment.name if investment else None,
        current_amount=current,
        progress_percent=round(float(current / item.target_amount * 100), 2),
        achieved=current >= item.target_amount,
        created_at=item.created_at,
    )


@router.get("/investment-goals", tags=["investment-goals"])
async def list_goals(
    user: User = Depends(get_current_user), repo: PlanningRepository = Depends(repository)
) -> list[GoalOut]:
    return [await goal_out(repo, item, user.id) for item in await repo.goals(user.id)]


@router.post("/investment-goals", tags=["investment-goals"], status_code=status.HTTP_201_CREATED)
async def create_goal(
    data: GoalIn,
    user: User = Depends(get_current_user),
    planner: PlanningService = Depends(service),
) -> GoalOut:
    return await goal_out(planner.repo, await planner.create_goal(user.id, data), user.id)


@router.patch("/investment-goals/{goal_id}", tags=["investment-goals"])
async def update_goal(
    goal_id: UUID,
    data: GoalPatch,
    user: User = Depends(get_current_user),
    planner: PlanningService = Depends(service),
) -> GoalOut:
    return await goal_out(planner.repo, await planner.update_goal(user.id, goal_id, data), user.id)


@router.delete("/investment-goals/{goal_id}", tags=["investment-goals"], status_code=204)
async def delete_goal(
    goal_id: UUID,
    user: User = Depends(get_current_user),
    planner: PlanningService = Depends(service),
) -> None:
    await planner.delete_goal(user.id, goal_id)
