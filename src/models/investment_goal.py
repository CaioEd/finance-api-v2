"""Objetivos de valor da carteira ou de uma posição."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Numeric, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base, TimestampMixin


class InvestmentGoal(TimestampMixin, Base):
    __tablename__ = "investment_goals"
    __table_args__ = (
        CheckConstraint("target_amount > 0", name="target_positive"),
        Index("ix_investment_goals_user_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    investment_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("investments.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    target_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    target_on: Mapped[date | None] = mapped_column(Date, nullable=True)
