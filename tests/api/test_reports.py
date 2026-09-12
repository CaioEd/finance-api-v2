"""As três rotas de relatório, da requisição ao arquivo.

O que só aqui se verifica é a **travessia inteira**: a query string vira recorte,
o recorte vira consulta, a consulta vira folha, e a folha chega ao navegador com
os cabeçalhos que fazem o download acontecer. As duas matrizes já garantem que a
rota exige token; o que a regra escreve na folha está em
`tests/unit/test_report_service.py`, e o desenho dela em `tests/unit/test_pdf.py`.

Duas afirmações que justificam esta suíte existir para os relatórios:

- o PDF traz **os lançamentos de quem pediu**, e nenhum de outra pessoa — num
  arquivo binário o escopo esquecido não vaza uma linha que alguém veja numa
  resposta JSON; vaza dentro de um anexo que ninguém abre no teste;
- o erro continua saindo no envelope de sempre. A rota devolve `application/pdf`
  no caminho feliz, e é fácil uma recusa virar um PDF vazio de 200 — que o front
  baixaria como se fosse o relatório.
"""

from __future__ import annotations

from tests.api.client import ApiClient, Response
from tests.api.factories import register_user
from tests.factories import RegisteredUser
from tests.pdf_text import text_of

SALARIO = ("Salário", "income")
MERCADO = ("Mercado", "expense")

EXTRACT = "/api/v1/reports/transactions"
MONTHLY = "/api/v1/reports/balance/monthly"
RANGE = "/api/v1/reports/balance/range"


def a_category(client: ApiClient, user: RegisteredUser, name_and_kind: tuple[str, str]) -> str:
    name, kind = name_and_kind
    response = client.post(
        "/api/v1/categories", headers=user.auth, json={"name": name, "kind": kind}
    )
    response.raise_for_status()
    return str(response.json()["id"])


def a_transaction(
    client: ApiClient,
    user: RegisteredUser,
    category_id: str,
    amount: str,
    day: str,
    description: str = "",
) -> None:
    response = client.post(
        "/api/v1/transactions",
        headers=user.auth,
        json={
            "amount": amount,
            "category_id": category_id,
            "occurred_on": day,
            "description": description,
        },
    )
    assert response.status_code == 201, response.text


def ana_with_a_september(client: ApiClient) -> RegisteredUser:
    """Uma conta com um mês de movimento: uma receita e uma despesa."""
    ana = register_user(client)
    salario = a_category(client, ana, SALARIO)
    mercado = a_category(client, ana, MERCADO)
    a_transaction(client, ana, salario, "5000.00", "2026-09-05", "Salário de setembro")
    a_transaction(client, ana, mercado, "1200.50", "2026-09-20", "Feira do mês")
    return ana


def assert_is_a_pdf(response: Response, filename: str) -> None:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-"), "o corpo não é um PDF"
    assert response.headers["content-disposition"] == f'attachment; filename="{filename}"'


# ------------------------------------------------------------------ download


def test_the_extract_comes_as_a_pdf_attachment(client: ApiClient) -> None:
    """`attachment` é o que faz o navegador baixar em vez de abrir na aba."""
    ana = ana_with_a_september(client)

    response = client.get(
        EXTRACT,
        headers=ana.auth,
        params={"occurred_from": "2026-09-01", "occurred_to": "2026-09-30"},
    )

    assert_is_a_pdf(response, "lancamentos-2026-09-01_2026-09-30.pdf")
    assert response.content.rstrip().endswith(b"%%EOF")


def test_the_extract_is_not_stored_by_the_browser(client: ApiClient) -> None:
    """Extrato tem saldo e descrição de gasto: fora do cache de disco, mesmo após o logout."""
    ana = ana_with_a_september(client)

    response = client.get(EXTRACT, headers=ana.auth)

    assert response.headers["cache-control"] == "no-store"


def test_the_declared_length_matches_the_file(client: ApiClient) -> None:
    """É o `Content-Length` que dá barra de progresso em vez de download sem tamanho."""
    ana = ana_with_a_september(client)

    response = client.get(EXTRACT, headers=ana.auth)

    assert int(response.headers["content-length"]) == len(response.content)


# ------------------------------------------------------------- o que sai nele


def test_the_extract_carries_the_transactions_of_the_period(client: ApiClient) -> None:
    ana = ana_with_a_september(client)

    response = client.get(
        EXTRACT,
        headers=ana.auth,
        params={"occurred_from": "2026-09-01", "occurred_to": "2026-09-30"},
    )
    sheet = text_of(response.content)

    assert "05/09/2026" in sheet and "5.000,00" in sheet
    assert "20/09/2026" in sheet and "1.200,50" in sheet
    assert "Salário" in sheet and "Mercado" in sheet
    assert "3.799,50" in sheet, "o saldo do recorte não saiu no resumo"
    assert ana.email in sheet, "a folha não diz de quem é"


