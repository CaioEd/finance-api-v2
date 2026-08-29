"""Rotas administrativas de usuários: criar, listar, atualizar e excluir.

Este é o CRUD que na versão antiga qualquer autenticado alcançava. Aqui ele
existe atrás de `require_role(Role.ADMIN)` **declarado no router inteiro**, e
não rota a rota: esquecer a declaração numa rota nova fecha a rota, em vez de
abri-la para o mundo.

A conta da própria pessoa não se administra por aqui — `/users/me` é o caminho
para isso, e ele pede a senha antes de excluir.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from dependencies.auth import get_current_user, require_role
from dependencies.services import get_admin_user_service
from models.user import Role, User
from repositories.admin_user_repository import UserFilters
from schemas.user import AdminUserCreateIn, AdminUserUpdateIn, UserOut, UserPageOut
from services.admin_user_service import AdminUserService

router = APIRouter(
    prefix="/admin/users",
    tags=["admin"],
    dependencies=[Depends(require_role(Role.ADMIN))],
    responses={
        401: {"description": "Ausência de token, token inválido ou expirado"},
        403: {"description": "Autenticado, mas sem papel de administrador"},
    },
)


@router.get("", summary="Lista usuários, com filtro e paginação")
async def list_users(
    service: AdminUserService = Depends(get_admin_user_service),
    limit: int = Query(50, ge=1, le=100, description="Tamanho da página"),
    offset: int = Query(0, ge=0),
    role: Role | None = Query(None, description="Filtra pelo papel"),
    is_active: bool | None = Query(None, description="Filtra por conta ativa ou desativada"),
    q: str | None = Query(
        None, min_length=1, max_length=100, description="Busca em e-mail, nome de usuário e nome"
    ),
) -> UserPageOut:
    page = await service.list_users(
        filters=UserFilters(role=role, is_active=is_active, search=q),
        limit=limit,
        offset=offset,
    )
    return UserPageOut(
        items=[UserOut.model_validate(user) for user in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Cria um usuário, com papel e estado à escolha",
    responses={409: {"description": "E-mail ou nome de usuário já em uso"}},
)
async def create_user(
    data: AdminUserCreateIn,
    service: AdminUserService = Depends(get_admin_user_service),
) -> UserOut:
    created = await service.create_user(data)
    return UserOut.model_validate(created)


@router.patch(
    "/{user_id}",
    summary="Atualiza um usuário, inclusive papel e estado",
    responses={
        403: {"description": "O alvo é a conta do próprio administrador"},
        404: {"description": "Usuário inexistente"},
        409: {"description": "E-mail ou nome de usuário já em uso"},
    },
)
async def update_user(
    user_id: UUID,
    data: AdminUserUpdateIn,
    actor: User = Depends(get_current_user),
    service: AdminUserService = Depends(get_admin_user_service),
) -> UserOut:
    updated = await service.update_user(user_id, data, actor=actor)
    return UserOut.model_validate(updated)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Exclui um usuário e tudo que pende dele",
    responses={
        403: {"description": "O alvo é a conta do próprio administrador"},
        404: {"description": "Usuário inexistente"},
    },
)
async def delete_user(
    user_id: UUID,
    actor: User = Depends(get_current_user),
    service: AdminUserService = Depends(get_admin_user_service),
) -> Response:
    await service.delete_user(user_id, actor=actor)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
