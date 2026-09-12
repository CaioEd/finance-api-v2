"""Regra de relatórios: o que o PDF diz.

Python puro, como os demais serviços — nada aqui conhece HTTP, FastAPI nem
reportlab. O serviço devolve um `Report`: o nome do arquivo e um `Document`
(`core.pdf`), que é texto já formatado. Quem transforma isso em bytes é a borda
(`api/routes/reports.py`), e o motivo é de camada: renderizar é serializar, e
tirar a renderização do event loop é assunto de quem serve HTTP. De quebra, o
teste desta regra não precisa gerar PDF nenhum para conferir o que a folha diz.

Três decisões vivem aqui:

1. **O relatório nasce das mesmas respostas que a tela.** As dependências são os
   serviços de lançamentos e de saldos, não os repositórios: um segundo caminho
   de consulta é como o PDF e a tela passam a discordar em silêncio — o mesmo
   argumento que faz `net` ser derivado e não coluna.
2. **Os totais impressos são a soma das linhas impressas.** Nenhuma consulta à
   parte para o rodapé: o recorte cabe inteiro na folha (ver `MAX_ROWS`), e
   somar o que está ali é a única forma de o total nunca desmentir a lista.
3. **O relatório declara o recorte que o produziu.** Período, tipo e categoria
   saem impressos. PDF sem os filtros que o geraram é um monte de número que
   ninguém consegue reconferir depois de o arquivo sair do navegador.

Formatação de dinheiro e de data é daqui, e não de `core.pdf`: o renderizador
não deve saber em que idioma se escreve um número. O projeto não guarda moeda
em lugar nenhum (ver `models.transaction`), e o PDF não inventa uma — imprime o
valor, como o JSON faz.
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

MAX_ROWS = 2000
"""Teto de linhas de um relatório de lançamentos — umas quarenta páginas.

O PDF é montado inteiro na memória e baixado de uma vez: diferente da listagem,
que pagina, aqui não há como entregar "a primeira página e o resto depois". O
teto é generoso para uso pessoal e existe para que exportar dez anos de conta
vire uma recusa legível em vez de um pico de memória. Ver `ReportTooLargeError`.
"""


class TransactionLister(Protocol):
    """O que este serviço precisa do domínio de lançamentos: a listagem filtrada."""

    async def list_transactions(
        self, user: User, *, filters: TransactionFilters, limit: int, offset: int
    ) -> TransactionPage: ...


class BalanceReader(Protocol):
    """O que este serviço precisa do domínio de saldos.

    Sem `current_month`: o relatório do mês corrente é o do intervalo entre o
    primeiro e o último dia dele, e `GET /balance/current` já devolve as duas
    pontas para quem consome repassar.
    """

    async def monthly(
        self, user: User, *, first: MonthRange | None = None, last: MonthRange | None = None
    ) -> MonthlySeries: ...

    async def in_range(self, user: User, *, first_day: date, last_day: date) -> PeriodBalance: ...


class CategoryLookup(Protocol):
    """Só para escrever o nome da categoria filtrada no cabeçalho do relatório."""

    async def get_visible(self, category_id: UUID, user_id: UUID) -> Category | None: ...


@dataclass(frozen=True, slots=True)
class Report:
    """Um relatório pronto para virar arquivo: como se chama e o que diz."""

    filename: str
    document: Document


TITLES = {
    None: "Lançamentos",
    CategoryKind.INCOME: "Receitas",
    CategoryKind.EXPENSE: "Despesas",
}
"""O filtro de tipo vira o título — e o nome do arquivo baixado.

É o que faz o botão "exportar despesas" da tela entregar um `despesas-....pdf`,
em vez de três arquivos chamados `lancamentos.pdf` na pasta de downloads.
"""

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
"""Escritos à mão, e não vindos de `locale`.

