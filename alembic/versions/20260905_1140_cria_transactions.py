"""cria transactions

A FK de `category_id` **não declara `ondelete`**, e isso é a decisão da
migration, não um esquecimento. O default do Postgres é NO ACTION, checado no
fim da instrução:

- excluir a conta cascateia para `categories` e `transactions` na mesma
  instrução, e no fim dela não sobrou lançamento órfão — a checagem passa. Com
  RESTRICT, que é checado na hora, a exclusão do usuário quebraria assim que a
  primeira categoria dele caísse;
- excluir uma categoria que ainda tem lançamento falha, e a violação vira 409
  em `repositories.category_repository.translate_integrity_error`.

Revision ID: 4c1f7a90d5e2
Revises: 931b3d28c871
Create Date: 2026-09-05 11:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4c1f7a90d5e2"
down_revision: str | None = "931b3d28c871"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transactions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("description", sa.String(length=200), server_default="", nullable=False),
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
        # O sinal do lançamento é o `kind` da categoria; valor negativo aqui
        # seria uma segunda forma de dizer "despesa".
        sa.CheckConstraint("amount > 0", name=op.f("ck_transactions_amount_positive")),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name=op.f("fk_transactions_category_id_categories"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_transactions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transactions")),
    )
    op.create_index(
        "ix_transactions_user_id_occurred_on",
        "transactions",
        ["user_id", "occurred_on"],
        unique=False,
    )
    # Sem este, a checagem da FK ao excluir uma categoria varre a tabela.
    op.create_index("ix_transactions_category_id", "transactions", ["category_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_transactions_category_id", table_name="transactions")
    op.drop_index("ix_transactions_user_id_occurred_on", table_name="transactions")
    op.drop_table("transactions")