def test_one_users_launches_never_reach_another_sheet(client: ApiClient) -> None:
    """O escopo por dono atravessa até dentro do anexo.

    Num PDF o vazamento não aparece na resposta que alguém lê no navegador — vai
    dentro do arquivo, que é exatamente onde ninguém olha.
    """
    ana = ana_with_a_september(client)
    bruno = register_user(client, email="bruno@exemplo.com", username="bruno")
    a_transaction(
        client, bruno, a_category(client, bruno, MERCADO), "999.99", "2026-09-10", "Segredo"
    )

    sheet = text_of(client.get(EXTRACT, headers=ana.auth).content)

    assert "999,99" not in sheet
    assert "Segredo" not in sheet


def test_the_kind_filter_names_the_file_and_narrows_the_sheet(client: ApiClient) -> None:
    """Exportar despesas é o extrato com `kind` — a mesma query string da listagem."""
    ana = ana_with_a_september(client)

    response = client.get(
        EXTRACT,
        headers=ana.auth,
        params={"kind": "expense", "occurred_from": "2026-09-01", "occurred_to": "2026-09-30"},
    )
    sheet = text_of(response.content)

    assert_is_a_pdf(response, "despesas-2026-09-01_2026-09-30.pdf")
    assert "1.200,50" in sheet
    assert "5.000,00" not in sheet, "a receita entrou num relatório de despesas"


def test_the_category_filter_narrows_the_sheet_and_is_declared_in_it(client: ApiClient) -> None:
    """O filtro por categoria vai para a consulta **e** para o cabeçalho da folha.

    Impresso, o relatório perde a query string que o gerou: sem o nome da
    categoria no alto, sobra uma lista curta sem explicação para quem a receber.
    """
    ana = register_user(client)
    mercado = a_category(client, ana, MERCADO)
    farmacia = a_category(client, ana, ("Farmácia", "expense"))
    a_transaction(client, ana, mercado, "1200.50", "2026-09-20", "Feira do mês")
    a_transaction(client, ana, farmacia, "80.00", "2026-09-21", "Remédio")

    response = client.get(EXTRACT, headers=ana.auth, params={"category_id": mercado})
    sheet = text_of(response.content)

    assert response.status_code == 200, response.text
    assert "1.200,50" in sheet
    assert "80,00" not in sheet, "a outra categoria entrou no relatório"
    assert "Mercado" in sheet


def test_an_empty_recorte_still_produces_a_sheet(client: ApiClient) -> None:
    """Nada a listar é um PDF dizendo isso, não um erro nem um arquivo vazio."""
    ana = register_user(client)

    response = client.get(
        EXTRACT,
        headers=ana.auth,
        params={"occurred_from": "2020-01-01", "occurred_to": "2020-12-31"},
    )

    assert_is_a_pdf(response, "lancamentos-2020-01-01_2020-12-31.pdf")
    assert "Nenhum lançamento neste recorte." in text_of(response.content)


# --------------------------------------------------------------- os saldos


def test_the_monthly_report_has_the_months_of_the_window(client: ApiClient) -> None:
    """O agrupamento por mês passa pelo `date_trunc` traduzido — ver `sqlite_backend`."""
    ana = ana_with_a_september(client)

    response = client.get(
        MONTHLY, headers=ana.auth, params={"from_month": "2026-08", "to_month": "2026-09"}
    )
    sheet = text_of(response.content)

    assert_is_a_pdf(response, "saldo-mensal-2026-08_2026-09.pdf")
    assert "ago/2026" in sheet and "set/2026" in sheet
    assert "3.799,50" in sheet, "o saldo de setembro não saiu na linha do mês"


def test_the_range_report_totals_the_period(client: ApiClient) -> None:
    ana = ana_with_a_september(client)

    response = client.get(
        RANGE,
        headers=ana.auth,
        params={"occurred_from": "2026-09-01", "occurred_to": "2026-09-30"},
    )
    sheet = text_of(response.content)

    assert_is_a_pdf(response, "saldo-2026-09-01_2026-09-30.pdf")
    assert "5.000,00" in sheet and "1.200,50" in sheet and "3.799,50" in sheet


# ----------------------------------------------------------------- as recusas


def test_an_inverted_period_answers_json_in_the_envelope(client: ApiClient) -> None:
    """A recusa não pode sair como um PDF de 200 que o front baixaria como relatório."""
    ana = register_user(client)

    response = client.get(
        RANGE,
        headers=ana.auth,
        params={"occurred_from": "2026-09-30", "occurred_to": "2026-09-01"},
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "invalid_period"


def test_a_malformed_month_is_refused_before_the_report_is_built(client: ApiClient) -> None:
    ana = register_user(client)

    response = client.get(MONTHLY, headers=ana.auth, params={"from_month": "2026-13"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
