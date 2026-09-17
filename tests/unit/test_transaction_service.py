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
from core.errors import (
    InvalidCategoryError,
    TransactionAlreadyRecurringError,
    TransactionNotFoundError,
)
from models.category import Category, CategoryKind
from models.recurring_transaction import RecurringTransaction
from models.transaction import FK_CATEGORY, Transaction
from models.user import Role, User
from repositories.transaction_repository import TransactionFilters
from schemas.transaction import RecurrenceIn, TransactionCreateIn, TransactionUpdateIn
from services.transaction_service import (
    CategoryLookup,
    RecurrenceSink,
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
        self.locked_reads = 0

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

    async def get_owned(
        self, transaction_id: UUID, user_id: UUID, *, lock: bool = False
    ) -> Transaction | None:
        """O duble impõe o mesmo escopo do repositório: nada de outro dono sai daqui."""
        if lock:
            self.locked_reads += 1
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


class FakeRecurrenceSink:
    """Implementa `RecurrenceSink`: guarda as recorrências que o serviço criou."""

    def __init__(self) -> None:
        self.added: list[RecurringTransaction] = []

    def add(self, rule: RecurringTransaction) -> None:
        self.added.append(rule)


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
    sink: FakeRecurrenceSink | None = None,
) -> TransactionService:
    # As anotações forçam a checagem de que os dubles satisfazem os Protocol.
    transactions: TransactionStore = store
    categories: CategoryLookup = lookup
    work: UnitOfWork = unit_of_work
    recurrences: RecurrenceSink = sink or FakeRecurrenceSink()
    return TransactionService(
        unit_of_work=work,
        transactions=transactions,
        categories=categories,
        recurrences=recurrences,
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


# ------------------------------------------------------- CREATE com recorrência


async def test_a_plain_create_starts_no_recurrence(user: User) -> None:
    category = make_category(user_id=user.id)
    sink = FakeRecurrenceSink()
    service = build_service(
        FakeTransactionStore(), FakeCategoryLookup([category]), FakeUnitOfWork(), sink=sink
    )

    created = await service.create(
        user, TransactionCreateIn(amount=Decimal("10.00"), category_id=category.id)
    )

    assert sink.added == []
    assert created.recurring_transaction_id is None


async def test_create_with_recurrence_starts_the_rule_in_the_next_month(user: User) -> None:
    """Netflix de 3/9 com "todo dia 5" não se repete em 5/9; regra e lançamento num commit."""
    category = make_category(user_id=user.id, name="Streaming")
    store, work, sink = FakeTransactionStore(), FakeUnitOfWork(), FakeRecurrenceSink()
    service = build_service(
        store,
        FakeCategoryLookup([category]),
        work,
        clock=clock_at(datetime(2026, 9, 3, 12, 0, tzinfo=UTC)),
        sink=sink,
    )

    created = await service.create(
        user,
        TransactionCreateIn(
            amount=Decimal("39.90"),
            category_id=category.id,
            occurred_on=date(2026, 9, 3),
            description="Netflix",
            recurrence=RecurrenceIn(day_of_month=5),
        ),
    )

    [rule] = sink.added
    assert rule.next_occurrence_on == date(2026, 10, 5)
    assert (rule.user_id, rule.category_id, rule.amount, rule.description) == (
        user.id,
        category.id,
        Decimal("39.90"),
        "Netflix",
    )
    assert rule.day_of_month == 5
    assert rule.is_active
    assert created.recurring_transaction_id == rule.id
    assert store.added == [created], "nada venceu: só o lançamento pedido entra"
    assert work.commits == 1


async def test_a_future_transaction_starts_its_recurrence_after_its_own_month(user: User) -> None:
    category = make_category(user_id=user.id)
    sink = FakeRecurrenceSink()
    service = build_service(
        FakeTransactionStore(), FakeCategoryLookup([category]), FakeUnitOfWork(), sink=sink
    )

    await service.create(
        user,
        TransactionCreateIn(
            amount=Decimal("100.00"),
            category_id=category.id,
            occurred_on=date(2026, 12, 20),
            recurrence=RecurrenceIn(day_of_month=20),
        ),
    )

    assert sink.added[0].next_occurrence_on == date(2027, 1, 20)


async def test_a_retroactive_transaction_does_not_backfill_the_months_in_between(
    user: User,
) -> None:
    """Julho lançado em setembro, "todo dia 10": a regra conta de hoje, sem agosto."""
    category = make_category(user_id=user.id)
    store, sink = FakeTransactionStore(), FakeRecurrenceSink()
    service = build_service(
        store,
        FakeCategoryLookup([category]),
        FakeUnitOfWork(),
        clock=clock_at(datetime(2026, 9, 17, 12, 0, tzinfo=UTC)),
        sink=sink,
    )

    await service.create(
        user,
        TransactionCreateIn(
            amount=Decimal("80.00"),
            category_id=category.id,
            occurred_on=date(2026, 7, 10),
            recurrence=RecurrenceIn(day_of_month=10),
        ),
    )

    assert sink.added[0].next_occurrence_on == date(2026, 10, 10)
    assert len(store.added) == 1


async def test_a_recurrence_whose_first_date_is_today_registers_it_now(user: User) -> None:
    """Agosto lançado hoje, 17/9, "todo dia 17": setembro já venceu e entra na mesma resposta."""
    category = make_category(user_id=user.id)
    store, sink = FakeTransactionStore(), FakeRecurrenceSink()
    service = build_service(
        store,
        FakeCategoryLookup([category]),
        FakeUnitOfWork(),
        clock=clock_at(datetime(2026, 9, 17, 12, 0, tzinfo=UTC)),
        sink=sink,
    )

    created = await service.create(
        user,
        TransactionCreateIn(
            amount=Decimal("80.00"),
            category_id=category.id,
            occurred_on=date(2026, 8, 17),
            recurrence=RecurrenceIn(day_of_month=17),
        ),
    )

    [rule] = sink.added
    assert [t.occurred_on for t in store.added] == [date(2026, 8, 17), date(2026, 9, 17)]
    assert {t.recurring_transaction_id for t in store.added} == {rule.id}
    assert store.added[0] is created
    assert rule.next_occurrence_on == date(2026, 10, 17)


async def test_create_with_recurrence_refuses_an_invisible_category_before_creating_the_rule(
    user: User,
) -> None:
    alheia = make_category(user_id=uuid4(), name="Barco")
    sink, work = FakeRecurrenceSink(), FakeUnitOfWork()
    service = build_service(FakeTransactionStore(), FakeCategoryLookup([alheia]), work, sink=sink)

    with pytest.raises(InvalidCategoryError):
        await service.create(
            user,
            TransactionCreateIn(
                amount=Decimal("10.00"),
                category_id=alheia.id,
                recurrence=RecurrenceIn(day_of_month=5),
            ),
        )

    assert sink.added == []
    assert work.commits == 0


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


async def test_update_ignores_the_fields_that_came_as_null(user: User) -> None:
    """Nulo é "não mexa": é o corpo que um formulário manda com um campo mexido.

    Sem isto, editar o valor zerava `description` e `occurred_on` — colunas
    NOT NULL, e a recusa do banco saía como `409 conflict`.
    """
    category = make_category(user_id=user.id)
    transaction = make_transaction(
        user_id=user.id, category=category, amount="10.00", description="Feira"
    )
    service = build_service(
        FakeTransactionStore([transaction]), FakeCategoryLookup([category]), FakeUnitOfWork()
    )
    data = TransactionUpdateIn.model_validate(
        {"amount": "99.90", "category_id": None, "occurred_on": None, "description": None}
    )

    updated = await service.update(user, transaction.id, data)

    assert updated.amount == Decimal("99.90")
    assert updated.description == "Feira"
    assert updated.occurred_on == date(2026, 9, 1)
    assert updated.category is category


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


# ------------------------------------------------------- UPDATE com recorrência


async def test_update_can_turn_an_existing_transaction_into_a_recurrence(user: User) -> None:
    """Avulsa de 3/9 vira "todo dia 5": regra em outubro, com a linha lida travada."""
    category = make_category(user_id=user.id, name="Streaming")
    transaction = make_transaction(
        user_id=user.id, category=category, amount="39.90", occurred_on=date(2026, 9, 3)
    )
    store, work, sink = FakeTransactionStore([transaction]), FakeUnitOfWork(), FakeRecurrenceSink()
    service = build_service(
        store,
        FakeCategoryLookup([category]),
        work,
        clock=clock_at(datetime(2026, 9, 17, 12, 0, tzinfo=UTC)),
        sink=sink,
    )

    updated = await service.update(
        user, transaction.id, TransactionUpdateIn(recurrence=RecurrenceIn(day_of_month=5))
    )

    [rule] = sink.added
    assert rule.next_occurrence_on == date(2026, 10, 5)
    assert (rule.amount, rule.category_id, rule.day_of_month) == (
        Decimal("39.90"),
        category.id,
        5,
    )
    assert updated.recurring_transaction_id == rule.id
    assert updated.occurred_on == date(2026, 9, 3), "a recorrência não mexe no lançamento"
    assert store.locked_reads == 1
    assert work.commits == 1


async def test_the_rule_copies_the_transaction_as_edited_in_the_same_request(user: User) -> None:
    """Valor, categoria e data mudam junto com o pedido: a regra nasce do lançamento novo."""
    antiga = make_category(user_id=user.id, name="Mercado")
    streaming = make_category(user_id=user.id, name="Streaming")
    transaction = make_transaction(
        user_id=user.id, category=antiga, amount="10.00", occurred_on=date(2026, 9, 3)
    )
    sink = FakeRecurrenceSink()
    service = build_service(
        FakeTransactionStore([transaction]),
        FakeCategoryLookup([antiga, streaming]),
        FakeUnitOfWork(),
        clock=clock_at(datetime(2026, 9, 17, 12, 0, tzinfo=UTC)),
        sink=sink,
    )

    await service.update(
        user,
        transaction.id,
        TransactionUpdateIn(
            amount=Decimal("55.90"),
            category_id=streaming.id,
            occurred_on=date(2026, 11, 20),
            description="Netflix",
            recurrence=RecurrenceIn(day_of_month=20),
        ),
    )

    [rule] = sink.added
    assert (rule.amount, rule.category, rule.description) == (
        Decimal("55.90"),
        streaming,
        "Netflix",
    )
    assert rule.next_occurrence_on == date(2026, 12, 20)


async def test_making_an_old_transaction_recurrent_registers_what_is_due_today(
    user: User,
) -> None:
    category = make_category(user_id=user.id)
    transaction = make_transaction(
        user_id=user.id, category=category, occurred_on=date(2026, 8, 17)
    )
    store, sink = FakeTransactionStore([transaction]), FakeRecurrenceSink()
    service = build_service(
        store,
        FakeCategoryLookup([category]),
        FakeUnitOfWork(),
        clock=clock_at(datetime(2026, 9, 17, 12, 0, tzinfo=UTC)),
        sink=sink,
    )

    await service.update(
        user, transaction.id, TransactionUpdateIn(recurrence=RecurrenceIn(day_of_month=17))
    )

    assert [t.occurred_on for t in store.added] == [date(2026, 9, 17)]
    assert store.added[0].recurring_transaction_id == sink.added[0].id


async def test_a_transaction_that_already_recurs_refuses_a_second_rule(user: User) -> None:
    """Uma segunda regra lançaria o mesmo gasto duas vezes por mês: 409, e nada muda."""
    category = make_category(user_id=user.id)
    transaction = make_transaction(user_id=user.id, category=category, amount="10.00")
    transaction.recurring_transaction_id = uuid4()
    work, sink = FakeUnitOfWork(), FakeRecurrenceSink()
    service = build_service(
        FakeTransactionStore([transaction]), FakeCategoryLookup([category]), work, sink=sink
    )

    with pytest.raises(TransactionAlreadyRecurringError):
        await service.update(
            user,
            transaction.id,
            TransactionUpdateIn(amount=Decimal("99.00"), recurrence=RecurrenceIn(day_of_month=5)),
        )

    assert sink.added == []
    assert transaction.amount == Decimal("10.00"), "a recusa vem antes de qualquer mudança"
    assert work.commits == 0


async def test_a_plain_update_neither_locks_nor_touches_the_recurrence(user: User) -> None:
    category = make_category(user_id=user.id)
    transaction = make_transaction(user_id=user.id, category=category)
    rule_id = uuid4()
    transaction.recurring_transaction_id = rule_id
    store, sink = FakeTransactionStore([transaction]), FakeRecurrenceSink()
    service = build_service(store, FakeCategoryLookup([category]), FakeUnitOfWork(), sink=sink)

    updated = await service.update(
        user, transaction.id, TransactionUpdateIn(amount=Decimal("1.00"), recurrence=None)
    )

    assert updated.recurring_transaction_id == rule_id
    assert sink.added == []
    assert store.locked_reads == 0


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
