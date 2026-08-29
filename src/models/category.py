"""Model de categoria.

Uma tabela só guarda os dois tipos de categoria, distinguidos por `user_id`:

- **`user_id IS NULL`** — categoria do sistema, visível para todo mundo e
  imutável pela API. Nascem na migration que cria a tabela.
- **`user_id = <alguém>`** — categoria daquele usuário, visível só para ele.

Duas tabelas separadas (`categories` e `user_categories`) fariam toda consulta
de transação virar `UNION`, e a chave estrangeira de transação teria de
apontar para uma das duas — que é como se chega a duas colunas nuláveis e a
um CHECK para garantir que exatamente uma esteja preenchida.

A unicidade do nome é imposta por dois índices **parciais**, e não por um
`UNIQUE (user_id, name, kind)`: no Postgres, `NULL` é distinto de `NULL` numa
constraint UNIQUE, então a categoria global "Moradia" poderia ser cadastrada
quantas vezes se quisesse. Os índices também comparam `lower(name)`, senão
"Mercado" e "mercado" convivem na mesma lista como categorias diferentes.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base, TimestampMixin


class CategoryKind(StrEnum):
    """Um lançamento é receita ou despesa, e a categoria diz de qual ele é.

    Sem isto, "Salário" e "Aluguel" caem na mesma lista na hora de registrar
    uma despesa — e nada impede lançar um gasto em "Salário".
    """

    INCOME = "income"
    EXPENSE = "expense"


def kind_column() -> Enum:
    """VARCHAR com CHECK, não ENUM nativo — mesma decisão de `models.user.role_column`."""
    return Enum(
        CategoryKind,
        native_enum=False,
        create_constraint=False,
        length=16,
        name="category_kind",
        values_callable=lambda enum_type: [member.value for member in enum_type],
    )


KIND_CHECK = CheckConstraint(
    "kind IN ({})".format(", ".join(f"'{member.value}'" for member in CategoryKind)),
    name="kind",  # com a convenção de nomes vira `ck_categories_kind`
)

CATEGORY_NAME_MAX_LENGTH = 60


class Category(TimestampMixin, Base):
    __tablename__ = "categories"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    """Dono. `NULL` é a categoria do sistema — ver o docstring do módulo."""

    name: Mapped[str] = mapped_column(String(CATEGORY_NAME_MAX_LENGTH), nullable=False)
    kind: Mapped[CategoryKind] = mapped_column(kind_column(), nullable=False)

    __table_args__ = (
        KIND_CHECK,
        # Um índice para cada metade da tabela: a global não tem dono para
        # compor a chave, a do usuário tem.
        Index(
            "uq_categories_global_name_kind",
            text("lower(name)"),
            "kind",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
        ),
        Index(
            "uq_categories_user_name_kind",
            "user_id",
            text("lower(name)"),
            "kind",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        # Toda listagem filtra por dono; sem isto é varredura de tabela inteira.
        Index("ix_categories_user_id", "user_id"),
    )

    @property
    def is_global(self) -> bool:
        return self.user_id is None

    def __repr__(self) -> str:
        owner = "sistema" if self.is_global else self.user_id
        return f"<Category {self.name} ({self.kind}, {owner})>"
