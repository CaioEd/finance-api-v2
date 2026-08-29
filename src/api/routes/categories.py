"""Rotas de categorias.

A listagem devolve as do sistema junto com as do usuário; o escopo vem do
repositório, e o endpoint não tem um `if` de dono para alguém esquecer.

A autenticação é declarada no router inteiro, não em cada rota.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from dependencies.auth import get_current_user
from dependencies.services import get_category_service
from models.category import CategoryKind
from models.user import User
from schemas.category import CategoryCreateIn, CategoryOut, CategoryUpdateIn
from services.category_service import CategoryService

router = APIRouter(
    prefix="/categories",
    tags=["categories"],
    dependencies=[Depends(get_current_user)],
    responses={401: {"description": "Ausência de token, token inválido ou expirado"}},
)

# Anotadas por causa do `responses` do FastAPI, cuja chave é `int | str`.
Responses = dict[int | str, dict[str, Any]]

NOT_FOUND: Responses = {404: {"description": "Categoria inexistente ou de outro usuário"}}
NOT_OWNED: Responses = {403: {"description": "Categoria do sistema: só um admin a altera"}}
NAME_TAKEN: Responses = {409: {"description": "Já existe uma categoria com este nome e tipo"}}


@router.get("", summary="Lista as categorias do sistema e as do próprio usuário")
async def list_categories(
    kind: CategoryKind | None = Query(default=None, description="Filtra por receita ou despesa"),
    user: User = Depends(get_current_user),
    service: CategoryService = Depends(get_category_service),
) -> list[CategoryOut]:
    categories = await service.list_visible(user, kind=kind)
    return [CategoryOut.model_validate(category) for category in categories]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Cria uma categoria do próprio usuário",
    responses=NAME_TAKEN,
)
async def create_category(
    data: CategoryCreateIn,
    user: User = Depends(get_current_user),
    service: CategoryService = Depends(get_category_service),
) -> CategoryOut:
    category = await service.create(user, data)
    return CategoryOut.model_validate(category)


@router.get("/{category_id}", summary="Detalha uma categoria visível", responses=NOT_FOUND)
async def read_category(
    category_id: UUID,
    user: User = Depends(get_current_user),
    service: CategoryService = Depends(get_category_service),
) -> CategoryOut:
    category = await service.get(user, category_id)
    return CategoryOut.model_validate(category)


@router.patch(
    "/{category_id}",
    summary="Atualiza uma categoria do próprio usuário",
    responses=NOT_FOUND | NOT_OWNED | NAME_TAKEN,
)
async def update_category(
    category_id: UUID,
    data: CategoryUpdateIn,
    user: User = Depends(get_current_user),
    service: CategoryService = Depends(get_category_service),
) -> CategoryOut:
    category = await service.update(user, category_id, data)
    return CategoryOut.model_validate(category)


@router.delete(
    "/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Exclui uma categoria do próprio usuário",
    responses=NOT_FOUND | NOT_OWNED,
)
async def delete_category(
    category_id: UUID,
    user: User = Depends(get_current_user),
    service: CategoryService = Depends(get_category_service),
) -> Response:
    await service.delete(user, category_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
