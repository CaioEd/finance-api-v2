"""Usuário atual e autorização por papel."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.errors import AccountInactiveError, ForbiddenError, InvalidTokenError
from core.security import TokenCodec
from dependencies.repositories import get_user_repository
from dependencies.state import get_token_codec
from models.user import Role, User
from repositories.user_repository import UserRepository

bearer_scheme = HTTPBearer(
    auto_error=False,  # o erro é nosso, para sair no envelope e com 401 (não 403)
    description="Access token obtido em POST /api/v1/auth/login",
)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    codec: TokenCodec = Depends(get_token_codec),
    users: UserRepository = Depends(get_user_repository),
) -> User:
    """Resolve o portador do access token.

    Aplicada no `APIRouter` inteiro de cada arquivo de rotas, nunca rota a rota:
    assim esquecer a declaração *fecha* a rota em vez de abri-la. Foi o
    esquecimento rota a rota que deixou receitas, despesas e saldo públicos no
    sistema antigo.
    """
    if credentials is None:
        raise InvalidTokenError("Credencial ausente.")

    claims = codec.decode_access(credentials.credentials)

    user = await users.get(claims.subject)
    if user is None:
        # Token assinado por nós para um usuário que não existe mais.
        raise InvalidTokenError()
    if not user.is_active:
        raise AccountInactiveError()
    return user


def require_role(*roles: Role) -> Callable[..., Awaitable[User]]:
    """Fábrica de dependência de autorização por papel.

    Usada como `APIRouter(dependencies=[Depends(require_role(Role.ADMIN))])`.
    """

    async def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise ForbiddenError()
        return user

    return dependency
