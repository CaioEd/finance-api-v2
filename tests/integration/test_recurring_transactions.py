"""Recorrências contra Postgres de verdade.

O que só existe aqui, e por isso justifica o container:

- **a trava do agendador.** `FOR UPDATE ... SKIP LOCKED` é o que deixa duas
  réplicas rodarem a mesma rodada sem lançar o mesmo mês duas vezes. O SQLite
  da suíte de API ignora a cláusula, então lá a garantia não tem como ser
  verificada — só a sua ausência de efeito colateral;
- **as constraints pelo nome.** `409 category_in_use` para a categoria presa a
  uma recorrência depende de o Postgres dizer qual FK falhou; o `SET NULL` e o
  `CHECK` do dia são DDL da migration, não do `Base.metadata`;
- **o schema da migration**, com o índice parcial do agendador.

Quase tudo roda na transação com rollback de cada teste (`db_session`). A
exceção é a trava: provar que uma conexão pula a linha travada por outra exige
duas conexões enxergando o mesmo dado, e dado não comitado é invisível para a
segunda. Esses testes comitam de verdade e apagam o que criaram no fim — a
conta, que leva junto categoria, regra e lançamentos em cascata.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.clock import Clock
from core.config import Settings
from core.database import Database
from jobs.recurring_transactions import register_due_recurrences
from models.category import Category, CategoryKind
from models.recurring_transaction import RecurringTransaction
from models.transaction import Transaction
from models.user import User
from repositories.recurring_transaction_repository import RecurringTransactionRepository
from tests.factories import RegisteredUser, register_user
from tests.integration.conftest import APP_TIMEZONE

RECURRING = "/api/v1/recurring-transactions"
SAO_PAULO_NOON = 15  # 12h em São Paulo, em UTC


def clock_on(day: date) -> Clock:
    moment = datetime(day.year, day.month, day.day, SAO_PAULO_NOON, tzinfo=UTC)
    return Clock(tz=APP_TIMEZONE, instant=lambda: moment)


async def a_category(client: AsyncClient, user: RegisteredUser, name: str = "Streaming") -> str:
    response = await client.post(
        "/api/v1/categories", headers=user.auth, json={"name": name, "kind": "expense"}
    )
    response.raise_for_status()
    return str(response.json()["id"])


# ------------------------------------------------------------------ o schema


async def test_the_scheduler_index_is_partial_on_active_rules(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE indexname = 'ix_recurring_transactions_next_occurrence_on'"
        )
    )

    assert "WHERE is_active" in result.scalar_one()


async def test_the_database_refuses_a_day_outside_the_month(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """O `CHECK` da migration, por baixo da validação do schema."""
    ana = await register_user(client)
    categoria = await a_category(client, ana)

    with pytest.raises(IntegrityError, match="ck_recurring_transactions_day_of_month_range"):
        await db_session.execute(
            text(
                "INSERT INTO recurring_transactions "
                "(user_id, category_id, amount, day_of_month, next_occurrence_on) "
                "VALUES (:user_id, :category_id, 10, 32, '2026-10-01')"
            ),
            {"user_id": ana.id, "category_id": categoria},
        )


# ---------------------------------------------------------- as FKs pelo nome


async def test_a_category_used_by_a_rule_cannot_be_deleted(client: AsyncClient) -> None:
    """409 `category_in_use`, e não 500: o Postgres diz qual FK falhou, e ela é traduzida.

    A regra não tem lançamento nenhum ainda — é a recorrência sozinha que prende a categoria.
    """
    ana = await register_user(client)
    categoria = await a_category(client, ana)
    created = await client.post(
        RECURRING,
        headers=ana.auth,
        json={
            "amount": "39.90",
            "category_id": categoria,
            "day_of_month": 5,
            "starts_on": "2099-01-01",
        },
    )
    created.raise_for_status()

    response = await client.delete(f"/api/v1/categories/{categoria}", headers=ana.auth)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "category_in_use"


async def test_deleting_a_rule_unlinks_the_transactions_it_created(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    ana = await register_user(client)
    categoria = await a_category(client, ana)
    lancamento = await client.post(
        "/api/v1/transactions",
        headers=ana.auth,
        json={
            "amount": "39.90",
            "category_id": categoria,
            "occurred_on": "2099-01-05",
            "recurrence": {"day_of_month": 5},
        },
    )
    lancamento.raise_for_status()
    rule_id = lancamento.json()["recurring_transaction_id"]

    response = await client.delete(f"{RECURRING}/{rule_id}", headers=ana.auth)

    assert response.status_code == 204
    db_session.expunge_all()
    stored = await db_session.get(Transaction, UUID(lancamento.json()["id"]))
    assert stored is not None
    assert stored.recurring_transaction_id is None


async def test_lock_due_filters_active_and_due_rules_in_date_order(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A consulta do agendador, compilada para Postgres — com `FOR UPDATE OF ... SKIP LOCKED`."""
    ana = await register_user(client)
    categoria = await a_category(client, ana)
    base = {"amount": "10.00", "category_id": categoria}
    for day, starts_on in (
        (20, "2099-03-01"),
        (5, "2099-01-01"),
        (10, "2099-02-01"),
        (1, "2099-12-01"),
    ):
        created = await client.post(
            RECURRING, headers=ana.auth, json={**base, "day_of_month": day, "starts_on": starts_on}
        )
        created.raise_for_status()
    pausada = await client.post(
        RECURRING, headers=ana.auth, json={**base, "day_of_month": 2, "starts_on": "2099-01-01"}
    )
    await client.patch(
        f"{RECURRING}/{pausada.json()['id']}", headers=ana.auth, json={"is_active": False}
    )

    due = await RecurringTransactionRepository(db_session).lock_due(date(2099, 3, 31), limit=10)

    assert [rule.next_occurrence_on for rule in due] == [
        date(2099, 1, 5),
        date(2099, 2, 10),
        date(2099, 3, 20),
    ]
    assert all(rule.category.kind is CategoryKind.EXPENSE for rule in due)