`locale.setlocale(LC_TIME, "pt_BR")` depende de o sistema ter o locale gerado, e
a imagem `python:3.12-slim` não tem nenhum além do C — o mês sairia em inglês em
produção e em português na máquina de quem escreveu o código.
"""

EMPTY = "—"
"""Célula sem conteúdo. Vazia, a linha parece dado faltando em vez de campo em branco."""


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
        """O extrato: os lançamentos do recorte, com os totais dele no alto.

        A ordem é a mesma da listagem — do mais recente para o mais antigo. O
        relatório é a folha do que está na tela, e inverter aqui faria a
        conferência linha a linha depender de ler os dois de pontas opostas.
        """
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
        """O saldo mês a mês: uma linha por mês do período, inclusive os vazios."""
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
        """O saldo de um intervalo de datas: três números e o recorte que os produziu."""
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
        """A folha montada: o que muda por relatório entra, o resto é igual em todos."""
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
        """A data do relatório vem do `Clock`, no fuso da aplicação.

        Nunca de `date.today()`: quem imprime às 23h de 31 de agosto em São Paulo
        precisa ver 31 de agosto, e não o 1º de setembro que é em UTC.
        """
        moment = self._clock.now_utc().astimezone(self._clock.tz)
        return f"Gerado em {moment:%d/%m/%Y às %H:%M}"

    def _filename(self, prefix: str, span: str) -> str:
        """`despesas-2026-01-01_2026-09-12.pdf`: só ASCII, e o recorte no nome.

        Sem acento e sem espaço de propósito — o nome atravessa o cabeçalho
        `Content-Disposition` e vira nome de arquivo em três sistemas
        diferentes. O recorte no nome é o que evita a pasta de downloads com
        cinco `relatorio.pdf` indistinguíveis.
        """
        return f"{prefix}-{span}.pdf"

    async def _category_name(self, user: User, category_id: UUID | None) -> str:
        """O nome da categoria filtrada, para o cabeçalho.

        Sem o filtro, "todas". Com um id que o usuário não enxerga, o próprio id:
        a listagem responde vazio nesse caso em vez de recusar, e o relatório
        segue o mesmo contrato — mas o cabeçalho não pode omitir um filtro que
        foi aplicado, ou a folha mente sobre o próprio recorte.
        """
        if category_id is None:
            return "todas"
        category = await self._categories.get_visible(category_id, user.id)
        return category.name if category is not None else str(category_id)


# ---------------------------------------------------------------- montagem


def _transactions_table(items: Sequence[Transaction]) -> Block:
    """A tabela do extrato — ou o aviso de que não há o que listar.

    Receita e despesa em colunas separadas, e não numa coluna "tipo": é assim
    que se soma uma coluna com o dedo na folha impressa, que é a razão de o PDF
    existir.
    """
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
    """Os três números de destaque, com a cor dizendo o que cada um significa."""
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
    """Soma as linhas que vão para a folha — ver a decisão 2 no topo do módulo."""
    income = sum((item.amount for item in items if item.kind is CategoryKind.INCOME), ZERO)
    expense = sum((item.amount for item in items if item.kind is CategoryKind.EXPENSE), ZERO)
    return Totals(income=income, expense=expense)


def _owner(user: User) -> str:
    """Quem pediu o relatório, no rodapé de toda folha.

    Nome e e-mail: a folha impressa sai do navegador e circula sozinha, e sem
    dono não há como saber de qual conta ela é — nem se é da conta certa.
    """
    name = f"{user.first_name} {user.last_name}".strip()
    return f"{name} · {user.email}" if name else user.email


# --------------------------------------------------------------- formatação


def _money(value: Decimal) -> str:
    """`Decimal("-1234.5")` vira `-1.234,50`: ponto de milhar, vírgula de centavo.

    Sem `locale`: a formatação passaria a depender de o sistema ter `pt_BR`
    gerado — a imagem `python:3.12-slim` não tem —, e o mesmo código imprimiria
    `1,234.50` em produção e `1.234,50` na máquina de quem o escreveu.
    """
    american = f"{value:,.2f}"
    return american.replace(",", "_").replace(".", ",").replace("_", ".")


def _date(day: date) -> str:
    return f"{day:%d/%m/%Y}"


def _month(month: MonthRange) -> str:
    """`set/2026`. Na API o mês é `2026-09`; na folha, quem lê é gente."""
    return f"{MONTH_ABBREVIATIONS[month.first_day.month - 1]}/{month.first_day.year}"


def _period_text(occurred_from: date | None, occurred_to: date | None) -> str:
    """O recorte de datas por extenso, inclusive quando é aberto de um lado."""
    if occurred_from is not None and occurred_to is not None:
        return f"{_date(occurred_from)} a {_date(occurred_to)}"
    if occurred_from is not None:
        return f"A partir de {_date(occurred_from)}"
    if occurred_to is not None:
        return f"Até {_date(occurred_to)}"
    return "Todo o histórico"


def _period_slug(occurred_from: date | None, occurred_to: date | None, today: date) -> str:
    """O mesmo recorte no nome do arquivo, em ISO para ordenar sozinho na pasta.

    Sem nenhuma ponta é a data de geração que nomeia o arquivo: "o extrato que
    puxei hoje". Escrever `completo` ali seria prometer uma coisa que o filtro de
    tipo ou de categoria pode estar desmentindo.
    """
    if occurred_from is not None and occurred_to is not None:
        return f"{occurred_from.isoformat()}_{occurred_to.isoformat()}"
    if occurred_from is not None:
        return f"desde-{occurred_from.isoformat()}"
    if occurred_to is not None:
        return f"ate-{occurred_to.isoformat()}"
    return today.isoformat()
