"""Regra de relatórios: o que o PDF diz.

Depende dos serviços de lançamentos e de saldos, não dos repositórios, para o PDF sair da mesma
resposta que a tela. Devolve um `Document`, não bytes: renderizar é assunto da borda HTTP.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from core.clock import Clock, MonthRange
from core.errors import ReportTooLargeError
from core.pdf import (
    Align,
    Block,
    Brand,
    Column,
    Document,
    NoteBlock,
    SummaryBlock,
    SummaryItem,
    TableBlock,
    Tone,
)
from models.category import Category, CategoryKind
from models.transaction import Transaction
from models.user import User
from repositories.balance_repository import ZERO, Totals
from repositories.transaction_repository import TransactionFilters
from services.balance_service import MonthlySeries, PeriodBalance
from services.transaction_service import TransactionPage

MAX_ROWS = 2000  # o PDF é montado inteiro em memória; ver issue #8


class TransactionLister(Protocol):
    async def list_transactions(
        self, user: User, *, filters: TransactionFilters, limit: int, offset: int
    ) -> TransactionPage: ...


class BalanceReader(Protocol):
    async def monthly(
        self, user: User, *, first: MonthRange | None = None, last: MonthRange | None = None
    ) -> MonthlySeries: ...

    async def in_range(self, user: User, *, first_day: date, last_day: date) -> PeriodBalance: ...


class CategoryLookup(Protocol):
    """Só para escrever o nome da categoria filtrada no cabeçalho."""

    async def get_visible(self, category_id: UUID, user_id: UUID) -> Category | None: ...


@dataclass(frozen=True, slots=True)
class Report:
    filename: str
    document: Document


TITLES = {
    None: "Lançamentos",
    CategoryKind.INCOME: "Receitas",
    CategoryKind.EXPENSE: "Despesas",
}

FILE_PREFIXES = {
    None: "lancamentos",
    CategoryKind.INCOME: "receitas",
    CategoryKind.EXPENSE: "despesas",
}

KIND_DESCRIPTIONS = {
    None: "receitas e despesas",
    CategoryKind.INCOME: "somente receitas",
    CategoryKind.EXPENSE: "somente despesas",
}

# À mão, sem `locale`: a imagem python:3.12-slim não tem pt_BR.
MONTH_ABBREVIATIONS = (
    "jan",
    "fev",
    "mar",
    "abr",
    "mai",
    "jun",
    "jul",
    "ago",
    "set",
    "out",
    "nov",
    "dez",
)

EMPTY = "—"


class ReportService:
    def __init__(
        self,
        *,
        transactions: TransactionLister,
        balances: BalanceReader,
        categories: CategoryLookup,
        clock: Clock,
        brand: Brand,
    ) -> None:
        self._transactions = transactions
        self._balances = balances
        self._categories = categories
        self._clock = clock
        self._brand = brand

    async def transactions(self, user: User, *, filters: TransactionFilters) -> Report:
        """Mesma ordem da listagem: do mais recente para o mais antigo."""
        page = await self._transactions.list_transactions(
            user, filters=filters, limit=MAX_ROWS, offset=0
        )
        if page.total > MAX_ROWS:
            raise ReportTooLargeError(
                f"O recorte pedido tem {page.total} lançamentos, e o máximo por relatório "
                f"é {MAX_ROWS}. Estreite o período ou filtre por categoria."
            )

        totals = _totals_of(page.items)
        period = _period_text(filters.occurred_from, filters.occurred_to)
        blocks: list[Block] = [
            SummaryBlock(
                [
                    *_totals_items(totals),
                    SummaryItem(label="Lançamentos", value=str(page.total)),
                ]
            ),
            _transactions_table(page.items),
        ]

        return Report(
            filename=self._filename(
                FILE_PREFIXES[filters.kind],
                _period_slug(filters.occurred_from, filters.occurred_to, self._clock.today()),
            ),
            document=self._document(
                user,
                title=TITLES[filters.kind],
                subtitle=period,
                filters=[
                    ("Tipo", KIND_DESCRIPTIONS[filters.kind]),
                    ("Categoria", await self._category_name(user, filters.category_id)),
                ],
                blocks=blocks,
            ),
        )

    async def monthly_balance(
        self, user: User, *, first: MonthRange | None = None, last: MonthRange | None = None
    ) -> Report:
        series = await self._balances.monthly(user, first=first, last=last)
        window = f"{_month(series.months[0].month)} a {_month(series.months[-1].month)}"

        rows = [
            [
                _month(month.month),
                _money(month.totals.income),
                _money(month.totals.expense),
                _money(month.totals.net),
            ]
            for month in series.months
        ]

        return Report(
            filename=self._filename(
                "saldo-mensal",
                f"{series.months[0].month.key}_{series.months[-1].month.key}",
            ),
            document=self._document(
                user,
                title="Saldo mês a mês",
                subtitle=window,
                filters=[("Meses no período", str(len(series.months)))],
                blocks=[
                    SummaryBlock(list(_totals_items(series.totals))),
                    TableBlock(
                        columns=[
                            Column("Mês", ratio=1.2),
                            Column("Receitas", ratio=1.4, align=Align.RIGHT),
                            Column("Despesas", ratio=1.4, align=Align.RIGHT),
                            Column("Saldo", ratio=1.4, align=Align.RIGHT),
                        ],
                        rows=rows,
                    ),
                ],
            ),
        )

    async def range_balance(self, user: User, *, first_day: date, last_day: date) -> Report:
        balance = await self._balances.in_range(user, first_day=first_day, last_day=last_day)
        period = f"{_date(balance.first_day)} a {_date(balance.last_day)}"

        return Report(
            filename=self._filename(
                "saldo", f"{balance.first_day.isoformat()}_{balance.last_day.isoformat()}"
            ),
            document=self._document(
                user,
                title="Saldo do período",
                subtitle=period,
                filters=[("Período", f"{period} (as duas pontas incluídas)")],
                blocks=[SummaryBlock(list(_totals_items(balance.totals)))],
            ),
        )

    def _document(
        self,
        user: User,
        *,
        title: str,
        subtitle: str,
        filters: Sequence[tuple[str, str]],
        blocks: Sequence[Block],
    ) -> Document:
        return Document(
            title=title,
            subtitle=subtitle,
            generated_at=self._generated_at(),
            footer=_owner(user),
            brand=self._brand,
            filters=filters,
            blocks=blocks,
        )

    def _generated_at(self) -> str:
        moment = self._clock.now_utc().astimezone(self._clock.tz)
        return f"Gerado em {moment:%d/%m/%Y às %H:%M}"

    def _filename(self, prefix: str, span: str) -> str:
        """Só ASCII: o nome atravessa o `Content-Disposition`."""
        return f"{prefix}-{span}.pdf"

    async def _category_name(self, user: User, category_id: UUID | None) -> str:
        """Categoria que o usuário não enxerga sai pelo id: filtro aplicado não some da folha."""
        if category_id is None:
            return "todas"
        category = await self._categories.get_visible(category_id, user.id)
        return category.name if category is not None else str(category_id)


# ---------------------------------------------------------------- montagem


def _transactions_table(items: Sequence[Transaction]) -> Block:
    if not items:
        return NoteBlock("Nenhum lançamento neste recorte.")

    return TableBlock(
        columns=[
            Column("Data", ratio=1.1),
            Column("Descrição", ratio=3.4, wrap=True),
            Column("Categoria", ratio=2.0, wrap=True),
            Column("Receita", ratio=1.4, align=Align.RIGHT),
            Column("Despesa", ratio=1.4, align=Align.RIGHT),
        ],
        rows=[
            [
                _date(item.occurred_on),
                item.description or EMPTY,
                item.category.name,
                _money(item.amount) if item.kind is CategoryKind.INCOME else "",
                _money(item.amount) if item.kind is CategoryKind.EXPENSE else "",
            ]
            for item in items
        ],
    )


def _totals_items(totals: Totals) -> tuple[SummaryItem, ...]:
    return (
        SummaryItem(label="Receitas", value=_money(totals.income), tone=Tone.POSITIVE),
        SummaryItem(label="Despesas", value=_money(totals.expense), tone=Tone.NEGATIVE),
        SummaryItem(
            label="Saldo",
            value=_money(totals.net),
            tone=Tone.POSITIVE if totals.net >= ZERO else Tone.NEGATIVE,
        ),
    )


def _totals_of(items: Sequence[Transaction]) -> Totals:
    """Soma as linhas impressas, para o total nunca desmentir a lista."""
    income = sum((item.amount for item in items if item.kind is CategoryKind.INCOME), ZERO)
    expense = sum((item.amount for item in items if item.kind is CategoryKind.EXPENSE), ZERO)
    return Totals(income=income, expense=expense)


def _owner(user: User) -> str:
    name = f"{user.first_name} {user.last_name}".strip()
    return f"{name} · {user.email}" if name else user.email


# --------------------------------------------------------------- formatação


def _money(value: Decimal) -> str:
    """`-1234.5` vira `-1.234,50`. Sem `locale`, pelo mesmo motivo dos meses."""
    american = f"{value:,.2f}"
    return american.replace(",", "_").replace(".", ",").replace("_", ".")


def _date(day: date) -> str:
    return f"{day:%d/%m/%Y}"


def _month(month: MonthRange) -> str:
    return f"{MONTH_ABBREVIATIONS[month.first_day.month - 1]}/{month.first_day.year}"


def _period_text(occurred_from: date | None, occurred_to: date | None) -> str:
    if occurred_from is not None and occurred_to is not None:
        return f"{_date(occurred_from)} a {_date(occurred_to)}"
    if occurred_from is not None:
        return f"A partir de {_date(occurred_from)}"
    if occurred_to is not None:
        return f"Até {_date(occurred_to)}"
    return "Todo o histórico"


def _period_slug(occurred_from: date | None, occurred_to: date | None, today: date) -> str:
    """Sem nenhuma ponta, o arquivo leva a data de geração."""
    if occurred_from is not None and occurred_to is not None:
        return f"{occurred_from.isoformat()}_{occurred_to.isoformat()}"
    if occurred_from is not None:
        return f"desde-{occurred_from.isoformat()}"
    if occurred_to is not None:
        return f"ate-{occurred_to.isoformat()}"
    return today.isoformat()
