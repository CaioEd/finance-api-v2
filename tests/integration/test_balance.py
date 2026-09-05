"""Saldos contra Postgres de verdade.

O que só existe aqui, e por isso justifica o container: a **agregação**. O
duble do teste unitário reproduz a regra de recorte; ele não tem como afirmar
que `SUM(...) FILTER (WHERE categories.kind = ...)` separa receita de despesa,
que o `JOIN` traz o `kind` da categoria certa, que o intervalo é fechado nas
duas pontas, ou que `WHERE user_id = ...` de fato existe na consulta.

Esse último é o teste que mais importa. Num agregado, o escopo esquecido não
vaza uma linha com nome e id — vaza um número, e nada na resposta denuncia de
onde ele veio. Por isso todo cenário aqui tem uma segunda pessoa lançando no
mesmo mês, e não só o caso feliz de uma conta sozinha.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from core.clock import Clock, shift_month
from tests.factories import RegisteredUser, register_user

BALANCE = "/api/v1/balance"


async def a_category(client: AsyncClient, user: RegisteredUser, *, kind: str, name: str) -> str:
    response = await client.post(
        "/api/v1/categories", headers=user.auth, json={"name": name, "kind": kind}
    )
    response.raise_for_status()
    return str(response.json()["id"])


async def a_transaction(
    client: AsyncClient,
    user: RegisteredUser,
    *,
    category_id: str,
    amount: str,
    occurred_on: str,
) -> None:
    response = await client.post(
        "/api/v1/transactions",
        headers=user.auth,
        json={"amount": amount, "category_id": category_id, "occurred_on": occurred_on},
    )
    response.raise_for_status()


class Ledger:
    """Uma conta com as duas categorias prontas, para o teste falar em dinheiro."""

    def __init__(self, client: AsyncClient, user: RegisteredUser, income: str, expense: str):
        self._client = client
        self.user = user
        self._income = income
        self._expense = expense

    @classmethod
    async def of(cls, client: AsyncClient, user: RegisteredUser, suffix: str = "") -> Ledger:
        return cls(
            client,
            user,
            await a_category(client, user, kind="income", name=f"Salário{suffix}"),
            await a_category(client, user, kind="expense", name=f"Mercado{suffix}"),
        )

    async def earn(self, amount: str, on: str) -> None:
        await a_transaction(
            self._client, self.user, category_id=self._income, amount=amount, occurred_on=on
        )

    async def spend(self, amount: str, on: str) -> None:
        await a_transaction(
            self._client, self.user, category_id=self._expense, amount=amount, occurred_on=on
        )


@pytest.fixture
async def ana(client: AsyncClient) -> Ledger:
    return await Ledger.of(client, await register_user(client))


@pytest.fixture
async def bruno(client: AsyncClient) -> Ledger:
    """A segunda pessoa. Existe em todo cenário para o vazamento ter como aparecer."""
    user = await register_user(client, email="bruno@exemplo.com", username="bruno")
    return await Ledger.of(client, user, suffix=" do Bruno")


# ------------------------------------------------------------------- CORRENTE


async def test_current_balance_sums_the_current_month(
    client: AsyncClient, ana: Ledger, clock: Clock
) -> None:
    month = clock.current_month()
    inside = month.first_day.isoformat()
    await ana.earn("1000.00", on=inside)
    await ana.spend("250.50", on=inside)

    response = await client.get(f"{BALANCE}/current", headers=ana.user.auth)

    assert response.status_code == 200
    body = response.json()
    assert body["month"] == month.key
    assert body["first_day"] == month.first_day.isoformat()
    assert body["last_day"] == month.last_day.isoformat()
    assert body["income"] == "1000.00"
    assert body["expense"] == "250.50"
    assert body["net"] == "749.50"


async def test_current_balance_ignores_the_other_months(
    client: AsyncClient, ana: Ledger, clock: Clock
) -> None:
    """O intervalo é fechado nas duas pontas: o dia 1º e o último dia entram, os vizinhos não."""
    month = clock.current_month()
    await ana.earn("10.00", on=month.first_day.isoformat())
    await ana.earn("20.00", on=month.last_day.isoformat())
    await ana.earn("999.00", on="2020-01-15")
    await ana.earn("777.00", on=shift_month(month, 1).first_day.isoformat())

    response = await client.get(f"{BALANCE}/current", headers=ana.user.auth)

    assert response.json()["income"] == "30.00"


async def test_current_balance_does_not_see_another_persons_money(
    client: AsyncClient, ana: Ledger, bruno: Ledger, clock: Clock
) -> None:
    """O escopo por dono vive na consulta. Sem ele, o vazamento seria só um número maior."""
    today = clock.current_month().first_day.isoformat()
    await ana.earn("100.00", on=today)
    await bruno.earn("500.00", on=today)
    await bruno.spend("300.00", on=today)

    response = await client.get(f"{BALANCE}/current", headers=ana.user.auth)

    body = response.json()
    assert body["income"] == "100.00"
    assert body["expense"] == "0.00"


async def test_current_balance_is_zero_and_not_null_without_transactions(
    client: AsyncClient, ana: Ledger
) -> None:
    """`coalesce` no lugar de `null`: quem consome não distingue "não gastei" de "não sei"."""
    response = await client.get(f"{BALANCE}/current", headers=ana.user.auth)

    assert response.json() | {"income": "0.00", "expense": "0.00", "net": "0.00"} == response.json()


# ---------------------------------------------------------------- MÊS A MÊS


async def test_monthly_series_has_no_gaps(client: AsyncClient, ana: Ledger) -> None:
    await ana.earn("100.00", on="2026-01-10")
    await ana.spend("40.00", on="2026-03-31")

    response = await client.get(
        f"{BALANCE}/monthly",
        headers=ana.user.auth,
        params={"from_month": "2026-01", "to_month": "2026-03"},
    )

    assert response.status_code == 200
    months = response.json()["months"]
    assert [month["month"] for month in months] == ["2026-01", "2026-02", "2026-03"]
    assert months[0]["income"] == "100.00"
    assert months[1] | {"income": "0.00", "expense": "0.00", "net": "0.00"} == months[1]
    assert months[2]["expense"] == "40.00"
    assert months[2]["net"] == "-40.00"


async def test_monthly_groups_by_the_competence_date(client: AsyncClient, ana: Ledger) -> None:
    """O agrupamento é por `occurred_on`, não por `created_at`.

    Os três lançamentos nascem agora, na mesma requisição; o que os separa é a
    data do fato. Confundir as duas é o que impede lançar hoje a despesa da
    semana passada — e faria os três caírem no mesmo mês aqui.
    """
    await ana.spend("10.00", on="2026-01-31")
    await ana.spend("20.00", on="2026-02-01")
    await ana.spend("30.00", on="2026-02-28")

    response = await client.get(
        f"{BALANCE}/monthly",
        headers=ana.user.auth,
        params={"from_month": "2026-01", "to_month": "2026-02"},
    )

    months = response.json()["months"]
    assert months[0]["expense"] == "10.00"
    assert months[1]["expense"] == "50.00"


async def test_monthly_total_matches_the_series(client: AsyncClient, ana: Ledger) -> None:
    await ana.earn("300.00", on="2026-01-05")
    await ana.spend("50.00", on="2026-01-06")
    await ana.spend("70.00", on="2026-02-06")

    response = await client.get(
        f"{BALANCE}/monthly",
        headers=ana.user.auth,
        params={"from_month": "2026-01", "to_month": "2026-02"},
    )

    body = response.json()
    assert body["total"] == {"income": "300.00", "expense": "120.00", "net": "180.00"}
    assert Decimal(body["total"]["expense"]) == sum(
        Decimal(month["expense"]) for month in body["months"]
    )


async def test_monthly_does_not_see_another_persons_money(
    client: AsyncClient, ana: Ledger, bruno: Ledger
) -> None:
    await ana.earn("100.00", on="2026-01-10")
    await bruno.earn("900.00", on="2026-01-10")

    response = await client.get(
        f"{BALANCE}/monthly",
        headers=ana.user.auth,
        params={"from_month": "2026-01", "to_month": "2026-01"},
    )

    assert response.json()["total"]["income"] == "100.00"


async def test_monthly_without_bounds_ends_in_the_current_month(
    client: AsyncClient, ana: Ledger, clock: Clock
) -> None:
    response = await client.get(f"{BALANCE}/monthly", headers=ana.user.auth)

    months = response.json()["months"]
    assert len(months) == 12
    assert months[-1]["month"] == clock.current_month().key


# ---------------------------------------------------------------- INTERVALO


async def test_range_is_closed_on_both_ends(client: AsyncClient, ana: Ledger) -> None:
    """O lançamento do último dia entra. Fosse aberto no fim, ele sumiria dos dois lados."""
    await ana.spend("1.00", on="2026-05-09")
    await ana.spend("2.00", on="2026-05-10")
    await ana.spend("4.00", on="2026-05-20")
    await ana.spend("8.00", on="2026-05-21")

    response = await client.get(
        f"{BALANCE}/range",
        headers=ana.user.auth,
        params={"occurred_from": "2026-05-10", "occurred_to": "2026-05-20"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["expense"] == "6.00"
    assert body["first_day"] == "2026-05-10"
    assert body["last_day"] == "2026-05-20"


async def test_range_can_cross_months_and_years(client: AsyncClient, ana: Ledger) -> None:
    await ana.earn("100.00", on="2025-12-20")
    await ana.spend("30.00", on="2026-01-15")

    response = await client.get(
        f"{BALANCE}/range",
        headers=ana.user.auth,
        params={"occurred_from": "2025-12-01", "occurred_to": "2026-01-31"},
    )

    assert response.json() | {"income": "100.00", "expense": "30.00", "net": "70.00"} == (
        response.json()
    )


async def test_range_does_not_see_another_persons_money(
    client: AsyncClient, ana: Ledger, bruno: Ledger
) -> None:
    await ana.spend("7.00", on="2026-05-10")
    await bruno.spend("700.00", on="2026-05-10")

    response = await client.get(
        f"{BALANCE}/range",
        headers=ana.user.auth,
        params={"occurred_from": "2026-05-01", "occurred_to": "2026-05-31"},
    )

    assert response.json()["expense"] == "7.00"


async def test_range_matches_the_transaction_list_of_the_same_filter(
    client: AsyncClient, ana: Ledger
) -> None:
    """Os nomes dos parâmetros são os de `GET /transactions` para isto valer.

    O mesmo par de datas nas duas rotas descreve o mesmo recorte: o saldo é o
    da lista que a outra mostra, e não de um recorte parecido.
    """
    await ana.spend("5.00", on="2026-04-30")
    await ana.spend("11.00", on="2026-05-15")
    await ana.spend("13.00", on="2026-06-01")
    window = {"occurred_from": "2026-05-01", "occurred_to": "2026-05-31"}

    balance = await client.get(f"{BALANCE}/range", headers=ana.user.auth, params=window)
    listed = await client.get("/api/v1/transactions", headers=ana.user.auth, params=window)

    total = sum(Decimal(item["amount"]) for item in listed.json()["items"])
    assert Decimal(balance.json()["expense"]) == total == Decimal("11.00")


# -------------------------------------------------------------------- RECUSAS


async def test_monthly_rejects_an_inverted_window(client: AsyncClient, ana: Ledger) -> None:
    response = await client.get(
        f"{BALANCE}/monthly",
        headers=ana.user.auth,
        params={"from_month": "2026-09", "to_month": "2026-08"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_period"


async def test_monthly_rejects_a_window_past_the_ceiling(client: AsyncClient, ana: Ledger) -> None:
    response = await client.get(
        f"{BALANCE}/monthly",
        headers=ana.user.auth,
        params={"from_month": "1900-01", "to_month": "2026-12"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "period_too_long"


@pytest.mark.parametrize("month", ["2026-13", "2026-00", "2026", "setembro", "2026-9"])
async def test_monthly_rejects_a_malformed_month(
    client: AsyncClient, ana: Ledger, month: str
) -> None:
    """Recusado pelo `pattern`, antes de `parse_month` — 422 com o campo apontado."""
    response = await client.get(
        f"{BALANCE}/monthly", headers=ana.user.auth, params={"from_month": month}
    )

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "validation_error"
    assert any("from_month" in detail["field"] for detail in body["details"])


async def test_range_rejects_an_inverted_period(client: AsyncClient, ana: Ledger) -> None:
    response = await client.get(
        f"{BALANCE}/range",
        headers=ana.user.auth,
        params={"occurred_from": "2026-05-31", "occurred_to": "2026-05-01"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_period"


async def test_range_requires_both_ends(client: AsyncClient, ana: Ledger) -> None:
    """Intervalo sem intervalo não cai num default silencioso: é `/balance/current`."""
    response = await client.get(
        f"{BALANCE}/range", headers=ana.user.auth, params={"occurred_from": "2026-05-01"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


# ------------------------------------------------------------------- LEITURA


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/current", {}),
        ("/monthly", {"from_month": "2026-01", "to_month": "2026-03"}),
        ("/range", {"occurred_from": "2026-01-01", "occurred_to": "2026-03-31"}),
    ],
)
async def test_balance_never_writes(
    client: AsyncClient,
    ana: Ledger,
    count_rows: Callable[[str], Awaitable[int]],
    path: str,
    params: dict[str, Any],
) -> None:
    """Saldo é pergunta, não recurso: nenhuma das três guarda o número que apurou."""
    await ana.earn("100.00", on="2026-01-10")
    before = await count_rows("transactions")

    response = await client.get(f"{BALANCE}{path}", headers=ana.user.auth, params=params)

    assert response.status_code == 200
    assert await count_rows("transactions") == before
