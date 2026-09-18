"""Regra de investimentos: cadastrar posição, aportar, registrar provento.

Python puro, como os demais serviços: nada aqui conhece HTTP nem FastAPI. As
dependências são `Protocol` — o serviço declara de que operações precisa, e
tanto o repositório real quanto um duble as satisfazem.

Quatro regras vivem aqui, e só aqui:

1. **O `type` decide a metade da tabela.** Campo da metade errada é recusado em
   vez de gravado em silêncio (`InvalidInvestmentFieldsError`).
2. **Cadastrar não lança nada; aportar lança.** A compra de dois anos atrás não
   pode cair como despesa do mês corrente.
3. **Aporte é despesa e provento é receita.** O `kind` vem da categoria, então a
   categoria escolhida precisa ser do tipo certo — senão o dinheiro investido
   entraria no saldo com o sinal trocado.
4. **Preço médio é média ponderada**, recalculada a cada aporte.

A cotação **não** vive aqui: `current_value` nasce igual ao investido, e quem o
corrige é o agendador (`jobs.investment_quotes`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from core.clock import Clock
from core.errors import (
    InvalidCategoryError,
    InvalidCategoryKindError,
    InvalidInvestmentFieldsError,
    InvestmentNotFoundError,
)
from models.category import Category, CategoryKind
from models.investment import (
    CLASS_OF_TYPE,
    MONEY_DECIMAL_PLACES,
    UNIT_DECIMAL_PLACES,
    Investment,
    InvestmentAsset,
    InvestmentClass,
    InvestmentType,
)
from models.transaction import Transaction
from models.user import User
from repositories.investment_repository import (
    InvestmentFilters,
    TypeTotals,
    translate_integrity_error,
)
from schemas.investment import (
    USD_TYPES,
    ContributionIn,
    EarningIn,
    InvestmentCreateIn,
    InvestmentUpdateIn,
)

BRL = "BRL"
USD = "USD"

ZERO = Decimal("0.00")

MONEY_QUANTUM = Decimal(1).scaleb(-MONEY_DECIMAL_PLACES)
UNIT_QUANTUM = Decimal(1).scaleb(-UNIT_DECIMAL_PLACES)

VARIABLE_INCOME_FIELDS = frozenset({"quantity", "average_price"})
FIXED_INCOME_FIELDS = frozenset({"rate_index", "rate_percent", "applied_on", "matures_on"})


def currency_of(kind: InvestmentType) -> str:
    """A moeda do tipo, e não a consultada no provedor: isso não muda com o
    provedor da vez."""
    return USD if kind in USD_TYPES else BRL


def as_money(value: Decimal) -> Decimal:
    """Meio para cima, explícito: o default do `Decimal` é banqueiro
    (`ROUND_HALF_EVEN`), e dinheiro assim não bate com o extrato de ninguém."""
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def as_unit(value: Decimal) -> Decimal:
    return value.quantize(UNIT_QUANTUM, rounding=ROUND_HALF_UP)


class InvestmentStore(Protocol):
    """O que este serviço precisa de um repositório de investimentos."""

    async def list_investments(
        self, user_id: UUID, *, filters: InvestmentFilters, limit: int, offset: int
    ) -> Sequence[Investment]: ...

    async def count_investments(self, user_id: UUID, *, filters: InvestmentFilters) -> int: ...

    async def get_owned(
        self, investment_id: UUID, user_id: UUID, *, lock: bool = False
    ) -> Investment | None: ...

    async def totals_by_type(self, user_id: UUID) -> list[TypeTotals]: ...

    def add(self, investment: Investment) -> None: ...

    async def delete(self, investment: Investment) -> None: ...

    async def get_asset(self, kind: InvestmentType, symbol: str) -> InvestmentAsset | None: ...

    def add_asset(self, asset: InvestmentAsset) -> None: ...


class CategoryLookup(Protocol):
    """Estreito de propósito, como em `services.transaction_service`: o serviço
    não lista nem altera categoria."""

    async def get_visible(self, category_id: UUID, user_id: UUID) -> Category | None: ...


class TransactionSink(Protocol):
    """Aporte e provento só *criam* lançamento; editá-lo é de `transaction_service`."""

    def add(self, transaction: Transaction) -> None: ...


class UnitOfWork(Protocol):
    """A parte da sessão que é assunto do serviço: quando o trabalho fecha."""

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


@dataclass(frozen=True, slots=True)
class InvestmentPage:
    items: Sequence[Investment]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class Slice:
    """Fatia da carteira antes de saber a participação dela. Somável para que
    agrupar por classe não custe uma segunda consulta sobre as mesmas linhas."""

    value: Decimal = ZERO
    invested: Decimal = ZERO
    count: int = 0

    def __add__(self, other: Slice) -> Slice:
        return Slice(
            value=self.value + other.value,
            invested=self.invested + other.invested,
            count=self.count + other.count,
        )


@dataclass(frozen=True, slots=True)
class Allocation:
    """Uma fatia da carteira, já com a participação calculada."""

    label: str
    value: Decimal
    invested: Decimal
    percent: Decimal
    count: int


@dataclass(frozen=True, slots=True)
class PortfolioSummary:
    total_value: Decimal
    total_invested: Decimal
    positions: int
    by_class: list[Allocation]
    by_type: list[Allocation]
    value_updated_at: datetime | None

    @property
    def profit(self) -> Decimal:
        return self.total_value - self.total_invested

    @property
    def profit_percent(self) -> str | None:
        # `None` quando nada foi investido: dividir por zero não é um número.
        if self.total_invested == 0:
            return None
        return f"{self.profit / self.total_invested * 100:.4f}"


@dataclass(frozen=True, slots=True)
class Movement:
    """O par que um aporte ou provento produz, num commit só."""

    investment: Investment
    transaction: Transaction


class InvestmentService:
    def __init__(
        self,
        *,
        unit_of_work: UnitOfWork,
        investments: InvestmentStore,
        categories: CategoryLookup,
        transactions: TransactionSink,
        clock: Clock,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._investments = investments
        self._categories = categories
        self._transactions = transactions
        self._clock = clock

    # ---------------------------------------------------------------- leitura

    async def list_investments(
        self, user: User, *, filters: InvestmentFilters, limit: int, offset: int
    ) -> InvestmentPage:
        items = await self._investments.list_investments(
            user.id, filters=filters, limit=limit, offset=offset
        )
        total = await self._investments.count_investments(user.id, filters=filters)
        return InvestmentPage(items=items, total=total, limit=limit, offset=offset)

    async def get(self, user: User, investment_id: UUID) -> Investment:
        return await self._owned_or_fail(user, investment_id)

    async def summary(self, user: User) -> PortfolioSummary:
        """Os números do topo da tela, de uma varredura só.

        A classe sai da soma dos tipos dela, em Python: uma segunda consulta
        agrupada por classe poderia observar um estado diferente se o agendador
        gravasse entre as duas.
        """
        rows = await self._investments.totals_by_type(user.id)
        total_value = sum((row.value for row in rows), ZERO)
        total_invested = sum((row.invested for row in rows), ZERO)

        by_class: dict[InvestmentClass, Slice] = {}
        for row in rows:
            family = CLASS_OF_TYPE[row.type]
            by_class[family] = by_class.get(family, Slice()) + Slice(
                value=row.value, invested=row.invested, count=row.count
            )

        updated: datetime | None = None
        for row in rows:
            updated = _latest(updated, row.value_updated_at)

        return PortfolioSummary(
            total_value=total_value,
            total_invested=total_invested,
            positions=sum(row.count for row in rows),
            by_class=[
                _allocation(family.value, share, total_value)
                for family, share in sorted(by_class.items(), key=lambda item: item[0].value)
            ],
            by_type=[
                _allocation(
                    row.type.value,
                    Slice(value=row.value, invested=row.invested, count=row.count),
                    total_value,
                )
                for row in sorted(rows, key=lambda row: row.value, reverse=True)
            ],
            value_updated_at=updated,
        )

    # ---------------------------------------------------------------- escrita

    async def create(self, user: User, data: InvestmentCreateIn) -> Investment:
        """Cadastra a posição. Não grava lançamento — ver o docstring do módulo."""
        investment = Investment(user_id=user.id, type=data.type, name=data.name or "")
        if CLASS_OF_TYPE[data.type] is InvestmentClass.VARIABLE_INCOME:
            await self._fill_variable_income(investment, data)
        else:
            _fill_fixed_income(investment, data)
        # O valor de hoje é o que se pagou até a primeira cotação chegar.
        investment.current_value = investment.invested_amount
        self._investments.add(investment)
        await self._commit()
        return investment

    async def _fill_variable_income(self, investment: Investment, data: InvestmentCreateIn) -> None:
        # O schema já garantiu que os três existem para esta metade.
        assert data.symbol is not None and data.quantity is not None
        assert data.average_price is not None

        asset = await self._asset_for(data.type, data.symbol, data.name)
        investment.asset = asset
        investment.asset_id = asset.id
        investment.name = data.name or asset.name
        investment.quantity = data.quantity
        investment.average_price = data.average_price
        # Em ativo cotado em real o produto já está em BRL e serve de default;
        # em dólar o schema exigiu o valor, porque converter uma compra passada
        # com a taxa de hoje inventaria um número.
        investment.invested_amount = as_money(
            data.invested_amount
            if data.invested_amount is not None
            else data.quantity * data.average_price
        )

    async def _asset_for(
        self, kind: InvestmentType, symbol: str, name: str | None
    ) -> InvestmentAsset:
        """O ativo do catálogo global, criado na primeira vez que alguém o
        cadastra. Sem cotação: quem cota é o agendador."""
        existing = await self._investments.get_asset(kind, symbol)
        if existing is not None:
            return existing
        asset = InvestmentAsset(
            # Id no Python: a posição aponta para o ativo antes do flush, como
            # o lançamento aponta para a recorrência em `transaction_service`.
            id=uuid4(),
            type=kind,
            symbol=symbol,
            name=name or symbol,
            currency=currency_of(kind),
        )
        self._investments.add_asset(asset)
        return asset

    async def update(self, user: User, investment_id: UUID, data: InvestmentUpdateIn) -> Investment:
        investment = await self._owned_or_fail(user, investment_id)
        changes = data.changes()
        _reject_fields_of_the_other_half(investment, changes)

        for field, value in changes.items():
            setattr(investment, field, value)
        # Sem arredondar: `Money` ja recusa mais de duas casas na entrada, e
        # arredondar aqui seria uma segunda regra para a mesma coisa.
        await self._commit()
        return investment

    async def delete(self, user: User, investment_id: UUID) -> None:
        """Exclui a posição. Os lançamentos ficam, com `investment_id` nulo: o
        aporte saiu da conta de verdade, e apagá-lo falsificaria o saldo do mês."""
        investment = await self._owned_or_fail(user, investment_id)
        await self._investments.delete(investment)
        await self._commit()

    async def contribute(self, user: User, investment_id: UUID, data: ContributionIn) -> Movement:
        """Aporta: grava a despesa e aumenta a posição no mesmo commit.

        A posição é travada porque o preço médio é lido, recalculado e gravado:
        sem trava, dois aportes simultâneos calculariam a média sobre a mesma
        quantidade antiga.
        """
        investment = await self._owned_or_fail(user, investment_id, lock=True)
        category = await self._category_of_kind(user, data.category_id, CategoryKind.EXPENSE)

        if investment.is_variable_income:
            _apply_purchase(investment, data)
        elif data.quantity is not None or data.unit_price is not None:
            raise InvalidInvestmentFieldsError(
                sorted(
                    field
                    for field, value in (
                        ("quantity", data.quantity),
                        ("unit_price", data.unit_price),
                    )
                    if value is not None
                )
            )

        investment.invested_amount = as_money(investment.invested_amount + data.amount)
        # O dinheiro acabou de entrar na posição; o agendador corrige na rodada
        # seguinte, quando souber quanto a cota vale hoje.
        investment.current_value = as_money(investment.current_value + data.amount)

        transaction = self._lancamento(
            investment, category, data.amount, data.occurred_on, data.description, "Aporte em"
        )
        await self._commit()
        return Movement(investment=investment, transaction=transaction)

    async def register_earning(self, user: User, investment_id: UUID, data: EarningIn) -> Movement:
        """Registra o provento: grava a receita e **não** mexe na posição.

        Dividendo cai na conta, não vira cota; reinvestir é um aporte à parte.
        """
        investment = await self._owned_or_fail(user, investment_id)
        category = await self._category_of_kind(user, data.category_id, CategoryKind.INCOME)
        transaction = self._lancamento(
            investment, category, data.amount, data.occurred_on, data.description, "Provento de"
        )
        await self._commit()
        return Movement(investment=investment, transaction=transaction)

    def _lancamento(
        self,
        investment: Investment,
        category: Category,
        amount: Decimal,
        occurred_on: date | None,
        description: str,
        prefix: str,
    ) -> Transaction:
        """O lançamento comum que um movimento produz. `investment_id` é a única
        marca que ele carrega: saldo, extrato e PDF seguem somando uma tabela só."""
        transaction = Transaction(
            user_id=investment.user_id,
            category_id=category.id,
            category=category,
            amount=amount,
            occurred_on=occurred_on or self._clock.today(),
            description=description or f"{prefix} {investment.name}"[:200],
            investment_id=investment.id,
        )
        self._transactions.add(transaction)
        return transaction

    # ------------------------------------------------------------------ apoio

    async def _owned_or_fail(
        self, user: User, investment_id: UUID, *, lock: bool = False
    ) -> Investment:
        investment = await self._investments.get_owned(investment_id, user.id, lock=lock)
        if investment is None:
            raise InvestmentNotFoundError()
        return investment

    async def _category_of_kind(
        self, user: User, category_id: UUID, expected: CategoryKind
    ) -> Category:
        """A categoria que este usuário pode usar, e do tipo que a rota exige.

        `get_visible` devolve `None` tanto para a inexistente quanto para a de
        outra pessoa, e a recusa é a mesma: a resposta não confirma o que existe
        na conta alheia.
        """
        category = await self._categories.get_visible(category_id, user.id)
        if category is None:
            raise InvalidCategoryError()
        if category.kind is not expected:
            raise InvalidCategoryKindError()
        return category

    async def _commit(self) -> None:
        try:
            await self._unit_of_work.commit()
        except IntegrityError as exc:
            await self._unit_of_work.rollback()
            raise translate_integrity_error(exc) from exc


# ------------------------------------------------------------------ funções


def _fill_fixed_income(investment: Investment, data: InvestmentCreateIn) -> None:
    # O schema já garantiu que estes existem para esta metade.
    assert data.invested_amount is not None
    # Explícito, e não deixado por preencher: `asset` é `lazy="raise"`, e ler um
    # atributo nunca carregado estoura na serialização da resposta. Atribuir
    # `None` marca a relação como carregada e vazia, que é o que renda fixa é.
    investment.asset = None
    investment.rate_index = data.rate_index
    investment.rate_percent = data.rate_percent
    investment.applied_on = data.applied_on
    investment.matures_on = data.matures_on
    investment.invested_amount = as_money(data.invested_amount)


def _apply_purchase(investment: Investment, data: ContributionIn) -> None:
    """Soma as cotas e recalcula o preço médio ponderado.

    O preço unitário fica na moeda do ativo: em real pode sair de
    `amount / quantity`; em dólar é exigido, porque `amount` está em BRL.
    """
    if data.quantity is None:
        raise InvalidInvestmentFieldsError(["quantity"])

    unit_price = data.unit_price
    if unit_price is None:
        if investment.type in USD_TYPES:
            raise InvalidInvestmentFieldsError(["unit_price"])
        unit_price = as_unit(data.amount / data.quantity)

    previous_quantity = investment.quantity or Decimal(0)
    previous_average = investment.average_price or Decimal(0)
    quantity = previous_quantity + data.quantity
    investment.average_price = as_unit(
        (previous_quantity * previous_average + data.quantity * unit_price) / quantity
    )
    investment.quantity = quantity


def _reject_fields_of_the_other_half(investment: Investment, changes: dict[str, object]) -> None:
    """Recusa o PATCH que tenta gravar campo da metade oposta da tabela."""
    forbidden = FIXED_INCOME_FIELDS if investment.is_variable_income else VARIABLE_INCOME_FIELDS
    offending = sorted(forbidden & changes.keys())
    if offending:
        raise InvalidInvestmentFieldsError(offending)


def _latest(first: datetime | None, second: datetime | None) -> datetime | None:
    known = [moment for moment in (first, second) if moment is not None]
    return max(known) if known else None


def _allocation(label: str, share: Slice, total_value: Decimal) -> Allocation:
    """A fatia, com participação zero — e não `None` — quando a carteira inteira
    vale zero: a alocação é desenhada como barra, e falta de percentual a quebra."""
    percent = Decimal(0) if total_value == 0 else share.value / total_value * 100
    return Allocation(
        label=label,
        value=share.value,
        invested=share.invested,
        percent=percent,
        count=share.count,
    )
