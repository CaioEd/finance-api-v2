"""Regra do perfil próprio: edição, senha e exclusão da conta."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.clock import Clock
from core.errors import AuthenticationError
from core.security import PasswordHasher
from models.user import User
from repositories.refresh_token_repository import RefreshTokenRepository
from repositories.user_repository import translate_integrity_error
from schemas.user import UserUpdateIn


class UserService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        tokens: RefreshTokenRepository,
        hasher: PasswordHasher,
        clock: Clock,
    ) -> None:
        self._session = session
        self._tokens = tokens
        self._hasher = hasher
        self._clock = clock

    async def update_profile(self, user: User, data: UserUpdateIn) -> User:
        changes = data.changes()
        for field, value in changes.items():
            setattr(user, field, value)

        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise translate_integrity_error(exc) from exc
        return user

    async def change_password(self, user: User, current: str, new: str) -> None:
        """Troca a senha e derruba todas as sessões.

        Trocar senha é o que a pessoa faz quando acha que a conta foi acessada;
        manter as sessões antigas vivas transformaria o gesto em nada.
        """
        if not self._hasher.verify(user.password_hash, current):
            raise AuthenticationError("Senha atual incorreta.")

        user.password_hash = self._hasher.hash(new)
        await self._tokens.revoke_all_for_user(user.id, at=self._clock.now_utc())
        await self._session.commit()

    async def delete_account(self, user: User, password: str) -> None:
        if not self._hasher.verify(user.password_hash, password):
            raise AuthenticationError("Senha incorreta.")

        # Os refresh tokens caem por ON DELETE CASCADE, junto com tudo que for
        # do usuário nas fases seguintes.
        await self._session.delete(user)
        await self._session.commit()
