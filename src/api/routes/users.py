"""Rotas de usuário.

Só existe `/users/me`. O CRUD de terceiros — que na versão antiga qualquer
autenticado conseguia usar para editar e apagar qualquer conta — vai para
`api/routes/admin_users.py` sob `require_role(ADMIN)`, na fase 6.

A autenticação é declarada no router inteiro, não em cada rota.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from dependencies.auth import get_current_user
from dependencies.services import get_user_service
from models.user import User
from schemas.user import AccountDeleteIn, PasswordChangeIn, UserOut, UserUpdateIn
from services.user_service import UserService

router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(get_current_user)],
    responses={401: {"description": "Ausência de token, token inválido ou expirado"}},
)


@router.get("/me", summary="Dados do usuário autenticado")
async def read_me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)


@router.patch(
    "/me",
    summary="Atualiza o próprio perfil",
    responses={409: {"description": "E-mail ou nome de usuário já em uso"}},
)
async def update_me(
    data: UserUpdateIn,
    user: User = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
) -> UserOut:
    updated = await service.update_profile(user, data)
    return UserOut.model_validate(updated)


@router.post(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Troca a senha e encerra todas as sessões",
    responses={401: {"description": "Senha atual incorreta"}},
)
async def change_password(
    data: PasswordChangeIn,
    user: User = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
) -> Response:
    await service.change_password(user, data.current_password, data.new_password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Exclui a própria conta e tudo que pende dela",
    responses={401: {"description": "Senha incorreta"}},
)
async def delete_me(
    data: AccountDeleteIn,
    user: User = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
) -> Response:
    await service.delete_account(user, data.password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
