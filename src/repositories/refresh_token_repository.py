"""Acesso a dados de refresh tokens."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.refresh_token import RefreshToken


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, token: RefreshToken) -> None:
        self._session.add(token)

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        result = await self._session.scalars(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.first()

    async def revoke_family(self, family_id: UUID, *, at: datetime) -> None:
        """Derruba a linhagem inteira. Usado na detecção de reuso."""
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=at)
        )

    async def revoke_all_for_user(self, user_id: UUID, *, at: datetime) -> None:
        """Encerra todas as sessões: troca de senha, desativação, exclusão."""
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=at)
        )
