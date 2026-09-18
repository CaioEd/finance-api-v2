"""Investimentos de ponta a ponta: da posição ao lançamento no extrato e no saldo.

O que é igual às outras rotas fica nas matrizes. Aqui está o que só este
domínio tem: as duas metades da tabela (renda fixa e variável), o preço médio
ponderado do aporte, e a ponte com `transactions` — aporte é despesa, provento
é receita, e os dois entram no saldo como qualquer lançamento.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.transaction import Transaction
from tests.api.client import ApiClient
from tests.api.factories import register_user
from tests.factories import RegisteredUser

INVESTMENTS = "/api/v1/investments"
TRANSACTIONS = "/api/v1/transactions"
CATEGORIES = "/api/v1/categories"


def a_category(client: ApiClient, user: RegisteredUser, *, name: str, kind: str) -> str:
    response = client.post(CATEGORIES, headers=user.auth, json={"name": name, "kind": kind})
    response.raise_for_status()
    return str(response.json()["id"])


def an_investment(client: ApiClient, user: RegisteredUser, **body: Any) -> dict[str, Any]:
    response = client.post(INVESTMENTS, headers=user.auth, json=body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def a_stock(client: ApiClient, user: RegisteredUser, **overrides: Any) -> dict[str, Any]:
    return an_investment(
        client,
        user,
        **{
            "type": "br_stock",
            "symbol": "PETR4",
            "quantity": "100",
            "average_price": "30.00",
            **overrides,
        },
    )


def a_cdb(client: ApiClient, user: RegisteredUser, **overrides: Any) -> dict[str, Any]:
    return an_investment(
        client,
        user,
        **{
            "type": "cdb",
            "name": "CDB Liquidez Diária",
            "invested_amount": "5000.00",
            "rate_index": "cdi",
            "rate_percent": "102",
            "applied_on": "2026-01-15",
            **overrides,
        },
    )


def error_code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


# ------------------------------------------------------- as duas metades


def test_a_variable_income_position_carries_the_asset_and_the_class(client: ApiClient) -> None:
    """Renda variável tem ativo, quantidade e preço médio — e a classe é derivada."""
    user = register_user(client)

    created = a_stock(client, user)

    assert created["investment_class"] == "variable_income"
    assert created["asset"]["symbol"] == "PETR4"
    assert created["asset"]["currency"] == "BRL"
    # Ainda não cotado: quem preenche o preço é o agendador, na rodada seguinte.
    assert created["asset"]["price"] is None
    assert created["quantity"] == "100"
    assert created["average_price"] == "30"
    # Sem `invested_amount` no corpo, em ativo cotado em real, o produto serve.
    assert created["invested_amount"] == "3000.00"
    # Vale o que se pagou até a primeira cotação chegar.
    assert created["current_value"] == "3000.00"
    assert created["rate_index"] is None


def test_a_fixed_income_position_carries_the_index_and_no_asset(client: ApiClient) -> None:
    user = register_user(client)

    created = a_cdb(client, user)

    assert created["investment_class"] == "fixed_income"
    assert created["asset"] is None
    assert created["quantity"] is None
    assert created["rate_index"] == "cdi"
    assert created["rate_percent"] == "102"
    assert created["applied_on"] == "2026-01-15"
    assert created["invested_amount"] == "5000.00"


def test_savings_fills_the_index_and_the_rate_by_itself(client: ApiClient) -> None:
    """Poupança não tem taxa a contratar: a regra é a do Banco Central."""
    user = register_user(client)

    created = an_investment(
        client,
        user,
        type="savings",
        name="Poupança Caixa",
        invested_amount="1200.00",
        applied_on="2026-03-01",
    )

    assert created["rate_index"] == "savings"
    assert created["rate_percent"] == "100"


def test_a_symbol_is_stored_upper_cased(client: ApiClient) -> None:
    """ "petr4" e "PETR4" são o mesmo papel — duas linhas seriam dois preços."""
    user = register_user(client)

    first = a_stock(client, user, symbol="petr4")
    second = a_stock(client, user, symbol="PETR4")

    assert first["asset"]["symbol"] == "PETR4"
    assert first["asset"]["id"] == second["asset"]["id"], "o catálogo duplicou o ativo"


def test_a_fixed_income_type_refuses_the_fields_of_the_other_half(client: ApiClient) -> None:
    response = client.post(
        INVESTMENTS,
        headers=user_auth(client),
        json={
            "type": "cdb",
            "name": "CDB",
            "invested_amount": "100.00",
            "applied_on": "2026-01-01",
            "rate_index": "cdi",
            "rate_percent": "100",
            "quantity": "10",
        },
    )

    assert response.status_code == 422, response.text
    assert "quantity" in response.text


def test_a_variable_income_type_requires_its_own_fields(client: ApiClient) -> None:
    response = client.post(
        INVESTMENTS, headers=user_auth(client), json={"type": "br_stock", "symbol": "VALE3"}
    )

    assert response.status_code == 422, response.text
    assert "quantity" in response.text
    assert "average_price" in response.text


def test_an_asset_priced_in_dollars_requires_what_was_paid_in_reais(client: ApiClient) -> None:
    """`quantity x average_price` está em USD; virar patrimônio em BRL seria inventar câmbio."""
    auth = user_auth(client)
    body = {"type": "us_stock", "symbol": "AAPL", "quantity": "5", "average_price": "200.00"}

    refused = client.post(INVESTMENTS, headers=auth, json=body)
    accepted = client.post(INVESTMENTS, headers=auth, json={**body, "invested_amount": "5200.00"})

    assert refused.status_code == 422, refused.text
    assert "invested_amount" in refused.text
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["invested_amount"] == "5200.00"


def test_a_patch_refuses_a_field_of_the_other_half(client: ApiClient) -> None:
    user = register_user(client)
    cdb = a_cdb(client, user)

    response = client.patch(
        f"{INVESTMENTS}/{cdb['id']}", headers=user.auth, json={"quantity": "10"}
    )

    assert response.status_code == 422, response.text
    assert error_code(response) == "invalid_investment_fields"
    assert response.json()["error"]["details"][0]["field"] == "quantity"


# ---------------------------------------------------------------- aporte


def test_a_contribution_records_an_expense_and_grows_the_position(client: ApiClient) -> None:
    """Preço médio é média ponderada: 100 a 30,00 mais 100 a 40,00 dá 35,00."""
    user = register_user(client)
    investment = a_stock(client, user)
    category = a_category(client, user, name="Investimentos", kind="expense")

    response = client.post(
        f"{INVESTMENTS}/{investment['id']}/contributions",
        headers=user.auth,
        json={
            "amount": "4000.00",
            "category_id": category,
            "quantity": "100",
            "unit_price": "40.00",
            "occurred_on": "2026-05-10",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["investment"]["quantity"] == "200"
    assert body["investment"]["average_price"] == "35"
    assert body["investment"]["invested_amount"] == "7000.00"
    assert body["investment"]["current_value"] == "7000.00"
    assert body["transaction"]["kind"] == "expense"
    assert body["transaction"]["amount"] == "4000.00"
    assert body["transaction"]["occurred_on"] == "2026-05-10"
    assert body["transaction"]["description"] == "Aporte em PETR4"


def test_the_contribution_appears_in_the_statement_and_in_the_balance(client: ApiClient) -> None:
    """Aporte é lançamento comum: entra no extrato e no saldo como qualquer despesa."""
    user = register_user(client)
    investment = a_stock(client, user)
    category = a_category(client, user, name="Investimentos", kind="expense")

    client.post(
        f"{INVESTMENTS}/{investment['id']}/contributions",
        headers=user.auth,
        json={"amount": "250.00", "category_id": category, "quantity": "5", "unit_price": "50.00"},
    )

    statement = client.get(TRANSACTIONS, headers=user.auth).json()["items"]
    balance = client.get("/api/v1/balance/current", headers=user.auth).json()
    assert [item["amount"] for item in statement] == ["250.00"]
    assert statement[0]["investment_id"] == investment["id"]
    assert balance["expense"] == "250.00"


def test_a_contribution_in_reais_derives_the_unit_price(client: ApiClient) -> None:
    """Em ativo cotado em real, `amount / quantity` é o preço pago — e basta."""
    user = register_user(client)
    investment = a_stock(client, user, quantity="10", average_price="10.00")
    category = a_category(client, user, name="Investimentos", kind="expense")

    response = client.post(
        f"{INVESTMENTS}/{investment['id']}/contributions",
        headers=user.auth,
        json={"amount": "300.00", "category_id": category, "quantity": "10"},
    )

    assert response.status_code == 201, response.text
    # 10 a 10,00 mais 10 a 30,00 = 20 a 20,00.
    assert response.json()["investment"]["average_price"] == "20"


def test_a_contribution_refuses_an_income_category(client: ApiClient) -> None:
    """Categoria de receita faria o dinheiro investido *entrar* no saldo."""
    user = register_user(client)
    investment = a_stock(client, user)
    category = a_category(client, user, name="Salário extra", kind="income")

    response = client.post(
        f"{INVESTMENTS}/{investment['id']}/contributions",
        headers=user.auth,
        json={"amount": "100.00", "category_id": category, "quantity": "1"},
    )

    assert response.status_code == 422, response.text
    assert error_code(response) == "invalid_category_kind"


def test_a_contribution_to_fixed_income_only_adds_money(client: ApiClient) -> None:
    user = register_user(client)
    cdb = a_cdb(client, user)
    category = a_category(client, user, name="Investimentos", kind="expense")

    response = client.post(
        f"{INVESTMENTS}/{cdb['id']}/contributions",
        headers=user.auth,
        json={"amount": "1000.00", "category_id": category},
    )

    assert response.status_code == 201, response.text
    assert response.json()["investment"]["invested_amount"] == "6000.00"
    assert response.json()["investment"]["quantity"] is None


def test_a_contribution_to_fixed_income_refuses_a_quantity(client: ApiClient) -> None:
    user = register_user(client)
    cdb = a_cdb(client, user)
    category = a_category(client, user, name="Investimentos", kind="expense")

    response = client.post(
        f"{INVESTMENTS}/{cdb['id']}/contributions",
        headers=user.auth,
        json={"amount": "100.00", "category_id": category, "quantity": "3"},
    )

    assert response.status_code == 422, response.text
    assert error_code(response) == "invalid_investment_fields"


# -------------------------------------------------------------- provento


def test_an_earning_records_an_income_and_leaves_the_position_alone(client: ApiClient) -> None:
    """Dividendo cai na conta, não vira cota — reinvestir é um aporte à parte."""
    user = register_user(client)
    investment = a_stock(client, user)
    category = a_category(client, user, name="Dividendos", kind="income")

    response = client.post(
        f"{INVESTMENTS}/{investment['id']}/earnings",
        headers=user.auth,
        json={"amount": "120.00", "category_id": category},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["transaction"]["kind"] == "income"
    assert body["transaction"]["description"] == "Provento de PETR4"
    assert body["investment"]["quantity"] == investment["quantity"]
    assert body["investment"]["invested_amount"] == investment["invested_amount"]
    assert client.get("/api/v1/balance/current", headers=user.auth).json()["income"] == "120.00"


def test_an_earning_refuses_an_expense_category(client: ApiClient) -> None:
    user = register_user(client)
    investment = a_stock(client, user)
    category = a_category(client, user, name="Corretagem", kind="expense")

    response = client.post(
        f"{INVESTMENTS}/{investment['id']}/earnings",
        headers=user.auth,
        json={"amount": "10.00", "category_id": category},
    )

    assert response.status_code == 422, response.text
    assert error_code(response) == "invalid_category_kind"


# ------------------------------------------------------- exclusão e escopo


def test_deleting_the_position_keeps_the_money_that_moved(client: ApiClient) -> None:
    """`ON DELETE SET NULL`: o aporte saiu da conta de verdade.

    Apagá-lo para remover um rótulo falsificaria o saldo do mês em que aconteceu.
    """
    user = register_user(client)
    investment = a_stock(client, user)
    category = a_category(client, user, name="Investimentos", kind="expense")
    client.post(
        f"{INVESTMENTS}/{investment['id']}/contributions",
        headers=user.auth,
        json={"amount": "500.00", "category_id": category, "quantity": "10"},
    )

    assert client.delete(f"{INVESTMENTS}/{investment['id']}", headers=user.auth).status_code == 204

    statement = client.get(TRANSACTIONS, headers=user.auth).json()["items"]
    assert [item["amount"] for item in statement] == ["500.00"]
    assert statement[0]["investment_id"] is None
    assert client.get("/api/v1/balance/current", headers=user.auth).json()["expense"] == "500.00"


def test_the_position_of_someone_else_is_not_found(client: ApiClient) -> None:
    """404 e não 403: um 403 confirmaria que a posição existe."""
    owner = register_user(client)
    intruder = register_user(client, email="outro@exemplo.com", username="outro")
    investment = a_stock(client, owner)

    for method in ("GET", "PATCH", "DELETE"):
        response = client.request(
            method,
            f"{INVESTMENTS}/{investment['id']}",
            headers=intruder.auth,
            json={"name": "roubado"} if method == "PATCH" else None,
        )
        assert response.status_code == 404, f"{method} respondeu {response.status_code}"
        assert error_code(response) == "investment_not_found"


def test_the_listing_only_shows_what_is_mine(client: ApiClient) -> None:
    owner = register_user(client)
    intruder = register_user(client, email="outro@exemplo.com", username="outro")
    a_stock(client, owner)

    listing = client.get(INVESTMENTS, headers=intruder.auth).json()

    assert listing == {"items": [], "total": 0, "limit": 50, "offset": 0}


def test_the_listing_filters_by_class_and_by_type(client: ApiClient) -> None:
    user = register_user(client)
    a_stock(client, user)
    a_cdb(client, user)

    def symbols(**params: Any) -> list[str]:
        page = client.get(INVESTMENTS, headers=user.auth, params=params).json()
        return sorted(item["type"] for item in page["items"])

    assert symbols() == ["br_stock", "cdb"]
    assert symbols(**{"class": "fixed_income"}) == ["cdb"]
    assert symbols(**{"class": "variable_income"}) == ["br_stock"]
    assert symbols(type="cdb") == ["cdb"]


# ----------------------------------------------------------------- resumo


def test_the_summary_adds_up_the_portfolio_and_the_allocation(client: ApiClient) -> None:
    user = register_user(client)
    a_stock(client, user)  # 100 x 30,00 = 3.000,00
    a_cdb(client, user)  # 5.000,00

    summary = client.get(f"{INVESTMENTS}/summary", headers=user.auth).json()

    assert summary["total_value"] == "8000.00"
    assert summary["total_invested"] == "8000.00"
    assert summary["profit"] == "0.00"
    assert summary["positions"] == 2
    assert {share["label"]: share["percent"] for share in summary["by_class"]} == {
        "fixed_income": "62.5000",
        "variable_income": "37.5000",
    }
    assert [share["label"] for share in summary["by_type"]] == ["cdb", "br_stock"]
    # Nenhuma rodada do agendador ainda: a tela diz "ainda não cotado".
    assert summary["value_updated_at"] is None


def test_an_empty_portfolio_summarises_to_zero(client: ApiClient) -> None:
    """Sem posição nenhuma não há percentual a calcular — e dividir por zero não é número."""
    user = register_user(client)

    summary = client.get(f"{INVESTMENTS}/summary", headers=user.auth).json()

    assert summary["total_value"] == "0.00"
    assert summary["profit_percent"] is None
    assert summary["positions"] == 0
    assert summary["by_class"] == []


def test_the_summary_only_counts_my_own_positions(client: ApiClient) -> None:
    owner = register_user(client)
    intruder = register_user(client, email="outro@exemplo.com", username="outro")
    a_cdb(client, owner)

    summary = client.get(f"{INVESTMENTS}/summary", headers=intruder.auth).json()

    assert summary["total_value"] == "0.00"
    assert summary["positions"] == 0


# ------------------------------------------------------------------ apoio


def test_the_transaction_points_back_to_the_investment(client: ApiClient) -> None:
    """A volta existe no banco, e não só na resposta da requisição que a criou."""
    user = register_user(client)
    investment = a_stock(client, user)
    category = a_category(client, user, name="Investimentos", kind="expense")
    client.post(
        f"{INVESTMENTS}/{investment['id']}/contributions",
        headers=user.auth,
        json={"amount": "100.00", "category_id": category, "quantity": "2"},
    )

    async def linked(session: AsyncSession) -> UUID | None:
        result = await session.scalars(select(Transaction.investment_id))
        return result.first()

    assert client.in_the_database(linked) == UUID(investment["id"])


def user_auth(client: ApiClient) -> dict[str, str]:
    """Um usuário novo só para a requisição que vem — quando o teste não precisa dele depois."""
    return register_user(client).auth
