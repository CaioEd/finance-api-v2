"""Refresh tokens persistidos.

Uma linha por token emitido. O valor em si nunca é gravado — só o SHA-256 —,
então um dump do banco não dá a ninguém uma sessão.

`family_id` agrupa a linhagem de uma sessão: login emite o primeiro token de
uma família, e cada rotação cria o próximo da mesma família. Se um token já
rotacionado reaparecer, a família inteira cai (ver `AuthService.refresh`).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    family_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    replaced_by_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("refresh_tokens.id", ondelete="SET NULL"),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_refresh_tokens_user_id_family_id", "user_id", "family_id"),
        Index("ix_refresh_tokens_expires_at", "expires_at"),
    )

    def is_usable_at(self, moment: datetime) -> bool:
        return self.revoked_at is None and self.expires_at > moment
