"""Model de recorrência: o molde de um lançamento que se repete todo mês.

Guarda valor, categoria, descrição e dia do mês. O que ela registra é lançamento
comum em `transactions`, apontando de volta por `recurring_transaction_id` —
saldo, extrato e PDF continuam somando uma tabela só.

`next_occurrence_on` é o estado do agendamento: avançá-lo no mesmo commit do
INSERT é o que impede um mês de entrar duas vezes (ver `lock_due`). O `kind`
vem da categoria, como em `models.transaction`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base, TimestampMixin
from models.category import Category, CategoryKind
from models.transaction import (
    AMOUNT_DECIMAL_PLACES,
    AMOUNT_MAX_DIGITS,
    DESCRIPTION_MAX_LENGTH,
    Transaction,
)

DAY_OF_MONTH_MIN = 1
DAY_OF_MONTH_MAX = 31

FK_RECURRING_CATEGORY = "fk_recurring_transactions_category_id_categories"
"""Nome da FK de `category_id`; lido por quem traduz a violação em 409/422."""


class RecurringTransaction(TimestampMixin, Base):
    __tablename__ = "recurring_transactions"

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
    category_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        # Sem `ondelete`, como em `transactions`: categoria em uso vira 409.
        ForeignKey("categories.id"),
        nullable=False,
    )
    category: Mapped[Category] = relationship(lazy="raise")

    amount: Mapped[Decimal] = mapped_column(
        Numeric(AMOUNT_MAX_DIGITS, AMOUNT_DECIMAL_PLACES), nullable=False
    )
    description: Mapped[str] = mapped_column(
        String(DESCRIPTION_MAX_LENGTH), nullable=False, server_default=""
    )

    day_of_month: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    """De 1 a 31; em mês mais curto, a ocorrência cai no último dia."""

    next_occurrence_on: Mapped[date] = mapped_column(Date, nullable=False)
    """Próxima data ainda não registrada. Só o serviço a move; nunca é entrada."""

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    """Pausada, não registra nada — e o que venceu na pausa não volta."""

    occurrences: Mapped[list[Transaction]] = relationship(lazy="raise", passive_deletes=True)
    """Ninguém lê esta coleção: ela existe para ordenar os INSERTs.

    O SQLAlchemy ordena escritas entre tabelas pelas `relationship`, não pela FK.
    Sem ela, regra e lançamento criados no mesmo flush dependeriam da ordem
    alfabética dos mappers. `passive_deletes` deixa o `SET NULL` com o banco.
    """

    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            f"day_of_month BETWEEN {DAY_OF_MONTH_MIN} AND {DAY_OF_MONTH_MAX}",
            name="day_of_month_range",
        ),
        Index("ix_recurring_transactions_user_id_day_of_month", "user_id", "day_of_month"),
        # Consulta do agendador; parcial porque regra pausada nunca é candidata.
        Index(
            "ix_recurring_transactions_next_occurrence_on",
            "next_occurrence_on",
            postgresql_where=text("is_active"),
        ),
        Index("ix_recurring_transactions_category_id", "category_id"),
    )

    @property
    def kind(self) -> CategoryKind:
        return self.category.kind

    def __repr__(self) -> str:
        return f"<RecurringTransaction {self.amount} todo dia {self.day_of_month}>"
