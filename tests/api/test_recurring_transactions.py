"""Recorrências de ponta a ponta: da requisição ao lançamento que aparece sozinho.

As duas matrizes já cobrem o que `/recurring-transactions` tem de igual às
outras rotas — quem alcança, 404, 422, PATCH parcial, paginação. Aqui fica o
que é só dela, e principalmente o que o usuário vê: marcar a despesa como
recorrente no formulário, o mês virar, e a despesa estar lá — no extrato, no
saldo, apontando para a regra.

O agendador roda chamado direto (`register_due_recurrences`), contra o banco
desta suíte e com um relógio parado em 2099: as datas dos testes não dependem
do dia em que a suíte roda, e o access token continua validado pelo relógio de
verdade (ver o fixture `clock` do conftest).

O que só o Postgres responde — a trava que pula a linha de outra réplica, o
`SET NULL` e o `409 category_in_use` pelo nome da FK — está em
`tests/integration/test_recurring_transactions.py`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from functools import partial
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.clock import Clock
from jobs.recurring_transactions import register_due_recurrences
from models.recurring_transaction import RecurringTransaction
from tests.api.client import ApiClient
from tests.api.conftest import APP_TIMEZONE
from tests.api.factories import register_user
from tests.factories import RegisteredUser

RECURRING = "/api/v1/recurring-transactions"
TRANSACTIONS = "/api/v1/transactions"


def a_category(client: ApiClient, user: RegisteredUser, *, name: str, kind: str) -> str:
    response = client.post(
        "/api/v1/categories", headers=user.auth, json={"name": name, "kind": kind}
    )
    response.raise_for_status()
    return str(response.json()["id"])


def a_rule(client: ApiClient, user: RegisteredUser, **body: Any) -> dict[str, Any]:
    response = client.post(RECURRING, headers=user.auth, json=body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def run_scheduler_on(client: ApiClient, day: date, *, batch_size: int = 100) -> int:
    """Uma rodada do agendador como se hoje fosse `day`, no banco da aplicação."""
    moment = datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC)
    clock = Clock(tz=APP_TIMEZONE, instant=lambda: moment)
    database = client.app.state.database  # type: ignore[attr-defined]
    return client.portal.call(
        partial(register_due_recurrences, database, clock, batch_size=batch_size)
    )


def transactions_of(client: ApiClient, user: RegisteredUser, **params: Any) -> list[dict[str, Any]]:
    response = client.get(TRANSACTIONS, headers=user.auth, params={"limit": 100, **params})
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return sorted(items, key=lambda item: item["occurred_on"])


# ------------------------------------------------- pelo formulário do lançamento


def test_a_transaction_can_be_created_already_repeating_every_month(client: ApiClient) -> None:
    """O fluxo da tela: a despesa de novembro marcada "todo dia 5" começa a repetir em dezembro."""
    ana = register_user(client)
    streaming = a_category(client, ana, name="Streaming", kind="expense")

    response = client.post(
        TRANSACTIONS,
        headers=ana.auth,
        json={
            "amount": "39.90",
            "category_id": streaming,
            "occurred_on": "2099-11-20",
            "description": "Netflix",
            "recurrence": {"day_of_month": 5},
        },
    )

    assert response.status_code == 201, response.text
    created = response.json()
    assert created["recurring_transaction_id"] is not None

    [rule] = client.get(RECURRING, headers=ana.auth).json()["items"]
    assert rule["id"] == created["recurring_transaction_id"]
    assert rule["next_occurrence_on"] == "2099-12-05"
    assert rule["day_of_month"] == 5
    assert rule["amount"] == "39.90"
    assert rule["description"] == "Netflix"
    assert rule["kind"] == "expense"
    assert rule["category"]["id"] == streaming
    assert rule["is_active"] is True
    assert [t["occurred_on"] for t in transactions_of(client, ana)] == ["2099-11-20"]


def test_a_transaction_without_recurrence_is_not_linked(client: ApiClient) -> None:
    ana = register_user(client)
    mercado = a_category(client, ana, name="Mercado", kind="expense")

    response = client.post(
        TRANSACTIONS, headers=ana.auth, json={"amount": "10.00", "category_id": mercado}
    )

    assert response.json()["recurring_transaction_id"] is None
    assert client.get(RECURRING, headers=ana.auth).json()["total"] == 0


def test_a_refused_transaction_leaves_no_rule_behind(client: ApiClient) -> None:
    """Lançamento e regra são um commit só: a recusa não deixa metade gravada."""
    ana = register_user(client)
    bruno = register_user(client, email="bruno@exemplo.com", username="bruno")
    de_bruno = a_category(client, bruno, name="Barco", kind="expense")

    response = client.post(
        TRANSACTIONS,
        headers=ana.auth,
        json={"amount": "10.00", "category_id": de_bruno, "recurrence": {"day_of_month": 5}},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_category"
    assert client.get(RECURRING, headers=ana.auth).json()["total"] == 0
    assert transactions_of(client, ana) == []


# ------------------------------------------------------------- o agendador


def test_the_scheduler_registers_each_due_month_as_a_transaction(client: ApiClient) -> None:
    """Todo dia 31 a partir de janeiro, rodando em 15 de março: janeiro e fevereiro (28)."""
    ana = register_user(client)
    streaming = a_category(client, ana, name="Streaming", kind="expense")
    rule = a_rule(
        client,
        ana,
        amount="39.90",
        category_id=streaming,
        description="Netflix",
        day_of_month=31,
        starts_on="2099-01-01",
    )

    registered = run_scheduler_on(client, date(2099, 3, 15))

    assert registered == 2
    lancamentos = transactions_of(client, ana)
    assert [t["occurred_on"] for t in lancamentos] == ["2099-01-31", "2099-02-28"]
    for lancamento in lancamentos:
        assert lancamento["recurring_transaction_id"] == rule["id"]
        assert lancamento["amount"] == "39.90"
        assert lancamento["description"] == "Netflix"
        assert lancamento["kind"] == "expense"
        assert lancamento["category"]["id"] == streaming
    detalhe = client.get(f"{RECURRING}/{rule['id']}", headers=ana.auth).json()
    assert detalhe["next_occurrence_on"] == "2099-03-31"


def test_the_registered_months_count_in_the_balance(client: ApiClient) -> None:
    """Lançamento gerado é lançamento comum: o saldo o soma sem saber de onde veio."""
    ana = register_user(client)
    salario = a_category(client, ana, name="Salário", kind="income")
    a_rule(
        client, ana, amount="5000.00", category_id=salario, day_of_month=5, starts_on="2099-01-01"
    )

    run_scheduler_on(client, date(2099, 3, 5))

    response = client.get(
        "/api/v1/balance/range",
        headers=ana.auth,
        params={"occurred_from": "2099-01-01", "occurred_to": "2099-03-31"},
    )
    assert response.status_code == 200
    assert response.json()["income"] == "15000.00"


def test_running_the_scheduler_twice_does_not_repeat_a_month(client: ApiClient) -> None:
    ana = register_user(client)
    streaming = a_category(client, ana, name="Streaming", kind="expense")
    a_rule(
        client, ana, amount="39.90", category_id=streaming, day_of_month=5, starts_on="2099-01-01"
    )

    first = run_scheduler_on(client, date(2099, 2, 10))
    second = run_scheduler_on(client, date(2099, 2, 10))

    assert (first, second) == (2, 0)
    assert len(transactions_of(client, ana)) == 2


def test_the_scheduler_goes_through_every_batch_and_every_owner(client: ApiClient) -> None:
    donos = [
        register_user(client, email=f"pessoa{n}@exemplo.com", username=f"pessoa{n}")
        for n in range(3)
    ]
    for dono in donos:
        categoria = a_category(client, dono, name="Aluguel", kind="expense")
        a_rule(
            client,
            dono,
            amount="1500.00",
            category_id=categoria,
            day_of_month=10,
            starts_on="2099-05-01",
        )

    registered = run_scheduler_on(client, date(2099, 5, 10), batch_size=1)

    assert registered == 3
    for dono in donos:
        assert [t["occurred_on"] for t in transactions_of(client, dono)] == ["2099-05-10"]


def test_a_paused_rule_registers_nothing(client: ApiClient) -> None:
    ana = register_user(client)
    streaming = a_category(client, ana, name="Streaming", kind="expense")
    rule = a_rule(
        client, ana, amount="39.90", category_id=streaming, day_of_month=5, starts_on="2099-01-01"
    )

    paused = client.patch(f"{RECURRING}/{rule['id']}", headers=ana.auth, json={"is_active": False})

    assert paused.status_code == 200
    assert paused.json()["is_active"] is False
    assert run_scheduler_on(client, date(2099, 6, 1)) == 0
    assert transactions_of(client, ana) == []


def test_deleting_the_rule_keeps_what_it_already_registered(client: ApiClient) -> None:
    """Cancelar a assinatura encerra os próximos meses, não apaga os que foram pagos."""
    ana = register_user(client)
    streaming = a_category(client, ana, name="Streaming", kind="expense")
    rule = a_rule(
        client, ana, amount="39.90", category_id=streaming, day_of_month=5, starts_on="2099-01-01"
    )
    run_scheduler_on(client, date(2099, 2, 10))

    response = client.delete(f"{RECURRING}/{rule['id']}", headers=ana.auth)

    assert response.status_code == 204
    lancamentos = transactions_of(client, ana)
    assert [t["occurred_on"] for t in lancamentos] == ["2099-01-05", "2099-02-05"]
    assert all(t["recurring_transaction_id"] is None for t in lancamentos)
    assert run_scheduler_on(client, date(2099, 12, 31)) == 0


# --------------------------------------------------------------- a listagem


def test_the_listing_filters_by_kind_and_orders_by_day(client: ApiClient) -> None:
    ana = register_user(client)
    salario = a_category(client, ana, name="Salário", kind="income")
    aluguel = a_category(client, ana, name="Aluguel", kind="expense")
    a_rule(client, ana, amount="1500.00", category_id=aluguel, day_of_month=10)
    a_rule(client, ana, amount="5000.00", category_id=salario, day_of_month=5)
    a_rule(client, ana, amount="39.90", category_id=aluguel, day_of_month=2)

    todas = client.get(RECURRING, headers=ana.auth).json()
    receitas = client.get(RECURRING, headers=ana.auth, params={"kind": "income"}).json()
    despesas = client.get(RECURRING, headers=ana.auth, params={"kind": "expense"}).json()

    assert [r["day_of_month"] for r in todas["items"]] == [2, 5, 10]
    assert (receitas["total"], despesas["total"]) == (1, 2)
    assert {r["kind"] for r in receitas["items"]} == {"income"}
    assert {r["kind"] for r in despesas["items"]} == {"expense"}


def test_one_user_never_reaches_the_rules_of_another(client: ApiClient) -> None:
    """404, não 403: a resposta não confirma que a regra existe."""
    ana = register_user(client)
    bruno = register_user(client, email="bruno@exemplo.com", username="bruno")
    categoria = a_category(client, bruno, name="Aluguel", kind="expense")
    de_bruno = a_rule(client, bruno, amount="1500.00", category_id=categoria, day_of_month=10)
    url = f"{RECURRING}/{de_bruno['id']}"

    assert client.get(RECURRING, headers=ana.auth).json()["total"] == 0
    for response in (
        client.get(url, headers=ana.auth),
        client.patch(url, headers=ana.auth, json={"amount": "1.00"}),
        client.delete(url, headers=ana.auth),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "recurring_transaction_not_found"
    assert client.get(url, headers=bruno.auth).json()["amount"] == "1500.00"


def test_a_rule_refuses_a_category_of_another_user(client: ApiClient) -> None:
    ana = register_user(client)
    bruno = register_user(client, email="bruno@exemplo.com", username="bruno")
    de_bruno = a_category(client, bruno, name="Barco", kind="expense")

    response = client.post(
        RECURRING,
        headers=ana.auth,
        json={"amount": "1.00", "category_id": de_bruno, "day_of_month": 1},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_category"


def test_deleting_the_account_takes_its_rules_along(client: ApiClient) -> None:
    ana = register_user(client)
    categoria = a_category(client, ana, name="Aluguel", kind="expense")
    rule = a_rule(client, ana, amount="1500.00", category_id=categoria, day_of_month=10)

    response = client.request(
        "DELETE", "/api/v1/users/me", headers=ana.auth, json={"password": ana.password}
    )

    assert response.status_code == 204

    async def count(session: AsyncSession) -> int:
        statement = select(func.count()).where(RecurringTransaction.id == UUID(rule["id"]))
        return int((await session.execute(statement)).scalar_one())

    assert client.in_the_database(count) == 0
