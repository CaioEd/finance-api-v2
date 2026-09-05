"""Regra de transações: listar, criar, editar e excluir lançamentos.

Python puro, como os demais serviços: nada aqui conhece HTTP nem FastAPI. As
dependências são `Protocol` — o serviço declara de que operações precisa, e
tanto o repositório real quanto um duble as satisfazem. É o que permite
exercitar a regra sem Postgres (`tests/unit/test_transaction_service.py`).

Três regras vivem aqui, e só aqui:

1. **A categoria precisa ser visível para quem lança.** Global ou própria, sim;
   de outra pessoa, não — e a recusa é a mesma nos dois casos, para não
   confirmar o que existe na conta alheia.
2. **O tipo do lançamento é o da categoria.** Não há o que validar entre os
   dois: trocar de categoria numa edição troca o tipo junto, por construção.
3. **"Hoje" vem do `Clock`**, nunca de `date.today()` — é o que torna o default
   de `occurred_on` testável e o prende ao fuso da aplicação.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from core.clock import Clock
from core.errors import InvalidCategoryError, TransactionNotFoundError
from models.category import Category
from models.transaction import Transaction
from models.user import User
from repositories.transaction_repository import TransactionFilters, translate_integrity_error
from schemas.transaction import TransactionCreateIn, TransactionUpdateIn


class TransactionStore(Protocol):
    """O que este serviço precisa de um repositório de lançamentos."""

    async def list_transactions(
        self, user_id: UUID, *, filters: TransactionFilters, limit: int, offset: int
    ) -> Sequence[Transaction]: ...

    async def count_transactions(self, user_id: UUID, *, filters: TransactionFilters) -> int: ...

    async def get_owned(self, transaction_id: UUID, user_id: UUID) -> Transaction | None: ...

    def add(self, transaction: Transaction) -> None: ...

    async def delete(self, transaction: Transaction) -> None: ...


class CategoryLookup(Protocol):
    """A única coisa que lançar exige de categorias: resolver uma que o dono enxergue.

    Estreito de propósito. O serviço não lista nem altera categoria — pedir o
    `CategoryRepository` inteiro deixaria isso possível por descuido.
    """

    async def get_visible(self, category_id: UUID, user_id: UUID) -> Category | None: ...


class UnitOfWork(Protocol):
    """A parte da sessão que é assunto do serviço: quando o trabalho fecha.

    `AsyncSession` a satisfaz estruturalmente; o teste unitário entrega uma
    transação de mentira e não abre conexão nenhuma. O nome foge do
    `Transaction` que `services.admin_user_service` usa para o mesmo papel:
    aqui `Transaction` já é o model, e repetir o nome seria confundir o
    lançamento com o commit.
    """

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


@dataclass(frozen=True, slots=True)
class TransactionPage:
    items: Sequence[Transaction]
    total: int
    limit: int
    offset: int


class TransactionService:
    def __init__(
        self,
        *,
        unit_of_work: UnitOfWork,
        transactions: TransactionStore,
        categories: CategoryLookup,
        clock: Clock,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._transactions = transactions
        self._categories = categories
        self._clock = clock

    async def list_transactions(
        self, user: User, *, filters: TransactionFilters, limit: int, offset: int
    ) -> TransactionPage:
        items = await self._transactions.list_transactions(
            user.id, filters=filters, limit=limit, offset=offset
        )
        total = await self._transactions.count_transactions(user.id, filters=filters)
        return TransactionPage(items=items, total=total, limit=limit, offset=offset)

    async def get(self, user: User, transaction_id: UUID) -> Transaction:
        return await self._owned_or_fail(user, transaction_id)

    async def create(self, user: User, data: TransactionCreateIn) -> Transaction:
        category = await self._visible_category_or_fail(user, data.category_id)
        transaction = Transaction(
            user_id=user.id,
            category_id=category.id,
            category=category,
            amount=data.amount,
            occurred_on=data.occurred_on or self._clock.today(),
            description=data.description,
        )
        self._transactions.add(transaction)
        await self._commit()
        return transaction

    async def update(
        self, user: User, transaction_id: UUID, data: TransactionUpdateIn
    ) -> Transaction:
        transaction = await self._owned_or_fail(user, transaction_id)
        changes = data.model_dump(exclude_unset=True)

        # A categoria sai do laço: trocá-la exige revalidar a visibilidade, e
        # atribuir `category_id` cru deixaria `category` — de onde vem `kind` —
        # apontando para a categoria antiga na resposta desta requisição.
        if "category_id" in changes:
            category = await self._visible_category_or_fail(user, changes.pop("category_id"))
            transaction.category_id = category.id
            transaction.category = category

        for field, value in changes.items():
            setattr(transaction, field, value)

        await self._commit()
        return transaction

    async def delete(self, user: User, transaction_id: UUID) -> None:
        transaction = await self._owned_or_fail(user, transaction_id)
        await self._transactions.delete(transaction)
        await self._commit()

    async def _owned_or_fail(self, user: User, transaction_id: UUID) -> Transaction:
        transaction = await self._transactions.get_owned(transaction_id, user.id)
        if transaction is None:
            raise TransactionNotFoundError()
        return transaction

    async def _visible_category_or_fail(self, user: User, category_id: UUID) -> Category:
        """A categoria que este usuário pode usar: uma global ou uma dele.

        `get_visible` devolve `None` tanto para a inexistente quanto para a de
        outra pessoa, e a recusa é a mesma — 422, sem dizer qual dos dois casos
        é. O escopo vem do repositório; aqui não há `if` de dono.
        """
        category = await self._categories.get_visible(category_id, user.id)
        if category is None:
            raise InvalidCategoryError()
        return category

    async def _commit(self) -> None:
        try:
            await self._unit_of_work.commit()
        except IntegrityError as exc:
            await self._unit_of_work.rollback()
            raise translate_integrity_error(exc) from exc
