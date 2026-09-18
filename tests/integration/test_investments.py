"""Investimentos contra Postgres: o que o SQLite da suíte de API não reproduz.

Três coisas, e as três só existem de verdade aqui:

- **o schema nasce das migrations**, não de `Base.metadata`. Uma migration que
  esqueça uma coluna ou um índice reprova o build neste diretório;
- **o índice único funcional** `uq_investment_assets_type_symbol`, sobre
  `upper(symbol)`: é ele que impede o catálogo de guardar "petr4" e "PETR4"
  como dois ativos, e quem o impõe é o banco;
- **o `ON DELETE SET NULL`** de `transactions.investment_id`. O SQLite só
  respeita FK com `PRAGMA foreign_keys`, e nenhuma `relationship` do ORM
  cobre essa ponta — quem anula a coluna ao excluir a posição é o Postgres.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.investment import Investment, InvestmentAsset, InvestmentType, RateIndex
from models.transaction import Transaction
from repositories.investment_quote_repository import InvestmentQuoteRepository
from tests.factories import RegisteredUser, register_user

INVESTMENTS = "/api/v1/investments"


async def a_category(client: AsyncClient, user: RegisteredUser, *, kind: str = "expense") -> str:
    response = await client.post(
        "/api/v1/categories",
        headers=user.auth,
        json={"name": f"Investimentos {kind}", "kind": kind},
    )
    response.raise_for_status()
    return str(response.json()["id"])


async def a_stock(client: AsyncClient, user: RegisteredUser, **overrides: Any) -> dict[str, Any]:
    response = await client.post(
        INVESTMENTS,
        headers=user.auth,
        json={
            "type": "br_stock",
            "symbol": "PETR4",
            "quantity": "100",
            "average_price": "30.00",
            **overrides,
        },
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def test_the_catalog_is_shared_between_users(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Cem usuários com PETR4 custam uma requisição por rodada, não cem."""
    ana = await register_user(client)
    bruno = await register_user(client, email="bruno@exemplo.com", username="bruno")

    first = await a_stock(client, ana)
    second = await a_stock(client, bruno, symbol="petr4")

    assert first["asset"]["id"] == second["asset"]["id"]
    assert first["asset"]["symbol"] == "PETR4"
    total = await db_session.scalar(
        select(text("count(*)"))
        .select_from(InvestmentAsset)
        .where(InvestmentAsset.symbol == "PETR4")
    )
    assert total == 1


async def test_the_unique_index_compares_the_symbol_without_case(db_session: AsyncSession) -> None:
    """A segunda linha é recusada pelo banco, não por um SELECT prévio do serviço."""
    db_session.add(
        InvestmentAsset(
            type=InvestmentType.BR_STOCK, symbol="VALE3", name="Vale ON", currency="BRL"
        )
    )
    await db_session.flush()

    db_session.add(
        InvestmentAsset(
            type=InvestmentType.BR_STOCK, symbol="vale3", name="Vale ON", currency="BRL"
        )
    )

    with pytest.raises(IntegrityError) as refusal:
        await db_session.flush()
    assert "uq_investment_assets_type_symbol" in str(refusal.value.orig)


async def test_the_same_symbol_in_another_market_is_a_different_asset(
    db_session: AsyncSession,
) -> None:
    """O índice é por `(type, upper(symbol))`: uma ação e uma cripto homônimas coexistem."""
    db_session.add(
        InvestmentAsset(type=InvestmentType.BR_STOCK, symbol="XPTO", name="XPTO", currency="BRL")
    )
    db_session.add(
        InvestmentAsset(type=InvestmentType.CRYPTO, symbol="XPTO", name="XPTO", currency="USD")
    )

    await db_session.flush()  # não levanta


