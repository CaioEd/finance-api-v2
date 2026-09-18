"""Regra de investimentos — sem banco e sem HTTP.

O serviço conversa com quatro `Protocol` (`InvestmentStore`, `CategoryLookup`,
`TransactionSink` e `UnitOfWork`), não com o repositório concreto nem com a
`AsyncSession`. É o que permite exercitar aqui o que de fato é regra: o preço
médio ponderado, o que cada metade da tabela aceita, e a ponte com os
lançamentos — aporte é despesa, provento é receita.

O escopo por dono em si é do repositório (`Investment.user_id == user_id`); o
que se cobre aqui é o degrau acima, que o serviço trata "não é seu" e "não
existe" pela mesma porta.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.exc import IntegrityError

from core.clock import Clock
from core.errors import (
    InvalidCategoryError,
    InvalidCategoryKindError,
    InvalidInvestmentAssetError,
    InvalidInvestmentFieldsError,
    InvestmentNotFoundError,
    UnprocessableError,
)
from models.category import Category, CategoryKind
from models.investment import (
    FK_INVESTMENT_ASSET,
    Investment,
    InvestmentAsset,
    InvestmentClass,
    InvestmentType,
)
from models.transaction import Transaction
from models.user import Role, User
from repositories.investment_repository import (
    InvestmentFilters,
    TypeTotals,
    translate_integrity_error,
)
from schemas.investment import ContributionIn, EarningIn, InvestmentCreateIn, InvestmentUpdateIn
from services.investment_service import InvestmentService

SAO_PAULO = ZoneInfo("America/Sao_Paulo")
NOON = datetime(2026, 9, 18, 15, 0, tzinfo=UTC)


def make_user(user_id: UUID | None = None) -> User:
    return User(
        id=user_id or uuid4(),
        email="ana@exemplo.com",
        username="ana",
        password_hash="$argon2id$hash-de-mentira",
        first_name="Ana",
        last_name="Ribeiro",
        role=Role.USER,
        is_active=True,
    )


def make_category(
    *,
    category_id: UUID | None = None,
    user_id: UUID | None = None,
    name: str = "Investimentos",
    kind: CategoryKind = CategoryKind.EXPENSE,
) -> Category:
    return Category(id=category_id or uuid4(), user_id=user_id, name=name, kind=kind)


def make_asset(
    symbol: str = "PETR4", kind: InvestmentType = InvestmentType.BR_STOCK
) -> InvestmentAsset:
    return InvestmentAsset(
        id=uuid4(),
        type=kind,
        symbol=symbol,
        name=symbol,
        currency="BRL" if kind is InvestmentType.BR_STOCK else "USD",
    )


def make_investment(
    *,
    user_id: UUID,
    kind: InvestmentType = InvestmentType.BR_STOCK,
    asset: InvestmentAsset | None = None,
    quantity: str | None = "100",
    average_price: str | None = "30.00",
    invested: str = "3000.00",
    current: str = "3000.00",
) -> Investment:
    return Investment(
        id=uuid4(),
        user_id=user_id,
        type=kind,
        asset=asset,
        asset_id=asset.id if asset else None,
        name=asset.symbol if asset else "CDB",
        quantity=Decimal(quantity) if quantity is not None else None,
        average_price=Decimal(average_price) if average_price is not None else None,
        invested_amount=Decimal(invested),
        current_value=Decimal(current),
    )


class FakeInvestmentStore:
    """Implementa `InvestmentStore` em memória, guardando o que recebeu."""

    def __init__(
        self,
        investments: Sequence[Investment] = (),
        assets: Sequence[InvestmentAsset] = (),
    ) -> None:
        self.investments = list(investments)
        self.assets = list(assets)
        self.added: list[Investment] = []
        self.added_assets: list[InvestmentAsset] = []
        self.deleted: list[Investment] = []
        self.locked_reads = 0
        self.updated_at: dict[InvestmentType, datetime] = {}

    async def list_investments(
        self, user_id: UUID, *, filters: InvestmentFilters, limit: int, offset: int
    ) -> Sequence[Investment]:
        return self._owned(user_id)[offset : offset + limit]

    async def count_investments(self, user_id: UUID, *, filters: InvestmentFilters) -> int:
        return len(self._owned(user_id))

    async def get_owned(
        self, investment_id: UUID, user_id: UUID, *, lock: bool = False
    ) -> Investment | None:
        """O duble impõe o mesmo escopo do repositório: nada de outro dono sai daqui."""
        if lock:
            self.locked_reads += 1
        return next(
            (
                investment
                for investment in self.investments
                if investment.id == investment_id and investment.user_id == user_id
            ),
            None,
        )

    async def totals_by_type(self, user_id: UUID) -> list[TypeTotals]:
        totals: dict[InvestmentType, TypeTotals] = {}
        for investment in self._owned(user_id):
            previous = totals.get(investment.type)
            totals[investment.type] = TypeTotals(
                type=investment.type,
                value=investment.current_value + (previous.value if previous else Decimal("0.00")),
                invested=investment.invested_amount
                + (previous.invested if previous else Decimal("0.00")),
                count=1 + (previous.count if previous else 0),
                value_updated_at=self.updated_at.get(investment.type),
            )
        return list(totals.values())

    def add(self, investment: Investment) -> None:
        self.added.append(investment)
        self.investments.append(investment)

    async def delete(self, investment: Investment) -> None:
        self.deleted.append(investment)
        self.investments.remove(investment)

    async def get_asset(self, kind: InvestmentType, symbol: str) -> InvestmentAsset | None:
        return next(
            (
                asset
                for asset in self.assets
                if asset.type == kind and asset.symbol.upper() == symbol.upper()
            ),
            None,
        )

    def add_asset(self, asset: InvestmentAsset) -> None:
        self.added_assets.append(asset)
        self.assets.append(asset)

    def _owned(self, user_id: UUID) -> list[Investment]:
        return [i for i in self.investments if i.user_id == user_id]


class FakeCategoryLookup:
    """Implementa `CategoryLookup` com a mesma regra de visibilidade do repositório."""

    def __init__(self, categories: Sequence[Category] = ()) -> None:
        self.categories = list(categories)

    async def get_visible(self, category_id: UUID, user_id: UUID) -> Category | None:
        return next(
            (
                category
                for category in self.categories
                if category.id == category_id
                and (category.user_id is None or category.user_id == user_id)
            ),
            None,
        )


class FakeTransactionSink:
    """Implementa `TransactionSink`: guarda os lançamentos que o serviço criou."""

    def __init__(self) -> None:
        self.added: list[Transaction] = []

    def add(self, transaction: Transaction) -> None:
        self.added.append(transaction)


class FakeUnitOfWork:
    """Transação de mentira: conta os commits e sabe falhar sob comando."""

    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.fails_with: Exception | None = None

    async def commit(self) -> None:
        if self.fails_with is not None:
            raise self.fails_with
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def build(
    *,
    investments: FakeInvestmentStore | None = None,
    categories: FakeCategoryLookup | None = None,
    transactions: FakeTransactionSink | None = None,
    unit_of_work: FakeUnitOfWork | None = None,
) -> InvestmentService:
    return InvestmentService(
        unit_of_work=unit_of_work or FakeUnitOfWork(),
        investments=investments or FakeInvestmentStore(),
        categories=categories or FakeCategoryLookup(),
        transactions=transactions or FakeTransactionSink(),
        clock=Clock(tz=SAO_PAULO, instant=lambda: NOON),
    )


# ------------------------------------------------------------------ cadastro


async def test_creating_a_variable_income_position_creates_the_asset_once() -> None:
    """O catálogo é global: o segundo usuário do mesmo papel reusa a linha."""
    store = FakeInvestmentStore()
    service = build(investments=store)
    ana, bruno = make_user(), make_user()
    body = InvestmentCreateIn(
        type=InvestmentType.BR_STOCK,
        symbol="PETR4",
        quantity=Decimal("100"),
        average_price=Decimal("30.00"),
    )

    first = await service.create(ana, body)
    second = await service.create(bruno, body)

    assert len(store.added_assets) == 1, "o catálogo duplicou o ativo"
    assert first.asset_id == second.asset_id


async def test_a_position_in_reais_derives_what_was_invested() -> None:
    service = build()

    investment = await service.create(
        make_user(),
        InvestmentCreateIn(
            type=InvestmentType.BR_STOCK,
            symbol="PETR4",
            quantity=Decimal("100"),
            average_price=Decimal("30.00"),
        ),
    )

    assert investment.invested_amount == Decimal("3000.00")
    # Vale o que se pagou até o agendador cotar pela primeira vez.
    assert investment.current_value == Decimal("3000.00")


async def test_a_position_in_dollars_keeps_what_was_paid_in_reais() -> None:
    """`quantity x average_price` está em USD e não é patrimônio em BRL."""
    service = build()

    investment = await service.create(
        make_user(),
        InvestmentCreateIn(
            type=InvestmentType.US_STOCK,
            symbol="AAPL",
            quantity=Decimal("5"),
            average_price=Decimal("200.00"),
            invested_amount=Decimal("5200.00"),
        ),
    )

    assert investment.invested_amount == Decimal("5200.00")
    assert investment.asset is not None
    assert investment.asset.currency == "USD"


async def test_creating_a_fixed_income_position_has_no_asset() -> None:
    service = build()

    investment = await service.create(
        make_user(),
        InvestmentCreateIn(
            type=InvestmentType.CDB,
            name="CDB Liquidez",
            invested_amount=Decimal("5000.00"),
            rate_index="cdi",
            rate_percent=Decimal("102"),
            applied_on=date(2026, 1, 15),
        ),
    )

    assert investment.asset is None
    assert investment.investment_class is InvestmentClass.FIXED_INCOME
    assert investment.rate_percent == Decimal("102")


# --------------------------------------------------------------------- aporte


async def test_a_contribution_averages_the_price_by_weight() -> None:
    """100 a 30,00 mais 100 a 40,00 dá 200 a 35,00 — não 35 por média simples de duas compras."""
    user = make_user()
    asset = make_asset()
    investment = make_investment(user_id=user.id, asset=asset)
    category = make_category(user_id=user.id)
    store = FakeInvestmentStore([investment], [asset])
    sink = FakeTransactionSink()
    service = build(investments=store, categories=FakeCategoryLookup([category]), transactions=sink)

    movement = await service.contribute(
        user,
        investment.id,
        ContributionIn(
            amount=Decimal("4000.00"),
            category_id=category.id,
            quantity=Decimal("100"),
            unit_price=Decimal("40.00"),
        ),
    )

    assert movement.investment.quantity == Decimal("200")
    assert movement.investment.average_price == Decimal("35.00000000")
    assert movement.investment.invested_amount == Decimal("7000.00")
    assert movement.investment.current_value == Decimal("7000.00")
    assert sink.added == [movement.transaction]


async def test_a_contribution_locks_the_position() -> None:
    """Dois aportes simultâneos sem trava calculariam a média sobre a mesma quantidade."""
    user = make_user()
    asset = make_asset()
    investment = make_investment(user_id=user.id, asset=asset)
    category = make_category(user_id=user.id)
    store = FakeInvestmentStore([investment], [asset])
    service = build(investments=store, categories=FakeCategoryLookup([category]))

    await service.contribute(
        user,
        investment.id,
        ContributionIn(amount=Decimal("100.00"), category_id=category.id, quantity=Decimal("2")),
    )

    assert store.locked_reads == 1


async def test_a_contribution_without_a_unit_price_derives_it_in_reais() -> None:
    user = make_user()
    asset = make_asset()
    investment = make_investment(
        user_id=user.id, asset=asset, quantity="10", average_price="10.00", invested="100.00"
    )
    category = make_category(user_id=user.id)
    service = build(
        investments=FakeInvestmentStore([investment], [asset]),
        categories=FakeCategoryLookup([category]),
    )

    movement = await service.contribute(
        user,
        investment.id,
        ContributionIn(amount=Decimal("300.00"), category_id=category.id, quantity=Decimal("10")),
    )

    # 10 a 10,00 mais 10 a 30,00 = 20 a 20,00.
    assert movement.investment.average_price == Decimal("20.00000000")


async def test_a_contribution_in_dollars_demands_the_unit_price() -> None:
    """`amount` está em BRL: dividi-lo pela quantidade daria preço em moeda nenhuma."""
    user = make_user()
    asset = make_asset("AAPL", InvestmentType.US_STOCK)
    investment = make_investment(user_id=user.id, kind=InvestmentType.US_STOCK, asset=asset)
    category = make_category(user_id=user.id)
    service = build(
        investments=FakeInvestmentStore([investment], [asset]),
        categories=FakeCategoryLookup([category]),
    )

    with pytest.raises(InvalidInvestmentFieldsError):
        await service.contribute(
            user,
            investment.id,
            ContributionIn(
                amount=Decimal("500.00"), category_id=category.id, quantity=Decimal("2")
            ),
        )


async def test_a_contribution_needs_an_expense_category() -> None:
    user = make_user()
    asset = make_asset()
    investment = make_investment(user_id=user.id, asset=asset)
    income = make_category(user_id=user.id, name="Salário", kind=CategoryKind.INCOME)
    service = build(
        investments=FakeInvestmentStore([investment], [asset]),
        categories=FakeCategoryLookup([income]),
    )

    with pytest.raises(InvalidCategoryKindError):
        await service.contribute(
            user,
            investment.id,
            ContributionIn(amount=Decimal("100.00"), category_id=income.id, quantity=Decimal("1")),
        )


async def test_a_category_of_someone_else_is_refused_like_one_that_does_not_exist() -> None:
    """A recusa é a mesma nos dois casos: a resposta não confirma a conta alheia."""
    user = make_user()
    asset = make_asset()
    investment = make_investment(user_id=user.id, asset=asset)
    alheia = make_category(user_id=uuid4())
    service = build(
        investments=FakeInvestmentStore([investment], [asset]),
        categories=FakeCategoryLookup([alheia]),
    )

    with pytest.raises(InvalidCategoryError):
        await service.contribute(
            user,
            investment.id,
            ContributionIn(amount=Decimal("10.00"), category_id=alheia.id, quantity=Decimal("1")),
        )


async def test_a_contribution_to_fixed_income_refuses_a_quantity() -> None:
    user = make_user()
    cdb = make_investment(
        user_id=user.id, kind=InvestmentType.CDB, quantity=None, average_price=None
    )
    category = make_category(user_id=user.id)
    service = build(
        investments=FakeInvestmentStore([cdb]), categories=FakeCategoryLookup([category])
    )

    with pytest.raises(InvalidInvestmentFieldsError):
        await service.contribute(
            user,
            cdb.id,
            ContributionIn(
                amount=Decimal("100.00"), category_id=category.id, quantity=Decimal("3")
            ),
        )


# ------------------------------------------------------------------- provento


async def test_an_earning_does_not_move_the_position() -> None:
    """Dividendo cai na conta, não vira cota — reinvestir é um aporte à parte."""
    user = make_user()
    asset = make_asset()
    investment = make_investment(user_id=user.id, asset=asset)
    category = make_category(user_id=user.id, name="Dividendos", kind=CategoryKind.INCOME)
    sink = FakeTransactionSink()
    service = build(
        investments=FakeInvestmentStore([investment], [asset]),
        categories=FakeCategoryLookup([category]),
        transactions=sink,
    )

    movement = await service.register_earning(
        user, investment.id, EarningIn(amount=Decimal("120.00"), category_id=category.id)
    )

    assert movement.investment.quantity == Decimal("100")
    assert movement.investment.invested_amount == Decimal("3000.00")
    assert movement.investment.current_value == Decimal("3000.00")
    assert sink.added[0].amount == Decimal("120.00")
    assert sink.added[0].investment_id == investment.id


async def test_an_earning_uses_todays_date_from_the_clock() -> None:
    """ "Hoje" vem do `Clock`, no fuso da aplicação — nunca de `date.today()`."""
    user = make_user()
    asset = make_asset()
    investment = make_investment(user_id=user.id, asset=asset)
    category = make_category(user_id=user.id, kind=CategoryKind.INCOME)
    sink = FakeTransactionSink()
    service = build(
        investments=FakeInvestmentStore([investment], [asset]),
        categories=FakeCategoryLookup([category]),
        transactions=sink,
    )

    await service.register_earning(
        user, investment.id, EarningIn(amount=Decimal("10.00"), category_id=category.id)
    )

    assert sink.added[0].occurred_on == date(2026, 9, 18)


# ------------------------------------------------------------ edição e escopo


async def test_a_patch_refuses_a_field_of_the_other_half() -> None:
    user = make_user()
    cdb = make_investment(
        user_id=user.id, kind=InvestmentType.CDB, quantity=None, average_price=None
    )
    service = build(investments=FakeInvestmentStore([cdb]))

    with pytest.raises(InvalidInvestmentFieldsError) as refusal:
        await service.update(user, cdb.id, InvestmentUpdateIn(quantity=Decimal("10")))

    assert refusal.value.details[0]["field"] == "quantity"


async def test_a_patch_changes_only_what_was_sent() -> None:
    user = make_user()
    asset = make_asset()
    investment = make_investment(user_id=user.id, asset=asset)
    service = build(investments=FakeInvestmentStore([investment], [asset]))

    updated = await service.update(user, investment.id, InvestmentUpdateIn(name="Petrobras PN"))

    assert updated.name == "Petrobras PN"
    assert updated.quantity == Decimal("100")


async def test_the_position_of_someone_else_is_not_found() -> None:
    """ "Não é seu" e "não existe" saem pela mesma porta."""
    owner = make_user()
    intruder = make_user()
    investment = make_investment(user_id=owner.id, asset=make_asset())
    service = build(investments=FakeInvestmentStore([investment]))

    with pytest.raises(InvestmentNotFoundError):
        await service.get(intruder, investment.id)
    with pytest.raises(InvestmentNotFoundError):
        await service.get(owner, uuid4())


# --------------------------------------------------------------------- resumo


async def test_the_summary_groups_by_class_and_by_type() -> None:
    user = make_user()
    service = build(
        investments=FakeInvestmentStore(
            [
                make_investment(
                    user_id=user.id, asset=make_asset(), invested="3000.00", current="3000.00"
                ),
                make_investment(
                    user_id=user.id,
                    kind=InvestmentType.CDB,
                    quantity=None,
                    average_price=None,
                    invested="5000.00",
                    current="5000.00",
                ),
            ]
        )
    )

    summary = await service.summary(user)

    assert summary.total_value == Decimal("8000.00")
    assert {share.label: share.percent for share in summary.by_class} == {
        "fixed_income": Decimal("62.5"),
        "variable_income": Decimal("37.5"),
    }
    assert [share.label for share in summary.by_type] == ["cdb", "br_stock"]


async def test_an_empty_portfolio_has_no_percentage() -> None:
    """Dividir por zero não é um número, e um zero diria "não rendeu"."""
    service = build()

    summary = await service.summary(make_user())

    assert summary.total_value == Decimal("0.00")
    assert summary.profit_percent is None
    assert summary.by_class == []


# ------------------------------------------------------ as pontas do commit


def integrity_error(constraint: str) -> IntegrityError:
    """Reproduz o erro que o Postgres devolve ao violar uma constraint."""
    orig = Exception(f'violates foreign key constraint "{constraint}"')
    return IntegrityError("INSERT INTO investments ...", {}, orig)


def test_a_vanished_asset_becomes_a_legible_refusal() -> None:
    """O ativo sumiu entre a resolução e o INSERT: quem decide é o banco.

    O serviço confere antes de gravar, mas um SELECT prévio sempre terá essa
    janela — a checagem existe para a recusa ser legível, não para ser autoridade.
    """
    refusal = translate_integrity_error(integrity_error(FK_INVESTMENT_ASSET))

    assert isinstance(refusal, InvalidInvestmentAssetError)


def test_any_other_constraint_stays_generic() -> None:
    """Traduzir o que não se reconhece seria afirmar uma causa que não se apurou."""
    refusal = translate_integrity_error(integrity_error("ck_investments_qualquer_outra"))

    assert isinstance(refusal, UnprocessableError)
    assert not isinstance(refusal, InvalidInvestmentAssetError)


async def test_a_failed_commit_rolls_back_before_raising() -> None:
    """Sem o rollback, a sessão fica inutilizável para a requisição seguinte."""
    user = make_user()
    asset = make_asset()
    investment = make_investment(user_id=user.id, asset=asset)
    unit_of_work = FakeUnitOfWork()
    unit_of_work.fails_with = integrity_error(FK_INVESTMENT_ASSET)
    service = build(
        investments=FakeInvestmentStore([investment], [asset]), unit_of_work=unit_of_work
    )

    with pytest.raises(InvalidInvestmentAssetError):
        await service.update(user, investment.id, InvestmentUpdateIn(name="Novo"))

    assert unit_of_work.rollbacks == 1


async def test_a_contribution_to_variable_income_demands_a_quantity() -> None:
    """Sem quantidade não há o que somar à posição, e o preço médio ficaria mentindo."""
    user = make_user()
    asset = make_asset()
    investment = make_investment(user_id=user.id, asset=asset)
    category = make_category(user_id=user.id)
    service = build(
        investments=FakeInvestmentStore([investment], [asset]),
        categories=FakeCategoryLookup([category]),
    )

    with pytest.raises(InvalidInvestmentFieldsError) as refusal:
        await service.contribute(
            user, investment.id, ContributionIn(amount=Decimal("100.00"), category_id=category.id)
        )

    assert refusal.value.details[0]["field"] == "quantity"


async def test_the_summary_reports_the_most_recent_correction() -> None:
    """`value_updated_at` é o que deixa a tela dizer "ainda não cotado" sem mentir."""
    user = make_user()
    older = make_investment(user_id=user.id, asset=make_asset())
    newer = make_investment(
        user_id=user.id, kind=InvestmentType.CDB, quantity=None, average_price=None
    )
    store = FakeInvestmentStore([older, newer])
    store.updated_at = {older.type: datetime(2026, 9, 1, tzinfo=UTC), newer.type: NOON}
    service = build(investments=store)

    summary = await service.summary(user)

    assert summary.value_updated_at == NOON
