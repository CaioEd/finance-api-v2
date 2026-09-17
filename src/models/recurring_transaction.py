"""Model de recorrência: a regra que registra um lançamento todo mês, sozinha.

Uma recorrência não é um lançamento. É o **molde** de um — valor, categoria e
descrição — mais o dia do mês em que ele acontece. Quem registra os lançamentos
é o agendador (`jobs.recurring_transactions`), e cada lançamento gerado é uma
linha comum de `transactions`, que aponta de volta para a regra que o criou.

Guardar a ocorrência como lançamento de verdade, e não calculá-la na leitura, é
o que mantém "saldo é derivado de `transactions`" verdadeiro: o saldo, o
extrato e o PDF continuam somando uma tabela só, e a despesa gerada se edita e
se exclui como qualquer outra.

O estado do agendamento é uma coluna só, `next_occurrence_on`: a próxima data
que ainda não foi registrada. O agendador registra tudo que tiver essa data em
hoje ou antes e a avança **na mesma transação** do INSERT — é isso, e não uma
constraint de unicidade, que impede o mesmo mês de ser lançado duas vezes (ver
`repositories.recurring_transaction_repository.lock_due`).

O `kind` segue a regra de `models.transaction`: vem da categoria, nunca é
gravado aqui.
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
"""Nome que `NAMING_CONVENTION` dá à FK de `category_id` desta tabela.

Lido por quem exclui categoria (`repositories.category_repository`): a
recorrência prende a categoria como o lançamento prende, e pelo mesmo motivo.
"""


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
        # Sem `ondelete`, pela mesma razão de `transactions.category_id`: excluir
        # a conta cascateia para as duas tabelas na mesma instrução, e excluir
        # uma categoria que alguma recorrência usa vira 409 `category_in_use`.
        # Apagar a regra junto com o rótulo pararia um lançamento mensal sem
        # ninguém ter pedido.
        ForeignKey("categories.id"),
        nullable=False,
    )
    category: Mapped[Category] = relationship(lazy="raise")
    """Sempre carregada pelo repositório — ver `models.transaction.Transaction.category`."""

    amount: Mapped[Decimal] = mapped_column(
        Numeric(AMOUNT_MAX_DIGITS, AMOUNT_DECIMAL_PLACES), nullable=False
    )
    description: Mapped[str] = mapped_column(
        String(DESCRIPTION_MAX_LENGTH), nullable=False, server_default=""
    )

    day_of_month: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    """O dia em que o lançamento acontece, de 1 a 31.

    Em mês mais curto que o dia pedido, a ocorrência cai no último dia do mês:
    "todo dia 31" é 28 (ou 29) de fevereiro e 30 de abril, e volta a ser 31 em
    março. O dia não se perde de um mês para o outro porque cada ocorrência é
    calculada a partir dele, e não da data anterior.
    """

    next_occurrence_on: Mapped[date] = mapped_column(Date, nullable=False)
    """A próxima competência ainda não registrada, no fuso da aplicação.

    Não é campo de entrada: nasce da data de início e do dia, e só o serviço o
    move — ao registrar uma ocorrência, ao trocar o dia e ao reativar.
    """

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    """Pausada, a regra não registra nada, e o que venceu durante a pausa não volta."""

    occurrences: Mapped[list[Transaction]] = relationship(lazy="raise", passive_deletes=True)
    """Os lançamentos que esta regra gerou. Ninguém lê esta coleção — e é de propósito.

    Ela existe pela ordem dos INSERTs. Criar um lançamento já com a recorrência
    nova numa requisição só grava as duas linhas no mesmo flush, e o SQLAlchemy
    só ordena INSERTs de tabelas diferentes pelas `relationship` declaradas, não
    pela FK: sem esta, a ordem sai do nome dos mappers, e a FK passaria a
    depender de "recurring" vir antes de "transactions" no alfabeto.

    `passive_deletes=True` deixa o `ON DELETE SET NULL` com o banco: sem ele,
    excluir a regra tentaria carregar a coleção para anular cada lançamento — e
    `lazy="raise"` recusaria.
    """

    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            f"day_of_month BETWEEN {DAY_OF_MONTH_MIN} AND {DAY_OF_MONTH_MAX}",
            name="day_of_month_range",
        ),
        # A listagem é "as minhas, pelo dia do mês".
        Index("ix_recurring_transactions_user_id_day_of_month", "user_id", "day_of_month"),
        # A consulta do agendador: só as ativas, pela data da próxima ocorrência.
        # Parcial porque a pausada nunca é candidata, e a varredura não precisa
        # passar por ela a cada rodada.
        Index(
            "ix_recurring_transactions_next_occurrence_on",
            "next_occurrence_on",
            postgresql_where=text("is_active"),
        ),
        # Sem este, a checagem da FK ao excluir uma categoria varre a tabela.
        Index("ix_recurring_transactions_category_id", "category_id"),
    )

    @property
    def kind(self) -> CategoryKind:
        """Receita ou despesa — do `kind` da categoria, a única fonte."""
        return self.category.kind

    def __repr__(self) -> str:
        return f"<RecurringTransaction {self.amount} todo dia {self.day_of_month}>"