async def test_deleting_the_position_nullifies_the_transaction_instead_of_removing_it(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """O aporte saiu da conta de verdade; apagá-lo falsificaria o saldo do mês."""
    user = await register_user(client)
    investment = await a_stock(client, user)
    category = await a_category(client, user)
    contribution = await client.post(
        f"{INVESTMENTS}/{investment['id']}/contributions",
        headers=user.auth,
        json={"amount": "500.00", "category_id": category, "quantity": "10"},
    )
    assert contribution.status_code == 201, contribution.text

    removed = await client.delete(f"{INVESTMENTS}/{investment['id']}", headers=user.auth)

    assert removed.status_code == 204
    transaction = await db_session.scalar(
        select(Transaction).where(Transaction.id == UUID(contribution.json()["transaction"]["id"]))
    )
    assert transaction is not None, "o lançamento foi apagado junto com a posição"
    assert transaction.investment_id is None
    assert transaction.amount == Decimal("500.00")


async def test_deleting_the_account_takes_the_positions_along(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """`ON DELETE CASCADE` em `user_id`: a conta excluída não deixa posição órfã."""
    user = await register_user(client)
    await a_stock(client, user)

    # `request("DELETE", ...)`: o `delete` do httpx não aceita corpo, e esta
    # rota exige a senha — ver `tests/integration/test_users.py`.
    removed = await client.request(
        "DELETE", "/api/v1/users/me", headers=user.auth, json={"password": user.password}
    )

    assert removed.status_code == 204
    remaining = await db_session.scalars(
        select(Investment).where(Investment.user_id == UUID(user.id))
    )
    assert remaining.all() == []
    # O catálogo sobrevive a quem o povoou: a cotação não é de ninguém.
    asset = await db_session.scalars(
        select(InvestmentAsset).where(InvestmentAsset.symbol == "PETR4")
    )
    assert asset.first() is not None


async def test_the_money_keeps_its_decimal_places(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """`NUMERIC(14,2)` e `NUMERIC(24,8)`: float perderia as casas de baixo do bitcoin."""
    user = await register_user(client)
    response = await client.post(
        INVESTMENTS,
        headers=user.auth,
        json={
            "type": "crypto",
            "symbol": "BTC",
            "quantity": "0.01234567",
            "average_price": "78058.65",
            "invested_amount": "4961.37",
        },
    )
    assert response.status_code == 201, response.text

    stored = await db_session.scalar(
        select(Investment).where(Investment.id == UUID(response.json()["id"]))
    )
    assert stored is not None
    assert stored.quantity == Decimal("0.01234567")
    assert stored.invested_amount == Decimal("4961.37")
    assert response.json()["quantity"] == "0.01234567"


# ------------------------------------------- o agendador contra Postgres


async def test_the_bulk_revalue_keeps_the_numeric_precision(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """O `UPDATE` em lote calcula `quantidade x preço` **no banco**, não em Python.

    É a diferença entre uma instrução e uma por dono, e é justamente por isso
    que o arredondamento passa a ser do Postgres: `round(numeric, 2)` sobre um
    produto de oito casas. Em float, `0,1 + 0,2` já não fecha.
    """
    user = await register_user(client)
    response = await client.post(
        INVESTMENTS,
        headers=user.auth,
        json={
            "type": "crypto",
            "symbol": "BTC",
            "quantity": "0.01234567",
            "average_price": "70000.00",
            "invested_amount": "5000.00",
        },
    )
    assert response.status_code == 201, response.text
    investment_id = UUID(response.json()["id"])
    asset_id = UUID(response.json()["asset"]["id"])

    changed = await InvestmentQuoteRepository(db_session).revalue_positions(
        asset_id,
        # 0,01234567 x 78058,65 x 5,00 = 4818,4316677275 -> 4818,43
        price_in_brl=Decimal("78058.65") * Decimal("5.00"),
        moment=datetime(2026, 9, 18, 15, 0, tzinfo=UTC),
    )
    await db_session.flush()

    assert changed == 1
    stored = await db_session.get(Investment, investment_id)
    assert stored is not None
    await db_session.refresh(stored)
    assert stored.current_value == Decimal("4818.43")


async def test_a_rate_has_one_row_per_index_however_many_times_it_is_read(
    db_session: AsyncSession,
) -> None:
    """A chave primária é o próprio índice: a leitura nova substitui a anterior.

    O flush entre as duas é o que cada rodada faz de verdade — ela comita no fim
    e a seguinte abre sessão nova. Sem ele, a sessão (que roda com
    `autoflush=False`) não enxergaria a primeira e criaria uma segunda linha.
    """
    repository = InvestmentQuoteRepository(db_session)

    await repository.save_rate(
        RateIndex.CDI,
        annual_percent=Decimal("13.65"),
        reference_date=date(2026, 9, 17),
        fetched_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
    )
    await db_session.flush()
    await repository.save_rate(
        RateIndex.CDI,
        annual_percent=Decimal("13.70"),
        reference_date=date(2026, 9, 18),
        fetched_at=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
    )
    await db_session.flush()

    rates = await repository.rates()
    assert list(rates) == [RateIndex.CDI]
    assert rates[RateIndex.CDI].annual_percent == Decimal("13.70")
    assert rates[RateIndex.CDI].reference_date == date(2026, 9, 18)


async def test_the_queue_puts_what_was_never_quoted_at_the_front(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """`NULLS FIRST` é explícito: o default do Postgres para `ASC` é `NULLS LAST`.

    Sem ele, o ativo recém-cadastrado só seria cotado depois de todos os que já
    têm preço — o oposto do que se quer.
    """
    user = await register_user(client)
    await a_stock(client, user, symbol="AAA3")
    await a_stock(client, user, symbol="BBB3")
    repository = InvestmentQuoteRepository(db_session)
    já_cotado = await db_session.scalars(
        select(InvestmentAsset).where(InvestmentAsset.symbol == "AAA3")
    )
    asset = já_cotado.one()
    asset.quoted_at = datetime(2026, 9, 18, 15, 0, tzinfo=UTC)
    await db_session.flush()

    fila = await repository.stale_assets(types=(InvestmentType.BR_STOCK,), limit=2)

    assert [item.symbol for item in fila] == ["BBB3", "AAA3"]
