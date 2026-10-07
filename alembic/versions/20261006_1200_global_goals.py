"""Permite objetivos globais com progresso calculado por usuário."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b61006globalgoals"
down_revision: str | None = "aa0927limitsgoals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("investment_goals", "user_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM investment_goals WHERE user_id IS NULL")
    op.alter_column("investment_goals", "user_id", existing_type=sa.Uuid(), nullable=False)
