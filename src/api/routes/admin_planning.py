"""Administração de categorias e objetivos visíveis para todos."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from api.routes.planning import goal_out, repository, service
from core.errors import NotFoundError
from dependencies.auth import get_current_user, require_role
from dependencies.services import get_category_service
from models.user import Role, User
from repositories.planning_repository import PlanningRepository
from schemas.category import CategoryCreateIn, CategoryOut, CategoryUpdateIn
from schemas.planning import GlobalGoalIn, GlobalGoalPatch, GoalOut
from services.category_service import CategoryService
from services.planning_service import PlanningService

router = APIRouter(
    prefix="/admin", tags=["admin"], dependencies=[Depends(require_role(Role.ADMIN))]
)


@router.get("/categories")
async def list_categories(
    user: User = Depends(get_current_user),
    categories: CategoryService = Depends(get_category_service),
) -> list[CategoryOut]:
    return [
        CategoryOut.model_validate(item)
        for item in await categories.list_visible(user)
        if item.is_global
    ]


@router.post("/categories", status_code=status.HTTP_201_CREATED)
async def create_category(
    data: CategoryCreateIn,
    categories: CategoryService = Depends(get_category_service),
) -> CategoryOut:
    return CategoryOut.model_validate(await categories.create_global(data))


@router.patch("/categories/{category_id}")
async def update_category(
    category_id: UUID,
    data: CategoryUpdateIn,
    user: User = Depends(get_current_user),
    categories: CategoryService = Depends(get_category_service),
) -> CategoryOut:
    item = await categories.get(user, category_id)
    if not item.is_global:
        raise NotFoundError()
    return CategoryOut.model_validate(await categories.update(user, category_id, data))


@router.delete("/categories/{category_id}", status_code=204)
async def delete_category(
    category_id: UUID,
    user: User = Depends(get_current_user),
    categories: CategoryService = Depends(get_category_service),
) -> Response:
    item = await categories.get(user, category_id)
    if not item.is_global:
        raise NotFoundError()
    await categories.delete(user, category_id)
    return Response(status_code=204)


@router.get("/investment-goals")
async def list_goals(repo: PlanningRepository = Depends(repository)) -> list[GoalOut]:
    # O painel mostra o modelo. O progresso é calculado para cada usuário na rota comum.
    return [
        GoalOut(
            id=item.id,
            is_global=True,
            name=item.name,
            target_amount=item.target_amount,
            target_on=item.target_on,
            investment_id=None,
            investment_name=None,
            current_amount=0,
            progress_percent=0,
            achieved=False,
            created_at=item.created_at,
        )
        for item in await repo.global_goals()
    ]


@router.post("/investment-goals", status_code=status.HTTP_201_CREATED)
async def create_goal(
    data: GlobalGoalIn,
    user: User = Depends(get_current_user),
    planner: PlanningService = Depends(service),
) -> GoalOut:
    return await goal_out(planner.repo, await planner.create_global_goal(data), user.id)


@router.patch("/investment-goals/{goal_id}")
async def update_goal(
    goal_id: UUID,
    data: GlobalGoalPatch,
    user: User = Depends(get_current_user),
    planner: PlanningService = Depends(service),
) -> GoalOut:
    return await goal_out(planner.repo, await planner.update_global_goal(goal_id, data), user.id)


@router.delete("/investment-goals/{goal_id}", status_code=204)
async def delete_goal(goal_id: UUID, planner: PlanningService = Depends(service)) -> Response:
    await planner.delete_global_goal(goal_id)
    return Response(status_code=204)
