"""Regra de relatórios — sem banco, sem HTTP e sem gerar um PDF sequer.

O serviço devolve um `Report` (nome do arquivo + `Document`), e é por isso que
o que a folha **diz** se verifica aqui, em estruturas: as linhas da tabela, os
totais em destaque, os filtros impressos no cabeçalho e o nome com que o arquivo
chega na pasta de downloads. Que esse documento vire um PDF legível é assunto de
`test_pdf.py`; que ele chegue ao navegador com os cabeçalhos certos é de
`tests/api/test_reports.py`.

As dependências são `Protocol` — os serviços de lançamentos e de saldos — e aqui
entram dubles. Que a consulta filtre por dono e some certo é assunto de quem faz
SQL, e está em `tests/integration/`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from core.clock import Clock, MonthRange, month_range, months_between
from core.errors import ReportTooLargeError
from core.pdf import Block, Brand, Document, NoteBlock, SummaryBlock, TableBlock, Tone
from models.category import Category, CategoryKind
from models.transaction import Transaction
from models.user import Role, User
from repositories.balance_repository import Totals
from repositories.transaction_repository import TransactionFilters
from services.balance_service import MonthBalance, MonthlySeries, PeriodBalance
from services.report_service import (
    MAX_ROWS,
    BalanceReader,
    CategoryLookup,
    ReportService,
    TransactionLister,
)
from services.transaction_service import TransactionPage

SAO_PAULO = ZoneInfo("America/Sao_Paulo")

MEIO_DE_SETEMBRO = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
VIRADA_DO_MES = datetime(2026, 9, 1, 2, 0, tzinfo=UTC)
"""1º de setembro às 2h em UTC — ainda 31 de agosto, às 23h, em São Paulo."""

SALARIO = "Salário"
MERCADO = "Mercado"


# ------------------------------------------------------------------- dubles


def make_user(first_name: str = "Ana", last_name: str = "Ribeiro") -> User:
    return User(
        id=uuid4(),
        email="ana@exemplo.com",
        username="ana",
        password_hash="$argon2id$hash-de-mentira",
        first_name=first_name,
        last_name=last_name,
        role=Role.USER,
        is_active=True,
    )


def make_category(name: str, kind: CategoryKind) -> Category:
    return Category(id=uuid4(), user_id=None, name=name, kind=kind)


def make_transaction(
    amount: str,
    category: Category,
    *,
    day: date = date(2026, 9, 5),
    description: str = "Feira do mês",
) -> Transaction:
    return Transaction(
        id=uuid4(),
        user_id=uuid4(),
        category_id=category.id,
        category=category,
        amount=Decimal(amount),
        occurred_on=day,
        description=description,
    )


class FakeTransactionLister:
    """Implementa `TransactionLister`, guardando o recorte que recebeu.

    `total` é separado de `items` de propósito: é assim que o teto de linhas se
    exercita sem construir dois mil objetos só para provocar a recusa.
    """

    def __init__(self, items: Sequence[Transaction] = (), *, total: int | None = None) -> None:
        self.items = list(items)
        self.total = len(self.items) if total is None else total
        self.last_call: dict[str, object] | None = None

    async def list_transactions(
        self, user: User, *, filters: TransactionFilters, limit: int, offset: int
    ) -> TransactionPage:
        self.last_call = {"user": user, "filters": filters, "limit": limit, "offset": offset}
        return TransactionPage(items=self.items, total=self.total, limit=limit, offset=offset)


class FakeBalanceReader:
    """Implementa `BalanceReader` a partir de totais declarados por mês."""

    def __init__(self, by_month: dict[str, Totals] | None = None) -> None:
        self.by_month = {
            month_range(int(key[:4]), int(key[5:])): value
            for key, value in (by_month or {}).items()
        }
        self.last_call: dict[str, object] | None = None

    async def monthly(
        self, user: User, *, first: MonthRange | None = None, last: MonthRange | None = None
    ) -> MonthlySeries:
        self.last_call = {"user": user, "first": first, "last": last}
        span = sorted(self.by_month, key=lambda month: month.first_day)
        window = months_between(
            first or (span[0] if span else month_range(2026, 9)),
            last or (span[-1] if span else month_range(2026, 9)),
        )
        months = [
            MonthBalance(month=month, totals=self.by_month.get(month, Totals())) for month in window
        ]
        total = Totals()
        for month in months:
            total = total + month.totals
        return MonthlySeries(months=months, totals=total)

    async def in_range(self, user: User, *, first_day: date, last_day: date) -> PeriodBalance:
        self.last_call = {"user": user, "first_day": first_day, "last_day": last_day}
        total = Totals()
        for month, totals in self.by_month.items():
            if first_day <= month.first_day and month.last_day <= last_day:
                total = total + totals
        return PeriodBalance(first_day=first_day, last_day=last_day, totals=total)


class FakeCategoryLookup:
    """Implementa `CategoryLookup`. Categoria de outra pessoa some, como no repositório."""

    def __init__(self, *categories: Category) -> None:
        self.by_id = {category.id: category for category in categories}

    async def get_visible(self, category_id: UUID, user_id: UUID) -> Category | None:
        return self.by_id.get(category_id)


def build_service(
    *,
    transactions: FakeTransactionLister | None = None,
    balances: FakeBalanceReader | None = None,
    categories: FakeCategoryLookup | None = None,
    clock: Clock | None = None,
    brand: Brand | None = None,
) -> ReportService:
    # As anotações forçam a checagem de que os dubles satisfazem os Protocols.
    lister: TransactionLister = transactions or FakeTransactionLister()
    reader: BalanceReader = balances or FakeBalanceReader()
    lookup: CategoryLookup = categories or FakeCategoryLookup()
    return ReportService(
        transactions=lister,
        balances=reader,
        categories=lookup,
        clock=clock or Clock(tz=SAO_PAULO, instant=lambda: MEIO_DE_SETEMBRO),
        brand=brand or Brand(name="finance-api"),
    )


# -------------------------------------------------------------------- apoio


def table_of(document: Document) -> TableBlock:
    return next(block for block in document.blocks if isinstance(block, TableBlock))


def summary_of(document: Document) -> SummaryBlock:
    return next(block for block in document.blocks if isinstance(block, SummaryBlock))


def blocks_of(document: Document, kind: type[Block]) -> list[Block]:
    return [block for block in document.blocks if isinstance(block, kind)]


def filters_of(document: Document) -> dict[str, str]:
    return dict(document.filters)


def values_of(summary: SummaryBlock) -> dict[str, str]:
    return {item.label: item.value for item in summary.items}


@pytest.fixture
def user() -> User:
    return make_user()


@pytest.fixture
def mercado() -> Category:
    return make_category(MERCADO, CategoryKind.EXPENSE)


@pytest.fixture
def salario() -> Category:
    return make_category(SALARIO, CategoryKind.INCOME)


# -------------------------------------------------------------- o extrato


async def test_the_extract_has_one_row_per_transaction(
    user: User, salario: Category, mercado: Category
) -> None:
    lister = FakeTransactionLister(
        [
            make_transaction("5000.00", salario, day=date(2026, 9, 5), description="Salário"),
            make_transaction("1200.50", mercado, day=date(2026, 9, 20), description="Feira"),
        ]
    )
    service = build_service(transactions=lister)

    report = await service.transactions(user, filters=TransactionFilters())

    assert table_of(report.document).rows == [
        ["05/09/2026", "Salário", SALARIO, "5.000,00", ""],
        ["20/09/2026", "Feira", MERCADO, "", "1.200,50"],
    ]


async def test_income_and_expense_go_to_their_own_columns(user: User, mercado: Category) -> None:
    """Colunas separadas é o que permite somar com o dedo na folha impressa."""
    lister = FakeTransactionLister([make_transaction("40.00", mercado)])
    service = build_service(transactions=lister)

    report = await service.transactions(user, filters=TransactionFilters())
    columns = [column.header for column in table_of(report.document).columns]
    row = table_of(report.document).rows[0]

    assert columns == ["Data", "Descrição", "Categoria", "Receita", "Despesa"]
    assert row[columns.index("Receita")] == ""
    assert row[columns.index("Despesa")] == "40,00"


async def test_the_totals_are_the_sum_of_the_printed_rows(
    user: User, salario: Category, mercado: Category
) -> None:
    """Nenhuma consulta à parte para o resumo: ele soma exatamente o que está na folha."""
    lister = FakeTransactionLister(
        [
            make_transaction("5000.00", salario),
            make_transaction("5000.00", salario),
            make_transaction("1200.50", mercado),
        ]
    )
    service = build_service(transactions=lister)

    report = await service.transactions(user, filters=TransactionFilters())

    assert values_of(summary_of(report.document)) == {
        "Receitas": "10.000,00",
        "Despesas": "1.200,50",
        "Saldo": "8.799,50",
        "Lançamentos": "3",
    }


async def test_a_negative_balance_is_marked_as_such(user: User, mercado: Category) -> None:
    """O tom vem de quem calculou: o renderizador não olha o sinal do texto."""
    lister = FakeTransactionLister([make_transaction("40.00", mercado)])
    service = build_service(transactions=lister)

    report = await service.transactions(user, filters=TransactionFilters())
    saldo = next(item for item in summary_of(report.document).items if item.label == "Saldo")

    assert saldo.value == "-40,00"
    assert saldo.tone is Tone.NEGATIVE


async def test_an_empty_recorte_says_so_instead_of_an_empty_table(user: User) -> None:
    service = build_service(transactions=FakeTransactionLister())

    report = await service.transactions(user, filters=TransactionFilters())

    assert not blocks_of(report.document, TableBlock)
    assert blocks_of(report.document, NoteBlock), "faltou dizer que não há lançamento"
    assert values_of(summary_of(report.document))["Lançamentos"] == "0"


async def test_a_transaction_without_description_gets_a_placeholder(
    user: User, mercado: Category
) -> None:
    lister = FakeTransactionLister([make_transaction("10.00", mercado, description="")])
    service = build_service(transactions=lister)

    report = await service.transactions(user, filters=TransactionFilters())

    assert table_of(report.document).rows[0][1] == "—"


# ------------------------------------------------------------------ o teto


async def test_a_recorte_past_the_ceiling_is_refused(user: User) -> None:
    """Sem teto, "exportar tudo" viraria centenas de páginas e um pico de memória."""
    service = build_service(transactions=FakeTransactionLister(total=MAX_ROWS + 1))

    with pytest.raises(ReportTooLargeError) as raised:
        await service.transactions(user, filters=TransactionFilters())

    assert str(MAX_ROWS) in raised.value.message
    assert str(MAX_ROWS + 1) in raised.value.message, "a recusa não diz quantos são"


async def test_the_ceiling_itself_is_accepted(user: User) -> None:
    service = build_service(transactions=FakeTransactionLister(total=MAX_ROWS))

    report = await service.transactions(user, filters=TransactionFilters())

    assert values_of(summary_of(report.document))["Lançamentos"] == str(MAX_ROWS)


async def test_the_whole_recorte_is_asked_for_at_once(user: User) -> None:
    """Relatório não pagina: pede o teto de uma vez, da primeira linha."""
    lister = FakeTransactionLister()
    service = build_service(transactions=lister)

    await service.transactions(user, filters=TransactionFilters())

    assert lister.last_call is not None
    assert lister.last_call["limit"] == MAX_ROWS
    assert lister.last_call["offset"] == 0


# -------------------------------------------------------- o recorte impresso


@pytest.mark.parametrize(
    ("kind", "title", "prefix"),
    [
        (None, "Lançamentos", "lancamentos"),
        (CategoryKind.INCOME, "Receitas", "receitas"),
        (CategoryKind.EXPENSE, "Despesas", "despesas"),
    ],
)
async def test_the_kind_filter_names_the_report(
    user: User, kind: CategoryKind | None, title: str, prefix: str
) -> None:
    """É o que faz o botão "exportar despesas" entregar um `despesas-....pdf`."""
    service = build_service()

    report = await service.transactions(user, filters=TransactionFilters(kind=kind))

    assert report.document.title == title
    assert report.filename.startswith(f"{prefix}-")


@pytest.mark.parametrize(
    ("occurred_from", "occurred_to", "subtitle", "span"),
    [
        (date(2026, 1, 1), date(2026, 3, 31), "01/01/2026 a 31/03/2026", "2026-01-01_2026-03-31"),
        (date(2026, 1, 1), None, "A partir de 01/01/2026", "desde-2026-01-01"),
        (None, date(2026, 3, 31), "Até 31/03/2026", "ate-2026-03-31"),
        (None, None, "Todo o histórico", "2026-09-15"),
    ],
)
async def test_the_period_shows_up_in_the_sheet_and_in_the_filename(
    user: User,
    occurred_from: date | None,
    occurred_to: date | None,
    subtitle: str,
    span: str,
) -> None:
    """Sem nenhuma ponta, quem nomeia o arquivo é a data de geração — ver `_period_slug`."""
    service = build_service()

    report = await service.transactions(
        user, filters=TransactionFilters(occurred_from=occurred_from, occurred_to=occurred_to)
    )

    assert report.document.subtitle == subtitle
    assert report.filename == f"lancamentos-{span}.pdf"


async def test_the_header_states_the_filters(user: User, mercado: Category) -> None:
    """Folha sem os filtros que a geraram é um monte de número que não se reconfere."""
    service = build_service(categories=FakeCategoryLookup(mercado))

    report = await service.transactions(
        user,
        filters=TransactionFilters(kind=CategoryKind.EXPENSE, category_id=mercado.id),
    )

    assert filters_of(report.document) == {"Tipo": "somente despesas", "Categoria": MERCADO}


async def test_without_filters_the_header_says_so(user: User) -> None:
    service = build_service()

    report = await service.transactions(user, filters=TransactionFilters())

    assert filters_of(report.document) == {"Tipo": "receitas e despesas", "Categoria": "todas"}


async def test_a_category_the_user_cannot_see_is_still_declared(user: User) -> None:
    """A listagem responde vazio para categoria de terceiro, e o relatório a acompanha.

    O cabeçalho não pode calar o filtro: a folha sairia vazia sem dizer por quê.
    """
    stranger = uuid4()
    service = build_service()

    report = await service.transactions(user, filters=TransactionFilters(category_id=stranger))

    assert filters_of(report.document)["Categoria"] == str(stranger)


# ------------------------------------------------------------ saldo mês a mês


async def test_the_monthly_report_has_one_row_per_month(user: User) -> None:
    """Inclusive os vazios: a série vem sem buraco do serviço de saldos."""
    balances = FakeBalanceReader(
        {
            "2026-07": Totals(income=Decimal("100.00"), expense=Decimal("30.00")),
            "2026-09": Totals(income=Decimal("50.00"), expense=Decimal("100.00")),
        }
    )
    service = build_service(balances=balances)

    report = await service.monthly_balance(
        user, first=month_range(2026, 7), last=month_range(2026, 9)
    )

    assert [row[0] for row in table_of(report.document).rows] == [
        "jul/2026",
        "ago/2026",
        "set/2026",
    ]
    assert table_of(report.document).rows[1] == ["ago/2026", "0,00", "0,00", "0,00"]
    assert report.document.subtitle == "jul/2026 a set/2026"
    assert report.filename == "saldo-mensal-2026-07_2026-09.pdf"


async def test_the_monthly_total_is_the_one_the_balance_service_computed(user: User) -> None:
    balances = FakeBalanceReader(
        {
            "2026-07": Totals(income=Decimal("100.00"), expense=Decimal("30.00")),
            "2026-08": Totals(income=Decimal("200.00"), expense=Decimal("70.00")),
        }
    )
    service = build_service(balances=balances)

    report = await service.monthly_balance(
        user, first=month_range(2026, 7), last=month_range(2026, 8)
    )

    assert values_of(summary_of(report.document)) == {
        "Receitas": "300,00",
        "Despesas": "100,00",
        "Saldo": "200,00",
    }


async def test_the_monthly_window_is_passed_through_untouched(user: User) -> None:
    """Quem resolve janela ausente é o serviço de saldos, e não este."""
    balances = FakeBalanceReader()
    service = build_service(balances=balances)

    await service.monthly_balance(user)

    assert balances.last_call == {"user": user, "first": None, "last": None}


# --------------------------------------------------------- saldo do intervalo


async def test_the_range_report_is_the_summary_of_the_period(user: User) -> None:
    balances = FakeBalanceReader({"2026-09": Totals(income=Decimal("80.00"))})
    service = build_service(balances=balances)

    report = await service.range_balance(
        user, first_day=date(2026, 9, 1), last_day=date(2026, 9, 30)
    )

    assert not blocks_of(report.document, TableBlock), "intervalo não lista lançamento"
    assert values_of(summary_of(report.document))["Receitas"] == "80,00"
    assert report.document.subtitle == "01/09/2026 a 30/09/2026"
    assert report.filename == "saldo-2026-09-01_2026-09-30.pdf"


async def test_the_range_header_says_the_ends_are_included(user: User) -> None:
    service = build_service()

    report = await service.range_balance(
        user, first_day=date(2026, 9, 1), last_day=date(2026, 9, 30)
    )

    assert "incluídas" in filters_of(report.document)["Período"]


# ------------------------------------------------------- marca, data e dono


async def test_the_generation_date_comes_from_the_clock(user: User) -> None:
    """Às 23h de 31 de agosto em São Paulo, o relatório é de 31 de agosto.

    O mesmo instante em UTC já é 1º de setembro, e `date.today()` no servidor
    carimbaria a folha com o dia seguinte.
    """
    service = build_service(clock=Clock(tz=SAO_PAULO, instant=lambda: VIRADA_DO_MES))

    report = await service.transactions(user, filters=TransactionFilters())

    assert report.document.generated_at == "Gerado em 31/08/2026 às 23:00"


async def test_the_owner_goes_in_the_footer(user: User) -> None:
    service = build_service()

    report = await service.transactions(user, filters=TransactionFilters())

    assert report.document.footer == "Ana Ribeiro · ana@exemplo.com"


async def test_an_account_without_a_name_falls_back_to_the_email() -> None:
    service = build_service()

    report = await service.transactions(
        make_user(first_name="", last_name=""), filters=TransactionFilters()
    )

    assert report.document.footer == "ana@exemplo.com"


async def test_the_brand_reaches_the_document(user: User) -> None:
    service = build_service(brand=Brand(name="minhas-financas"))

    report = await service.transactions(user, filters=TransactionFilters())

    assert report.document.brand.name == "minhas-financas"
