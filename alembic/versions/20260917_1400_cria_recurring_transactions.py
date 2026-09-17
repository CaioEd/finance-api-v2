"""cria recurring_transactions

- `category_id` sem `ondelete`, como em `transactions`: categoria em uso vira 409.
- `transactions.recurring_transaction_id` com `SET NULL`: excluir a regra não
  apaga o que ela já lançou.
- Sem `UNIQUE (regra, data)`: a duplicidade é evitada pela trava do agendador, e
  a constraint barraria mover um lançamento gerado para a data de um mês futuro.

Revision ID: 5d28986187b0
Revises: 4c1f7a90d5e2
Create Date: 2026-09-17 14:00:22.487078
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5d28986187b0"
down_revision: str | None = "4c1f7a90d5e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recurring_transactions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("description", sa.String(length=200), server_default="", nullable=False),
        sa.Column("day_of_month", sa.SmallInteger(), nullable=False),
        sa.Column("next_occurrence_on", sa.Date(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
        sa.CheckConstraint("amount > 0", name=op.f("ck_recurring_transactions_amount_positive")),
        sa.CheckConstraint(
            "day_of_month BETWEEN 1 AND 31",
            name=op.f("ck_recurring_transactions_day_of_month_range"),
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name=op.f("fk_recurring_transactions_category_id_categories"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_recurring_transactions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recurring_transactions")),
    )
    op.create_index(
        "ix_recurring_transactions_user_id_day_of_month",
        "recurring_transactions",
        ["user_id", "day_of_month"],
        unique=False,
    )
    op.create_index(
        "ix_recurring_transactions_next_occurrence_on",
        "recurring_transactions",
        ["next_occurrence_on"],
        unique=False,
        postgresql_where=sa.text("is_active"),
    )
    # Sem este, a checagem da FK ao excluir uma categoria varre a tabela.
    op.create_index(
        "ix_recurring_transactions_category_id",
        "recurring_transactions",
        ["category_id"],
        unique=False,
    )

    op.add_column("transactions", sa.Column("recurring_transaction_id", sa.Uuid(), nullable=True))
    # Para o SET NULL ao excluir uma recorrência não varrer `transactions`.
    op.create_index(
        "ix_transactions_recurring_transaction_id",
        "transactions",
        ["recurring_transaction_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_transactions_recurring_transaction_id_recurring_transactions"),
        "transactions",
        "recurring_transactions",
        ["recurring_transaction_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_transactions_recurring_transaction_id_recurring_transactions"),
        "transactions",
        type_="foreignkey",
    )
    op.drop_index("ix_transactions_recurring_transaction_id", table_name="transactions")
    op.drop_column("transactions", "recurring_transaction_id")
    op.drop_index("ix_recurring_transactions_category_id", table_name="recurring_transactions")
    op.drop_index(
        "ix_recurring_transactions_next_occurrence_on",
        table_name="recurring_transactions",
        postgresql_where=sa.text("is_active"),
    )
    op.drop_index(
        "ix_recurring_transactions_user_id_day_of_month", table_name="recurring_transactions"
    )
    op.drop_table("recurring_transactions")
