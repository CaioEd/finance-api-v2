"""cria categories

Cria a tabela e já insere as categorias do sistema (`user_id IS NULL`).

A lista mora aqui, escrita à mão, e não importada de um módulo da aplicação:
migration é retrato de um momento. Se ela lesse uma constante do código,
acrescentar uma categoria nova amanhã mudaria em silêncio o que esta revisão
faz — e o banco de quem já a aplicou deixaria de corresponder a ela.

Categoria do sistema é dado de referência do produto (todo usuário precisa
enxergá-la), então nasce na migration, e não no `seed-dev`: o seed é travado em
local/test, e em produção a lista simplesmente não existiria.

Revision ID: 931b3d28c871
Revises: eb92653118d8
Create Date: 2026-08-29 13:14:56.691067
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "931b3d28c871"
down_revision: str | None = "eb92653118d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SYSTEM_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("Salário", "income"),
    ("Freelance", "income"),
    ("Investimentos", "income"),
    ("Aluguel recebido", "income"),
    ("Reembolso", "income"),
    ("Outras receitas", "income"),
    ("Moradia", "expense"),
    ("Alimentação", "expense"),
    ("Transporte", "expense"),
    ("Saúde", "expense"),
    ("Educação", "expense"),
    ("Lazer", "expense"),
    ("Compras", "expense"),
    ("Serviços", "expense"),
    ("Impostos e taxas", "expense"),
    ("Outras despesas", "expense"),
)


def upgrade() -> None:
    categories = op.create_table(
        "categories",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("income", "expense", name="category_kind", native_enum=False, length=16),
            nullable=False,
        ),
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
        sa.CheckConstraint("kind IN ('income', 'expense')", name=op.f("ck_categories_kind")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_categories_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
    )
    op.create_index("ix_categories_user_id", "categories", ["user_id"], unique=False)
    # Dois índices parciais em vez de um UNIQUE: numa constraint UNIQUE do
    # Postgres, NULL é distinto de NULL, e a categoria global "Moradia" poderia
    # repetir à vontade. `lower(name)` porque caixa não distingue categoria.
    op.create_index(
        "uq_categories_global_name_kind",
        "categories",
        [sa.literal_column("lower(name)"), "kind"],
        unique=True,
        postgresql_where=sa.text("user_id IS NULL"),
    )
    op.create_index(
        "uq_categories_user_name_kind",
        "categories",
        ["user_id", sa.literal_column("lower(name)"), "kind"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )

    op.bulk_insert(
        categories,
        [{"user_id": None, "name": name, "kind": kind} for name, kind in SYSTEM_CATEGORIES],
    )


def downgrade() -> None:
    # As linhas do sistema caem junto com a tabela.
    op.drop_index(
        "uq_categories_user_name_kind",
        table_name="categories",
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_categories_global_name_kind",
        table_name="categories",
        postgresql_where=sa.text("user_id IS NULL"),
    )
    op.drop_index("ix_categories_user_id", table_name="categories")
    op.drop_table("categories")
