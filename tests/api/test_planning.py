"""Limites e objetivos: cálculo, escopo e validação das referências."""

from __future__ import annotations

from tests.api.client import ApiClient
from tests.api.factories import register_user
from tests.factories import RegisteredUser


def category(client: ApiClient, user: RegisteredUser, name: str, kind: str) -> str:
    response = client.post(
        "/api/v1/categories", headers=user.auth, json={"name": name, "kind": kind}
    )
    response.raise_for_status()
    return str(response.json()["id"])


def expense(
    client: ApiClient, user: RegisteredUser, category_id: str, amount: str, day: str
) -> None:
    response = client.post(
        "/api/v1/transactions",
        headers=user.auth,
        json={"category_id": category_id, "amount": amount, "occurred_on": day},
    )
    assert response.status_code == 201, response.text


def test_category_limit_counts_only_its_expenses_in_inclusive_period(client: ApiClient) -> None:
    ana = register_user(client)
    mercado = category(client, ana, "Mercado", "expense")
    casa = category(client, ana, "Casa", "expense")
    expense(client, ana, mercado, "10.00", "2026-09-19")
    expense(client, ana, mercado, "40.00", "2026-09-20")
    expense(client, ana, mercado, "70.00", "2026-10-20")
    expense(client, ana, mercado, "20.00", "2026-10-21")
    expense(client, ana, casa, "100.00", "2026-09-25")
    other = register_user(client, email="bia@exemplo.com", username="bia")
    expense(client, other, category(client, other, "Mercado", "expense"), "1000.00", "2026-09-25")

    response = client.post(
        "/api/v1/spending-limits",
        headers=ana.auth,
        json={
            "name": "Mercado",
            "amount": "100.00",
            "category_id": mercado,
            "starts_on": "2026-09-20",
            "ends_on": "2026-10-20",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["spent"] == "110.00"
    assert response.json()["exceeded"] is True
    general = client.post(
        "/api/v1/spending-limits",
        headers=ana.auth,
        json={
            "name": "Tudo",
            "amount": "220.00",
            "starts_on": "2026-09-20",
            "ends_on": "2026-10-20",
        },
    )
    assert general.json()["spent"] == "210.00"
    assert general.json()["exceeded"] is False
    assert client.get("/api/v1/spending-limits", headers=other.auth).json() == []


def test_limit_rejects_income_category_and_reversed_dates(client: ApiClient) -> None:
    user = register_user(client)
    salary = category(client, user, "Salário", "income")
    body = {
        "name": "Inválido",
        "amount": "10.00",
        "category_id": salary,
        "starts_on": "2026-09-20",
        "ends_on": "2026-10-20",
    }
    assert client.post("/api/v1/spending-limits", headers=user.auth, json=body).status_code == 422
    body["category_id"] = None
    body["ends_on"] = "2026-09-19"
    assert client.post("/api/v1/spending-limits", headers=user.auth, json=body).status_code == 422


def test_a_limit_can_release_its_category_and_prevents_deleting_it_while_in_use(
    client: ApiClient,
) -> None:
    user = register_user(client)
    market = category(client, user, "Mercado", "expense")
    created = client.post(
        "/api/v1/spending-limits",
        headers=user.auth,
        json={
            "name": "Mercado",
            "amount": "100.00",
            "category_id": market,
            "starts_on": "2026-09-20",
            "ends_on": "2026-10-20",
        },
    )
    assert created.status_code == 201, created.text
    refused = client.delete(f"/api/v1/categories/{market}", headers=user.auth)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "category_in_use"
    kind_change = client.patch(
        f"/api/v1/categories/{market}", headers=user.auth, json={"kind": "income"}
    )
    assert kind_change.status_code == 422
    changed = client.patch(
        f"/api/v1/spending-limits/{created.json()['id']}",
        headers=user.auth,
        json={"clear_category": True},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["category_id"] is None
    assert client.delete(f"/api/v1/categories/{market}", headers=user.auth).status_code == 204


def test_goal_tracks_portfolio_or_selected_position(client: ApiClient) -> None:
    user = register_user(client)
    investment = client.post(
        "/api/v1/investments",
        headers=user.auth,
        json={
            "type": "cdb",
            "name": "CDB",
            "invested_amount": "500.00",
            "rate_index": "cdi",
            "rate_percent": "100",
            "applied_on": "2026-09-01",
        },
    )
    assert investment.status_code == 201, investment.text
    position_id = investment.json()["id"]
    goal = client.post(
        "/api/v1/investment-goals",
        headers=user.auth,
        json={
            "name": "Reserva",
            "target_amount": "1000.00",
            "investment_id": position_id,
            "target_on": "2027-12-31",
        },
    )
    assert goal.status_code == 201, goal.text
    assert goal.json()["current_amount"] == investment.json()["current_value"]
    assert goal.json()["progress_percent"] == 50.0
    updated = client.patch(
        f"/api/v1/investment-goals/{goal.json()['id']}",
        headers=user.auth,
        json={"clear_investment": True, "clear_target_on": True},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["investment_id"] is None
    assert updated.json()["target_on"] is None
    other = register_user(client, email="bia@exemplo.com", username="bia")
    assert client.get("/api/v1/investment-goals", headers=other.auth).json() == []
    refused = client.post(
        "/api/v1/investment-goals",
        headers=other.auth,
        json={"name": "Alheio", "target_amount": "1000.00", "investment_id": position_id},
    )
    assert refused.status_code == 422
