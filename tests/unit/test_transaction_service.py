"""Regra de transações — sem banco e sem HTTP.

O serviço conversa com três `Protocol` (`TransactionStore`, `CategoryLookup` e
`UnitOfWork`), não com o repositório concreto nem com a `AsyncSession`. É o que
permite exercitar aqui o que de fato é regra: o que vira 404, que categoria um
usuário pode usar, de onde sai "hoje", e como uma edição que troca a categoria
troca o tipo do lançamento junto.

O escopo por dono em si é do repositório (`Transaction.user_id == user_id`), e
o que se cobre aqui é o degrau acima: que o serviço trata "não é seu" e "não
existe" pela mesma porta. Que a query realmente filtre por dono ainda não tem
teste de integração — o duble reproduz a regra, e reproduzir não é verificar.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.exc import IntegrityError

from core.clock import Clock
from core.errors import InvalidCategoryError, TransactionNotFoundError
from models.category import Category, CategoryKind
from models.transaction import FK_CATEGORY, Transaction
from models.user import Role, User
from repositories.transaction_repository import TransactionFilters
from schemas.transaction import TransactionCreateIn, TransactionUpdateIn
from services.transaction_service import (
    CategoryLookup,
    TransactionService,
    TransactionStore,
    UnitOfWork,
)

SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def make_user(user_id: UUID | None = None) -> User:
    return User(
        id=user_id or uuid4(),
        email="ana@exemplo.com",
        username="ana",
        password_hash="$argon2id$hash-de-mentira",
        first_name="Ana",
        last_name="Ribeiro",
        role=Role.USER,
        is_active=True,
    )


def make_category(
    *,
    category_id: UUID | None = None,
    user_id: UUID | None = None,
    name: str = "Mercado",
    kind: CategoryKind = CategoryKind.EXPENSE,
) -> Category:
    return Category(id=category_id or uuid4(), user_id=user_id, name=name, kind=kind)


def make_transaction(
    *,
    transaction_id: UUID | None = None,
    user_id: UUID,
    category: Category,
    amount: str = "10.00",
    occurred_on: date | None = None,
    description: str = "",
) -> Transaction:
    return Transaction(
        id=transaction_id or uuid4(),
        user_id=user_id,
        category_id=category.id,
        category=category,
        amount=Decimal(amount),
        occurred_on=occurred_on or date(2026, 9, 1),
        description=description,
    )


def clock_at(moment: datetime) -> Clock:
    """Relógio parado num instante. O tempo é parâmetro, não leitura global."""
    return Clock(tz=SAO_PAULO, instant=lambda: moment)


def integrity_error(constraint: str) -> IntegrityError:
    """Reproduz o erro que o Postgres devolve ao violar uma constraint."""
    orig = Exception(f'violates foreign key constraint "{constraint}"')
    return IntegrityError("INSERT INTO transactions ...", {}, orig)


class FakeTransactionStore:
    """Implementa `TransactionStore` em memória, guardando o que recebeu."""

    def __init__(self, transactions: Sequence[Transaction] = ()) -> None:
        self.transactions = list(transactions)
        self.added: list[Transaction] = []
        self.deleted: list[Transaction] = []
        self.last_query: dict[str, Any] | None = None

    async def list_transactions(
        self, user_id: UUID, *, filters: TransactionFilters, limit: int, offset: int
    ) -> Sequence[Transaction]:
        self.last_query = {
            "user_id": user_id,
            "filters": filters,
            "limit": limit,
            "offset": offset,
        }
        return self._owned(user_id)[offset : offset + limit]

    async def count_transactions(self, user_id: UUID, *, filters: TransactionFilters) -> int:
        return len(self._owned(user_id))

    async def get_owned(self, transaction_id: UUID, user_id: UUID) -> Transaction | None:
        """O duble impõe o mesmo escopo do repositório: nada de outro dono sai daqui."""
        return next(
            (
                transaction
                for transaction in self.transactions
                if transaction.id == transaction_id and transaction.user_id == user_id
            ),
            None,
        )

    def add(self, transaction: Transaction) -> None:
        self.added.append(transaction)
        self.transactions.append(transaction)

    async def delete(self, transaction: Transaction) -> None:
        self.deleted.append(transaction)
        self.transactions.remove(transaction)

    def _owned(self, user_id: UUID) -> list[Transaction]:
        return [t for t in self.transactions if t.user_id == user_id]


class FakeCategoryLookup:
    """Implementa `CategoryLookup` com a mesma regra de visibilidade do repositório."""

    def __init__(self, categories: Sequence[Category] = ()) -> None:
        self.categories = list(categories)

    async def get_visible(self, category_id: UUID, user_id: UUID) -> Category | None:
        return next(
            (
                category
                for category in self.categories
                if category.id == category_id
                and (category.user_id is None or category.user_id == user_id)
            ),
            None,
        )


class FakeUnitOfWork:
    """Transação de mentira: conta os commits e sabe falhar sob comando."""

    def __init__(self, *, fails_with: Exception | None = None) -> None:
        self._fails_with = fails_with
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1
        if self._fails_with is not None:
            raise self._fails_with

    async def rollback(self) -> None:
        self.rollbacks += 1


def build_service(
    store: FakeTransactionStore,
    lookup: FakeCategoryLookup,
    unit_of_work: FakeUnitOfWork,
    clock: Clock | None = None,
) -> TransactionService:
    # As anotações forçam a checagem de que os dubles satisfazem os Protocol.
    transactions: TransactionStore = store
    categories: CategoryLookup = lookup
    work: UnitOfWork = unit_of_work
    return TransactionService(
        unit_of_work=work,
        transactions=transactions,
        categories=categories,
        clock=clock or clock_at(datetime(2026, 9, 5, 12, 0, tzinfo=UTC)),
    )


@pytest.fixture
def user() -> User:
    return make_user()


# ------------------------------------------------------------------- CREATE


async def test_create_stores_the_transaction_and_commits(user: User) -> None:
    category = make_category(user_id=user.id)
    store, work = FakeTransactionStore(), FakeUnitOfWork()
    service = build_service(store, FakeCategoryLookup([category]), work)

    created = await service.create(
        user,
        TransactionCreateIn(
            amount=Decimal("42.50"),
            category_id=category.id,
            occurred_on=date(2026, 9, 3),
            description="Feira",
        ),
    )

    assert created.user_id == user.id
    assert created.amount == Decimal("42.50")
    assert created.occurred_on == date(2026, 9, 3)
    assert created.description == "Feira"
    assert store.added == [created]
    assert work.commits == 1


async def test_create_takes_the_kind_from_the_category(user: User) -> None:
    """Não há campo `kind` de entrada: escolher a categoria é escolher o tipo."""
    salario = make_category(user_id=None, name="Salário", kind=CategoryKind.INCOME)
    service = build_service(FakeTransactionStore(), FakeCategoryLookup([salario]), FakeUnitOfWork())

    created = await service.create(
        user, TransactionCreateIn(amount=Decimal("5000.00"), category_id=salario.id)
    )

    assert created.kind is CategoryKind.INCOME


async def test_create_accepts_a_global_category(user: User) -> None:
    """A categoria do sistema serve a todo mundo — é metade do que a fase 2 entregou."""
    global_category = make_category(user_id=None, name="Moradia")
    service = build_service(
        FakeTransactionStore(), FakeCategoryLookup([global_category]), FakeUnitOfWork()
    )

    created = await service.create(
        user, TransactionCreateIn(amount=Decimal("1200.00"), category_id=global_category.id)
    )

    assert created.category_id == global_category.id
    assert created.category.is_global


async def test_create_refuses_a_category_of_another_user(user: User) -> None:
    """Categoria de terceiro e categoria inexistente têm a mesma resposta.

    A recusa não confirma que a categoria existe — é a mesma razão da mensagem
    única em credencial inválida.
    """
    alheia = make_category(user_id=uuid4(), name="Barco")
    store, work = FakeTransactionStore(), FakeUnitOfWork()
    service = build_service(store, FakeCategoryLookup([alheia]), work)

    with pytest.raises(InvalidCategoryError):
        await service.create(
            user, TransactionCreateIn(amount=Decimal("10.00"), category_id=alheia.id)
        )

    assert store.added == [], "nada pode ter sido gravado"
    assert work.commits == 0


async def test_create_refuses_a_category_that_does_not_exist(user: User) -> None:
    service = build_service(FakeTransactionStore(), FakeCategoryLookup(), FakeUnitOfWork())

    with pytest.raises(InvalidCategoryError):
        await service.create(
            user, TransactionCreateIn(amount=Decimal("10.00"), category_id=uuid4())
        )


async def test_create_without_a_date_uses_today_from_the_clock(user: User) -> None:
    """ "Hoje" vem do relógio da aplicação, nunca de `date.today()`."""
    category = make_category(user_id=user.id)
    service = build_service(
        FakeTransactionStore(),
        FakeCategoryLookup([category]),
        FakeUnitOfWork(),
        clock=clock_at(datetime(2026, 3, 14, 15, 0, tzinfo=UTC)),
    )

    created = await service.create(
        user, TransactionCreateIn(amount=Decimal("10.00"), category_id=category.id)
    )

    assert created.occurred_on == date(2026, 3, 14)


async def test_today_is_resolved_in_the_application_timezone(user: User) -> None:
    """23:30 em São Paulo já é o dia seguinte em UTC — a competência é a local.

    Sem isto, tudo lançado depois das 21h cairia no dia errado.
    """
    category = make_category(user_id=user.id)
    service = build_service(
        FakeTransactionStore(),
        FakeCategoryLookup([category]),
        FakeUnitOfWork(),
        clock=clock_at(datetime(2026, 3, 15, 2, 30, tzinfo=UTC)),
    )

    created = await service.create(
        user, TransactionCreateIn(amount=Decimal("10.00"), category_id=category.id)
    )

    assert created.occurred_on == date(2026, 3, 14)


async def test_create_rolls_back_when_the_category_vanishes_mid_flight(user: User) -> None:
    """A checagem prévia tem janela de corrida; quem decide é o banco."""
    category = make_category(user_id=user.id)
    work = FakeUnitOfWork(fails_with=integrity_error(FK_CATEGORY))
    service = build_service(FakeTransactionStore(), FakeCategoryLookup([category]), work)

    with pytest.raises(InvalidCategoryError):
        await service.create(
            user, TransactionCreateIn(amount=Decimal("10.00"), category_id=category.id)
        )

    assert work.rollbacks == 1


# --------------------------------------------------------------------- GET


async def test_get_returns_the_own_transaction(user: User) -> None:
    category = make_category(user_id=user.id)
    transaction = make_transaction(user_id=user.id, category=category)
    service = build_service(
        FakeTransactionStore([transaction]), FakeCategoryLookup([category]), FakeUnitOfWork()
    )

    assert await service.get(user, transaction.id) is transaction


async def test_a_transaction_of_another_user_is_not_found(user: User) -> None:
    """404 e não 403: um 403 confirmaria que o lançamento existe."""
    alheia = make_category(user_id=uuid4())
    de_outro = make_transaction(user_id=alheia.user_id or uuid4(), category=alheia)
    service = build_service(
        FakeTransactionStore([de_outro]), FakeCategoryLookup([alheia]), FakeUnitOfWork()
    )

    with pytest.raises(TransactionNotFoundError):
        await service.get(user, de_outro.id)


async def test_a_transaction_that_does_not_exist_is_not_found(user: User) -> None:
    service = build_service(FakeTransactionStore(), FakeCategoryLookup(), FakeUnitOfWork())

    with pytest.raises(TransactionNotFoundError):
        await service.get(user, uuid4())


# ------------------------------------------------------------------- UPDATE


async def test_update_changes_only_what_was_sent(user: User) -> None:
    category = make_category(user_id=user.id)
    transaction = make_transaction(
        user_id=user.id, category=category, amount="10.00", description="Feira"
    )
    work = FakeUnitOfWork()
    service = build_service(
        FakeTransactionStore([transaction]), FakeCategoryLookup([category]), work
    )

    updated = await service.update(
        user, transaction.id, TransactionUpdateIn(amount=Decimal("99.90"))
    )

    assert updated.amount == Decimal("99.90")
    assert updated.description == "Feira", "o que não foi enviado não muda"
    assert updated.occurred_on == date(2026, 9, 1)
    assert work.commits == 1


async def test_changing_the_category_changes_the_kind(user: User) -> None:
    """O tipo acompanha a categoria porque não existe uma segunda fonte para ele."""
    despesa = make_category(user_id=user.id, name="Mercado", kind=CategoryKind.EXPENSE)
    receita = make_category(user_id=None, name="Salário", kind=CategoryKind.INCOME)
    transaction = make_transaction(user_id=user.id, category=despesa)
    service = build_service(
        FakeTransactionStore([transaction]),
        FakeCategoryLookup([despesa, receita]),
        FakeUnitOfWork(),
    )

    updated = await service.update(
        user, transaction.id, TransactionUpdateIn(category_id=receita.id)
    )

    assert updated.category_id == receita.id
    assert updated.category is receita, "a categoria carregada acompanha o id"
    assert updated.kind is CategoryKind.INCOME


async def test_update_refuses_a_category_of_another_user(user: User) -> None:
    minha = make_category(user_id=user.id)
    alheia = make_category(user_id=uuid4(), name="Barco")
    transaction = make_transaction(user_id=user.id, category=minha)
    work = FakeUnitOfWork()
    service = build_service(
        FakeTransactionStore([transaction]), FakeCategoryLookup([minha, alheia]), work
    )

    with pytest.raises(InvalidCategoryError):
        await service.update(user, transaction.id, TransactionUpdateIn(category_id=alheia.id))

    assert transaction.category_id == minha.id, "o lançamento não pode ter sido tocado"
    assert work.commits == 0


async def test_update_of_another_users_transaction_is_not_found(user: User) -> None:
    alheia = make_category(user_id=uuid4())
    de_outro = make_transaction(user_id=uuid4(), category=alheia)
    service = build_service(
        FakeTransactionStore([de_outro]), FakeCategoryLookup([alheia]), FakeUnitOfWork()
    )

    with pytest.raises(TransactionNotFoundError):
        await service.update(user, de_outro.id, TransactionUpdateIn(amount=Decimal("1.00")))


# ------------------------------------------------------------------- DELETE


async def test_delete_removes_the_own_transaction(user: User) -> None:
    category = make_category(user_id=user.id)
    transaction = make_transaction(user_id=user.id, category=category)
    store, work = FakeTransactionStore([transaction]), FakeUnitOfWork()
    service = build_service(store, FakeCategoryLookup([category]), work)

    await service.delete(user, transaction.id)

    assert store.deleted == [transaction]
    assert store.transactions == []
    assert work.commits == 1


async def test_delete_of_another_users_transaction_is_not_found(user: User) -> None:
    alheia = make_category(user_id=uuid4())
    de_outro = make_transaction(user_id=uuid4(), category=alheia)
    store = FakeTransactionStore([de_outro])
    service = build_service(store, FakeCategoryLookup([alheia]), FakeUnitOfWork())

    with pytest.raises(TransactionNotFoundError):
        await service.delete(user, de_outro.id)

    assert store.deleted == [], "o lançamento alheio continua onde estava"


# --------------------------------------------------------------------- LIST


async def test_list_returns_the_page_and_the_total(user: User) -> None:
    category = make_category(user_id=user.id)
    store = FakeTransactionStore(
        [make_transaction(user_id=user.id, category=category) for _ in range(5)]
    )
    service = build_service(store, FakeCategoryLookup([category]), FakeUnitOfWork())

    page = await service.list_transactions(user, filters=TransactionFilters(), limit=2, offset=0)

    assert len(page.items) == 2
    assert page.total == 5, "o total é do filtro inteiro, não da página"
    assert page.limit == 2
    assert page.offset == 0


async def test_list_forwards_the_filters_and_the_owner_to_the_repository(user: User) -> None:
    """Filtrar é construir query: a decisão é do repositório, não do serviço."""
    category = make_category(user_id=user.id)
    store = FakeTransactionStore([make_transaction(user_id=user.id, category=category)])
    service = build_service(store, FakeCategoryLookup([category]), FakeUnitOfWork())
    filters = TransactionFilters(
        kind=CategoryKind.EXPENSE,
        category_id=category.id,
        occurred_from=date(2026, 9, 1),
        occurred_to=date(2026, 9, 30),
    )

    await service.list_transactions(user, filters=filters, limit=10, offset=20)

    assert store.last_query == {
        "user_id": user.id,
        "filters": filters,
        "limit": 10,
        "offset": 20,
    }


async def test_list_never_reaches_another_users_transactions(user: User) -> None:
    """O escopo é do repositório; o duble o reproduz para o serviço não o burlar."""
    minha = make_category(user_id=user.id)
    alheia = make_category(user_id=uuid4())
    store = FakeTransactionStore(
        [
            make_transaction(user_id=user.id, category=minha),
            make_transaction(user_id=uuid4(), category=alheia),
            make_transaction(user_id=uuid4(), category=alheia),
        ]
    )
    service = build_service(store, FakeCategoryLookup([minha]), FakeUnitOfWork())

    page = await service.list_transactions(user, filters=TransactionFilters(), limit=50, offset=0)

    assert page.total == 1
    assert [item.user_id for item in page.items] == [user.id]
