"""Cria limites de gastos e objetivos de investimentos."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "aa0927limitsgoals"
down_revision: str | None = "f6f7100166fa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "spending_limits",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("category_id", sa.Uuid(), sa.ForeignKey("categories.id", ondelete="RESTRICT")),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name="ck_spending_limits_amount_positive"),
        sa.CheckConstraint("starts_on <= ends_on", name="ck_spending_limits_valid_period"),
    )
    op.create_index("ix_spending_limits_user_id", "spending_limits", ["user_id"])
    op.create_table(
        "investment_goals",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("investment_id", sa.Uuid(), sa.ForeignKey("investments.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("target_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("target_on", sa.Date()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("target_amount > 0", name="ck_investment_goals_target_positive"),
    )
    op.create_index("ix_investment_goals_user_id", "investment_goals", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_investment_goals_user_id", table_name="investment_goals")
    op.drop_table("investment_goals")
    op.drop_index("ix_spending_limits_user_id", table_name="spending_limits")
    op.drop_table("spending_limits")
