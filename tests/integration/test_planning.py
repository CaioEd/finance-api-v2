"""Migração, NUMERIC e FK dos limites no PostgreSQL real."""

from __future__ import annotations

from httpx import AsyncClient

from tests.factories import register_user


async def test_limit_uses_exact_money_and_protects_its_category(client: AsyncClient) -> None:
    user = await register_user(client)
    category = await client.post(
        "/api/v1/categories",
        headers=user.auth,
        json={"name": "Mercado", "kind": "expense"},
    )
    assert category.status_code == 201, category.text
    category_id = category.json()["id"]
    transaction = await client.post(
        "/api/v1/transactions",
        headers=user.auth,
        json={"category_id": category_id, "amount": "0.10", "occurred_on": "2026-09-20"},
    )
    assert transaction.status_code == 201, transaction.text
    transaction = await client.post(
        "/api/v1/transactions",
        headers=user.auth,
        json={"category_id": category_id, "amount": "0.20", "occurred_on": "2026-09-20"},
    )
    assert transaction.status_code == 201, transaction.text
    limit = await client.post(
        "/api/v1/spending-limits",
        headers=user.auth,
        json={
            "name": "Mercado",
            "category_id": category_id,
            "amount": "0.29",
            "starts_on": "2026-09-20",
            "ends_on": "2026-10-20",
        },
    )
    assert limit.status_code == 201, limit.text
    assert limit.json()["spent"] == "0.30"
    assert limit.json()["exceeded"] is True
    unused_category = await client.post(
        "/api/v1/categories",
        headers=user.auth,
        json={"name": "Teto sem movimento", "kind": "expense"},
    )
    assert unused_category.status_code == 201, unused_category.text
    protected_id = unused_category.json()["id"]
    protected_limit = await client.post(
        "/api/v1/spending-limits",
        headers=user.auth,
        json={
            "name": "Teto",
            "category_id": protected_id,
            "amount": "50.00",
            "starts_on": "2026-09-20",
            "ends_on": "2026-10-20",
        },
    )
    assert protected_limit.status_code == 201, protected_limit.text
    refused = await client.delete(f"/api/v1/categories/{protected_id}", headers=user.auth)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "category_in_use"
