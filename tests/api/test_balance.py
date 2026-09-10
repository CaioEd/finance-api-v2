"""Os três endpoints de saldo, no que a suíte de API tem como afirmar.

O contrato do saldo — o `FILTER` separando receita de despesa, o `JOIN` que traz
o `kind` da categoria, o escopo por dono num agregado — está em
`tests/integration/test_balance.py`, contra Postgres. Não se repete aqui.

Este arquivo existe por outra razão, e ela é específica desta suíte: o
agrupamento mensal do saldo é a **única** consulta da aplicação que depende de
uma função que o SQLite não tem, e `tests/api/sqlite_backend.py` a implementa —
`date_trunc('month', …)` recortando texto, mais o `CAST(… AS DATE)` que precisa
desaparecer para não virar `2026`. Tradução de banco escrita à mão é código, e
código sem teste quebra calado: sem o que está aqui, um `date_trunc` errado
devolveria uma resposta no formato certo, com os meses somados errado, e as
matrizes passariam — elas exercitam a rota com a conta vazia, onde não há o que
agrupar.
"""

from __future__ import annotations

from tests.api.client import ApiClient
from tests.api.factories import register_user
from tests.factories import RegisteredUser

SALARIO = ("Salário", "income")
MERCADO = ("Mercado", "expense")


def a_category(client: ApiClient, user: RegisteredUser, name_and_kind: tuple[str, str]) -> str:
    name, kind = name_and_kind
    response = client.post(
        "/api/v1/categories", headers=user.auth, json={"name": name, "kind": kind}
    )
    response.raise_for_status()
    return str(response.json()["id"])


def a_transaction(
    client: ApiClient, user: RegisteredUser, category_id: str, amount: str, day: str | None = None
) -> None:
    body = {"amount": amount, "category_id": category_id}
    if day is not None:
        body["occurred_on"] = day
    response = client.post("/api/v1/transactions", headers=user.auth, json=body)
    assert response.status_code == 201, response.text


def test_the_monthly_series_groups_by_the_month_of_occurrence(client: ApiClient) -> None:
    """Dois lançamentos em agosto e um em setembro caem em dois meses, não num só.

    É este teste que sustenta o `date_trunc` de mentira do `sqlite_backend`. Com
    o `CAST(… AS DATE)` de volta, os três lançamentos cairiam no mesmo balde —
    `2026` — e a série viria com um mês só.
    """
    ana = register_user(client)
    salario = a_category(client, ana, SALARIO)
    mercado = a_category(client, ana, MERCADO)

    a_transaction(client, ana, salario, "5000.00", "2026-08-05")
    a_transaction(client, ana, mercado, "1200.50", "2026-08-20")
    a_transaction(client, ana, salario, "5000.00", "2026-09-05")

    response = client.get(
        "/api/v1/balance/monthly",
        headers=ana.auth,
        params={"from_month": "2026-08", "to_month": "2026-09"},
    )

    assert response.status_code == 200, response.text
    series = response.json()
    assert [month["month"] for month in series["months"]] == ["2026-08", "2026-09"]
    agosto, setembro = series["months"]
    assert (agosto["income"], agosto["expense"], agosto["net"]) == ("5000.00", "1200.50", "3799.50")
    assert (setembro["income"], setembro["expense"]) == ("5000.00", "0.00")
    assert series["total"] == {"income": "10000.00", "expense": "1200.50", "net": "8799.50"}


def test_the_monthly_series_fills_the_months_without_a_launch(client: ApiClient) -> None:
    """Mês vazio vem zerado, não some da série — a consulta só devolve o que existe."""
    ana = register_user(client)
    a_transaction(client, ana, a_category(client, ana, SALARIO), "100.00", "2026-08-10")

    response = client.get(
        "/api/v1/balance/monthly",
        headers=ana.auth,
        params={"from_month": "2026-06", "to_month": "2026-08"},
    )

    assert response.status_code == 200, response.text
    months = response.json()["months"]
    assert [month["month"] for month in months] == ["2026-06", "2026-07", "2026-08"]
    assert [month["net"] for month in months] == ["0.00", "0.00", "100.00"]


def test_the_range_totals_only_what_falls_inside_it(client: ApiClient) -> None:
    """O intervalo é fechado nas duas pontas, e o que está fora fica fora."""
    ana = register_user(client)
    salario = a_category(client, ana, SALARIO)

    a_transaction(client, ana, salario, "10.00", "2026-08-31")
    a_transaction(client, ana, salario, "20.00", "2026-09-01")
    a_transaction(client, ana, salario, "40.00", "2026-09-30")
    a_transaction(client, ana, salario, "80.00", "2026-10-01")

    response = client.get(
        "/api/v1/balance/range",
        headers=ana.auth,
        params={"occurred_from": "2026-09-01", "occurred_to": "2026-09-30"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "first_day": "2026-09-01",
        "last_day": "2026-09-30",
        "income": "60.00",
        "expense": "0.00",
        "net": "60.00",
    }


def test_the_current_month_sees_a_launch_made_today(client: ApiClient) -> None:
    """O "hoje" do `Clock` é o mesmo dos dois lados, e o lançamento sem data cai nele.

    Sem data fixa no teste de propósito: os dois lados — o default de
    `occurred_on` e o mês que `/balance/current` resolve — saem do mesmo
    relógio, e é a concordância entre eles que importa. Fixar uma data aqui
    congelaria a suíte no mês em que ela foi escrita.
    """
    ana = register_user(client)
    a_transaction(client, ana, a_category(client, ana, SALARIO), "123.45")

    response = client.get("/api/v1/balance/current", headers=ana.auth)

    assert response.status_code == 200, response.text
    current = response.json()
    assert current["income"] == "123.45"
    assert current["net"] == "123.45"
    assert current["month"] == current["first_day"][:7]
