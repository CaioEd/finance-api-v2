"""Model de transação: um lançamento de receita ou de despesa.

Uma entidade só para os dois tipos. O que separa receita
de despesa é o `kind` da categoria à qual o lançamento está preso.

Guardar `kind` também nesta tabela criaria duas fontes para o mesmo fato, e a
primeira despesa lançada em "Salário" seria irrecuperável — o total do mês
discordaria da lista que o usuário vê. Derivando, a contradição não tem como
existir: escolher a categoria *é* escolher o tipo. O preço é um JOIN nas
consultas, e ele é coberto por `ix_transactions_category_id`.

Por isso `amount` é sempre **positivo**, e o `kind` da categoria é que diz se é receita ou despesa.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base, TimestampMixin
from models.category import Category, CategoryKind

AMOUNT_MAX_DIGITS = 14
AMOUNT_DECIMAL_PLACES = 2
DESCRIPTION_MAX_LENGTH = 200

FK_CATEGORY = "fk_transactions_category_id_categories"
"""Nome que `NAMING_CONVENTION` dá à FK de `category_id`.

Mora aqui, junto da declaração, porque os dois repositórios que traduzem a
violação dela — o que insere lançamento e o que exclui categoria — a leem com
significados opostos, e nenhum dos dois deve ser dono do nome.
"""

AMOUNT_CHECK = CheckConstraint(
    "amount > 0",
    name="amount_positive",  # com a convenção de nomes vira `ck_transactions_amount_positive`
)


class Transaction(TimestampMixin, Base):
    __tablename__ = "transactions"

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
        # Sem `ondelete`: o default do Postgres é NO ACTION, checado no fim da
        # instrução, e é isso que faz as duas exclusões se comportarem bem.
        #
        #   - excluir a conta cascateia para `categories` e `transactions` na
        #     mesma instrução; no fim dela não sobrou lançamento órfão, e a
        #     checagem passa. Com RESTRICT — que é checado na hora — a exclusão
        #     do usuário quebraria assim que a primeira categoria dele caísse.
        #   - excluir uma categoria que tem lançamento falha, e vira 409 em
        #     `translate_integrity_error`. Perder o histórico para apagar um
        #     rótulo seria o pior default possível.
        ForeignKey("categories.id"),
        nullable=False,
    )
    category: Mapped[Category] = relationship(lazy="raise")
    """Sempre carregada de propósito: `kind` sai daqui, e lazy load em async falha.

    `lazy="raise"` troca um `MissingGreenlet` no meio da serialização por um
    erro que diz o que faltou — o `contains_eager` do repositório.
    """

    amount: Mapped[Decimal] = mapped_column(
        Numeric(AMOUNT_MAX_DIGITS, AMOUNT_DECIMAL_PLACES), nullable=False
    )
    """`NUMERIC`, nunca float: 0.1 + 0.2 não pode dar 0.30000000000000004 em dinheiro."""

    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    """Competência: o dia do fato, no fuso da aplicação.

    Distinta de `created_at`, que é quando a linha foi gravada. Confundir as
    duas é o que impede lançar hoje uma despesa da semana passada.
    """

    description: Mapped[str] = mapped_column(
        String(DESCRIPTION_MAX_LENGTH), nullable=False, server_default=""
    )

    __table_args__ = (
        AMOUNT_CHECK,
        # Toda listagem é "os meus, do mais recente para o mais antigo", e a
        # fase 4 agrega por intervalo de datas sobre o mesmo recorte.
        Index("ix_transactions_user_id_occurred_on", "user_id", "occurred_on"),
        # Sem este, a checagem da FK ao excluir uma categoria varre a tabela.
        Index("ix_transactions_category_id", "category_id"),
    )

    @property
    def kind(self) -> CategoryKind:
        """Receita ou despesa — do `kind` da categoria, a única fonte."""
        return self.category.kind

    def __repr__(self) -> str:
        return f"<Transaction {self.amount} {self.kind} em {self.occurred_on}>"
