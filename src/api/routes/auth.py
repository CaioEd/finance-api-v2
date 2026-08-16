"""Rotas de autenticação.

Único arquivo de rotas sem `get_current_user`: são exatamente os endpoints que
existem para quem ainda não tem token. A exceção é explícita aqui e em lugar
nenhum mais.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from dependencies.services import get_auth_service
from schemas.auth import (
    LoginIn,
    LogoutIn,
    RefreshIn,
    RegisterIn,
    RegisterOut,
    TokenPairOut,
)
from schemas.user import UserOut
from services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Cria a conta e já devolve o par de tokens",
    responses={409: {"description": "E-mail ou nome de usuário já cadastrado"}},
)
async def register(
    data: RegisterIn,
    service: AuthService = Depends(get_auth_service),
) -> RegisterOut:
    user, pair = await service.register(data)
    return RegisterOut(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
        user=UserOut.model_validate(user),
    )


@router.post(
    "/login",
    summary="Autentica por e-mail e senha",
    responses={
        401: {"description": "E-mail ou senha inválidos"},
        403: {"description": "Conta desativada"},
    },
)
async def login(
    data: LoginIn,
    service: AuthService = Depends(get_auth_service),
) -> TokenPairOut:
    pair = await service.login(data.email, data.password)
    return TokenPairOut(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post(
    "/refresh",
    summary="Rotaciona o refresh token e emite um par novo",
    responses={401: {"description": "Token inválido, expirado ou já utilizado"}},
)
async def refresh(
    data: RefreshIn,
    service: AuthService = Depends(get_auth_service),
) -> TokenPairOut:
    pair = await service.refresh(data.refresh_token)
    return TokenPairOut(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoga o refresh token enviado (idempotente)",
)
async def logout(
    data: LogoutIn,
    service: AuthService = Depends(get_auth_service),
) -> None:
    await service.logout(data.refresh_token)
