"""CRUD do catálogo global e isolamento dos objetivos pessoais."""

from __future__ import annotations

from tests.api.client import ApiClient
from tests.api.factories import register_admin, register_user


def test_admin_manages_global_categories_visible_to_everyone(client: ApiClient) -> None:
    admin = register_admin(client)
    member = register_user(client)
    created = client.post(
        "/api/v1/admin/categories",
        headers=admin.auth,
        json={"name": "Educação global", "kind": "expense"},
    )
    assert created.status_code == 201, created.text
    category_id = created.json()["id"]
    assert created.json()["is_global"] is True
    assert any(
        item["id"] == category_id
        for item in client.get("/api/v1/categories", headers=member.auth).json()
    )
    assert any(
        item["id"] == category_id
        for item in client.get("/api/v1/admin/categories", headers=admin.auth).json()
    )
    changed = client.patch(
        f"/api/v1/admin/categories/{category_id}",
        headers=admin.auth,
        json={"name": "Cursos globais"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["name"] == "Cursos globais"
    assert (
        client.delete(f"/api/v1/admin/categories/{category_id}", headers=admin.auth).status_code
        == 204
    )
    assert all(
        item["id"] != category_id
        for item in client.get("/api/v1/categories", headers=member.auth).json()
    )


def test_admin_manages_global_goal_and_users_cannot_change_it(client: ApiClient) -> None:
    admin = register_admin(client)
    member = register_user(client)
    created = client.post(
        "/api/v1/admin/investment-goals",
        headers=admin.auth,
        json={"name": "Reserva global", "target_amount": "1000.00"},
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["id"]
    assert created.json()["is_global"] is True
    visible = client.get("/api/v1/investment-goals", headers=member.auth)
    assert visible.status_code == 200
    assert visible.json()[0]["id"] == goal_id
    assert visible.json()[0]["current_amount"] == "0.00"
    assert (
        client.patch(
            f"/api/v1/investment-goals/{goal_id}",
            headers=member.auth,
            json={"name": "Alterado"},
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/v1/investment-goals/{goal_id}", headers=member.auth).status_code == 404
    )
    listed = client.get("/api/v1/admin/investment-goals", headers=admin.auth)
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == goal_id
    changed = client.patch(
        f"/api/v1/admin/investment-goals/{goal_id}",
        headers=admin.auth,
        json={"target_amount": "1500.00"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["target_amount"] == "1500.00"
    assert (
        client.delete(f"/api/v1/admin/investment-goals/{goal_id}", headers=admin.auth).status_code
        == 204
    )
    assert client.get("/api/v1/investment-goals", headers=member.auth).json() == []


def test_admin_catalog_cannot_edit_personal_category(client: ApiClient) -> None:
    admin = register_admin(client)
    personal = client.post(
        "/api/v1/categories",
        headers=admin.auth,
        json={"name": "Minha categoria", "kind": "expense"},
    )
    assert personal.status_code == 201
    category_id = personal.json()["id"]
    assert (
        client.patch(
            f"/api/v1/admin/categories/{category_id}",
            headers=admin.auth,
            json={"name": "Global"},
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/v1/admin/categories/{category_id}",
            headers=admin.auth,
        ).status_code
        == 404
    )


def test_admin_can_clear_global_goal_target_date(client: ApiClient) -> None:
    admin = register_admin(client)
    created = client.post(
        "/api/v1/admin/investment-goals",
        headers=admin.auth,
        json={"name": "Reserva", "target_amount": "1000.00", "target_on": "2027-01-01"},
    )
    assert created.status_code == 201
    updated = client.patch(
        f"/api/v1/admin/investment-goals/{created.json()['id']}",
        headers=admin.auth,
        json={"clear_target_on": True},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["target_on"] is None
