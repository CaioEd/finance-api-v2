"""Acesso a dados de transações.

O escopo por dono é imposto **aqui**, como no repositório de categorias: toda
consulta nasce de `Transaction.user_id == user_id`, e não existe método que
alcance o lançamento de outra pessoa. Um `if` na borda seria uma linha que
alguém esquece de escrever na rota seguinte.

Toda consulta faz `JOIN` com `categories` e traz a categoria junto
(`contains_eager`): o `kind` do lançamento sai de lá, o nome da categoria vai
na resposta, e o model declara `lazy="raise"` justamente para que faltar o
eager load falhe aqui, e não no meio da serialização.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from core.errors import DomainError, InvalidCategoryError, UnprocessableError
from models.category import Category, CategoryKind
from models.transaction import FK_CATEGORY, Transaction


@dataclass(frozen=True, slots=True)
class TransactionFilters:
    """O recorte pedido na listagem. Ausente (`None`) é "não filtre por isto"."""

    kind: CategoryKind | None = None
    category_id: UUID | None = None
    occurred_from: date | None = None
    occurred_to: date | None = None


def _conditions(user_id: UUID, filters: TransactionFilters) -> list[ColumnElement[bool]]:
    """Uma função só para os dois lados da paginação.

    A contagem precisa exatamente do mesmo `WHERE` da listagem; duplicar a
    construção é como o `total` acaba mentindo sobre o número de páginas.
    """
    conditions: list[ColumnElement[bool]] = [Transaction.user_id == user_id]
    if filters.kind is not None:
        conditions.append(Category.kind == filters.kind)
    if filters.category_id is not None:
        conditions.append(Transaction.category_id == filters.category_id)
    if filters.occurred_from is not None:
        conditions.append(Transaction.occurred_on >= filters.occurred_from)
    if filters.occurred_to is not None:
        conditions.append(Transaction.occurred_on <= filters.occurred_to)
    return conditions


def _with_category() -> Select[tuple[Transaction]]:
    """`SELECT` de lançamento com a categoria já carregada.

    O JOIN é interno porque `category_id` é `NOT NULL`: não existe lançamento
    sem categoria para um `LEFT JOIN` resgatar.
    """
    return (
        select(Transaction)
        .join(Category, Transaction.category_id == Category.id)
        .options(contains_eager(Transaction.category))
    )


class TransactionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_transactions(
        self, user_id: UUID, *, filters: TransactionFilters, limit: int, offset: int
    ) -> Sequence[Transaction]:
        statement = (
            _with_category()
            .where(*_conditions(user_id, filters))
            # O desempate por id mantém a paginação estável: sem ele, dois
            # lançamentos do mesmo dia podem trocar de página entre duas
            # requisições e um deles nunca ser visto.
            .order_by(Transaction.occurred_on.desc(), Transaction.id)
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.scalars(statement)
        return result.all()

    async def count_transactions(self, user_id: UUID, *, filters: TransactionFilters) -> int:
        statement = (
            select(func.count())
            .select_from(Transaction)
            .join(Category, Transaction.category_id == Category.id)
            .where(*_conditions(user_id, filters))
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def get_owned(self, transaction_id: UUID, user_id: UUID) -> Transaction | None:
        """Devolve `None` para lançamento de terceiro — que a borda traduz em 404.

        Não é `session.get`: aquele traria a linha de qualquer dono, e o escopo
        passaria a depender de quem chamou lembrar de conferir.
        """
        result = await self._session.scalars(
            _with_category().where(Transaction.id == transaction_id, Transaction.user_id == user_id)
        )
        return result.first()

    def add(self, transaction: Transaction) -> None:
        self._session.add(transaction)

    async def delete(self, transaction: Transaction) -> None:
        await self._session.delete(transaction)


def translate_integrity_error(exc: IntegrityError) -> DomainError:
    """Traduz a violação da FK de categoria **vista de quem insere o lançamento**.

    O serviço já conferiu que a categoria é visível antes de gravar, então
    chegar aqui significa que ela deixou de existir entre a checagem e o
    INSERT. Quem decide é o banco, porque um SELECT prévio sempre terá essa
    janela — a checagem existe para a recusa ser legível, não para ser
    autoridade.

    A mesma constraint aparece em `repositories.category_repository` com outro
    significado: lá é a exclusão da categoria esbarrando no lançamento que
    aponta para ela.
    """
    if FK_CATEGORY in str(exc.orig):
        return InvalidCategoryError()
    return UnprocessableError()