# ------------------------------------------------- a trava, com dado comitado


@dataclass(frozen=True, slots=True)
class Committed:
    database: Database
    url: str
    user_id: UUID
    rule_id: UUID


@pytest.fixture
async def committed_rule(settings: Settings) -> AsyncGenerator[Committed]:
    """Uma conta com uma regra vencida, **comitada**, e apagada no fim do teste."""
    database = Database(settings)
    user_id, rule_id = uuid4(), uuid4()
    async with database.sessionmaker() as session:
        session.add(
            User(
                id=user_id,
                email=f"trava-{user_id.hex[:8]}@exemplo.com",
                username=f"trava{user_id.hex[:8]}",
                password_hash="$argon2id$nao-usado",
            )
        )
        await session.flush()
        category = Category(id=uuid4(), user_id=user_id, name="Aluguel", kind=CategoryKind.EXPENSE)
        session.add(category)
        await session.flush()
        session.add(
            RecurringTransaction(
                id=rule_id,
                user_id=user_id,
                category_id=category.id,
                amount=Decimal("1500.00"),
                description="Aluguel",
                day_of_month=10,
                next_occurrence_on=date(2099, 1, 10),
                is_active=True,
            )
        )
        await session.commit()
    try:
        yield Committed(
            database=database, url=settings.database_url, user_id=user_id, rule_id=rule_id
        )
    finally:
        async with database.sessionmaker() as session:
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()
        await database.dispose()


async def test_a_rule_locked_by_one_runner_is_skipped_by_another(committed_rule: Committed) -> None:
    """Duas réplicas na mesma rodada: a segunda não espera nem repete — pula a linha travada."""
    engine = create_async_engine(committed_rule.url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as primeira, sessions() as segunda:
            travadas = await RecurringTransactionRepository(primeira).lock_due(
                date(2099, 1, 31), limit=10
            )
            puladas = await asyncio.wait_for(
                RecurringTransactionRepository(segunda).lock_due(date(2099, 1, 31), limit=10),
                timeout=5,
            )

            assert committed_rule.rule_id in {rule.id for rule in travadas}
            assert committed_rule.rule_id not in {rule.id for rule in puladas}
            await primeira.rollback()

            liberada = await RecurringTransactionRepository(segunda).lock_due(
                date(2099, 1, 31), limit=10
            )
            assert committed_rule.rule_id in {rule.id for rule in liberada}
            await segunda.rollback()
    finally:
        await engine.dispose()


async def test_two_concurrent_runners_register_each_month_once(committed_rule: Committed) -> None:
    """O agendador inteiro, duas vezes ao mesmo tempo, contra uma regra vencida há três meses."""
    clock = clock_on(date(2099, 3, 15))

    resultados = await asyncio.gather(
        register_due_recurrences(committed_rule.database, clock, batch_size=1),
        register_due_recurrences(committed_rule.database, clock, batch_size=1),
    )

    async with committed_rule.database.sessionmaker() as session:
        rows = await session.scalars(
            select(Transaction.occurred_on)
            .where(Transaction.recurring_transaction_id == committed_rule.rule_id)
            .order_by(Transaction.occurred_on)
        )
        datas = list(rows.all())
        rule = await session.get(RecurringTransaction, committed_rule.rule_id)

    assert sum(resultados) == 3
    assert datas == [date(2099, 1, 10), date(2099, 2, 10), date(2099, 3, 10)]
    assert rule is not None
    assert rule.next_occurrence_on == date(2099, 4, 10)
