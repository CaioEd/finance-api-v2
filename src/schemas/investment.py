"""Contrato público do domínio de investimentos.

Três decisões de contrato que vêm do desenho:

- **Todo número sai como string**, como no resto da API. Dinheiro com duas
  casas; quantidade e preço unitário com até oito, normalizados (`"0.012"`, não
  `"0.01200000"`), porque `0,012 BTC` e uma ação a `R$ 48,6137` não cabem em
  `NUMERIC(14,2)` e um double na ponta do cliente perderia as casas de baixo.
- **`investment_class` não é campo de entrada.** Renda fixa ou variável é
  derivada do `type` (ver `models.investment.CLASS_OF_TYPE`); aceitá-la no
  corpo abriria caminho para um CDB cadastrado como renda variável.
- **O corpo de criação é um só para as duas metades**, com um validador que
  cobra o que cada `type` exige. Uma união discriminada exigiria do cliente
  escolher o schema antes de escolher o tipo, e o PATCH não teria como
  expressar "mude só isto" sobre ela.

`invested_amount` é sempre **em BRL** — ver o docstring da coluna. Por isso ele
é obrigatório em ativo cotado em dólar e opcional em ativo cotado em real, onde
`quantity x average_price` já é o valor pago.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    computed_field,
    field_serializer,
    model_validator,
)

from models.investment import (
    CLASS_OF_TYPE,
    INVESTMENT_NAME_MAX_LENGTH,
    MONEY_DECIMAL_PLACES,
    RATE_DECIMAL_PLACES,
    RATE_MAX_DIGITS,
    SYMBOL_MAX_LENGTH,
    UNIT_DECIMAL_PLACES,
    UNIT_MAX_DIGITS,
    InvestmentClass,
    InvestmentType,
    RateIndex,
)
from models.transaction import DESCRIPTION_MAX_LENGTH
from schemas.base import PatchIn
from schemas.transaction import Money, TransactionOut

USD_TYPES = frozenset({InvestmentType.US_STOCK, InvestmentType.CRYPTO})
"""Os tipos cotados em dólar. Decidido pelo `type`, e não pela moeda do ativo,
porque na criação o ativo ainda pode não existir no catálogo."""

SAVINGS_RATE_PERCENT = Decimal("100")
"""Poupança rende a regra do Banco Central inteira; não há taxa a contratar."""


type Quantity = Annotated[
    Decimal,
    Field(gt=0, max_digits=UNIT_MAX_DIGITS, decimal_places=UNIT_DECIMAL_PLACES),
]
type UnitPrice = Quantity

type RatePercent = Annotated[
    Decimal,
    Field(gt=0, max_digits=RATE_MAX_DIGITS, decimal_places=RATE_DECIMAL_PLACES),
]
"""Percentual do índice (`102` = 102% do CDI) ou taxa anual, se `prefixed`."""

type Symbol = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, to_upper=True, min_length=1, max_length=SYMBOL_MAX_LENGTH
    ),
]
"""Maiúsculo na entrada: o índice único compara `upper(symbol)`, e deixar a
caixa passar faria `petr4` e `PETR4` conviverem como dois ativos na mesma tela."""

type InvestmentName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=INVESTMENT_NAME_MAX_LENGTH),
]

type Description = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=DESCRIPTION_MAX_LENGTH),
]


def money_string(value: Decimal) -> str:
    return f"{value:.{MONEY_DECIMAL_PLACES}f}"


def unit_string(value: Decimal) -> str:
    """Quantidade sem zeros à toa e sem notação científica.

    `normalize()` sozinho devolve `3.2E+2` para `320.00000000`; o `f` do
    format desfaz o expoente. Sem os dois, a tela mostraria "3.2E+2 cotas".
    """
    return format(value.normalize(), "f")


# ------------------------------------------------------------------- saída


class InvestmentAssetOut(BaseModel):
    """O ativo cotado, como ele aparece dentro de uma posição.

    Aninhado em vez de um `asset_id` solto, pela mesma razão que a categoria é
    aninhada no lançamento: listar a carteira e ter de buscar cada cotação à
    parte é o N+1 saindo do servidor e virando problema de quem consome.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: InvestmentType
    symbol: str
    name: str
    currency: str
    price: Decimal | None
    change_percent: Decimal | None
    quoted_at: datetime | None
    logo_url: str | None

    @field_serializer("price")
    def _price_as_string(self, price: Decimal | None) -> str | None:
        return None if price is None else unit_string(price)

    @field_serializer("change_percent")
    def _change_as_string(self, change: Decimal | None) -> str | None:
        return None if change is None else unit_string(change)


class InvestmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: InvestmentType
    name: str
    asset: InvestmentAssetOut | None = None

    quantity: Decimal | None = None
    average_price: Decimal | None = None

    rate_index: RateIndex | None = None
    rate_percent: Decimal | None = None
    applied_on: date | None = None
    matures_on: date | None = None

    invested_amount: Decimal
    current_value: Decimal
    value_updated_at: datetime | None = None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def investment_class(self) -> InvestmentClass:
        """Derivada do tipo, nunca gravada — ver `models.investment`."""
        return CLASS_OF_TYPE[self.type]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def profit(self) -> str:
        """Quanto a posição rendeu, em BRL. Negativo quando está no prejuízo."""
        return money_string(self.current_value - self.invested_amount)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def profit_percent(self) -> str | None:
        """Rendimento sobre o investido. `None` quando não se investiu nada.

        Dividir por zero devolveria `Infinity`, que não é JSON válido — e um
        zero no lugar afirmaria "não rendeu", que é diferente de "não dá para
        calcular".
        """
        if self.invested_amount == 0:
            return None
        ratio = (self.current_value - self.invested_amount) / self.invested_amount * 100
        return f"{ratio:.{RATE_DECIMAL_PLACES}f}"

    @field_serializer("invested_amount", "current_value")
    def _money_as_string(self, value: Decimal) -> str:
        return money_string(value)

    @field_serializer("quantity", "average_price", "rate_percent")
    def _unit_as_string(self, value: Decimal | None) -> str | None:
        return None if value is None else unit_string(value)


class InvestmentPageOut(BaseModel):
    """Página de listagem: `total` é do filtro inteiro, não do que veio nesta."""

    items: list[InvestmentOut]
    total: int
    limit: int
    offset: int


class AllocationOut(BaseModel):
    """Uma fatia da carteira — por classe ou por tipo."""

    label: str
    """O valor do enum (`fixed_income`, `br_stock`); quem desenha traduz."""

    value: Decimal
    invested: Decimal
    percent: Decimal
    """Participação no patrimônio total, de 0 a 100."""

    count: int

    @field_serializer("value", "invested")
    def _money_as_string(self, value: Decimal) -> str:
        return money_string(value)

    @field_serializer("percent")
    def _percent_as_string(self, percent: Decimal) -> str:
        return f"{percent:.{RATE_DECIMAL_PLACES}f}"


class InvestmentSummaryOut(BaseModel):
    """Os números do topo da tela de investimentos, numa consulta só."""

    total_value: Decimal
    total_invested: Decimal
    profit: Decimal
    profit_percent: str | None
    positions: int
    by_class: list[AllocationOut]
    by_type: list[AllocationOut]
    value_updated_at: datetime | None
    """A correção mais recente de qualquer posição. `None` enquanto o agendador
    não rodou — é o que deixa a tela dizer "ainda não cotado" em vez de mentir."""

    @field_serializer("total_value", "total_invested", "profit")
    def _money_as_string(self, value: Decimal) -> str:
        return money_string(value)


class InvestmentMovementOut(BaseModel):
    """O que um aporte ou um provento devolve: a posição e o lançamento.

    Os dois juntos porque a chamada faz as duas coisas num commit só. Devolver
    só a posição obrigaria quem consome a recarregar `/transactions` para achar
    o lançamento que ele mesmo acabou de causar.
    """

    investment: InvestmentOut
    transaction: TransactionOut


# ------------------------------------------------------------------ entrada


class InvestmentCreateIn(BaseModel):
    """Cadastro de posição. O `type` decide quais campos são obrigatórios.

    **Não lança nada.** Cadastrar é declarar uma posição que já existe — quem
    comprou ITSA4 há dois anos não pode ver a compra cair como despesa do mês
    corrente. Aporte é `POST /investments/{id}/contributions`, e esse sim grava
    lançamento.
    """

    model_config = ConfigDict(extra="forbid")

    type: InvestmentType
    name: InvestmentName | None = None
    """Ausente em renda variável vira o nome do ativo; em renda fixa é obrigatório."""

    # --- renda variável ---
    symbol: Symbol | None = None
    quantity: Quantity | None = None
    average_price: UnitPrice | None = None
    """Preço médio pago, na **moeda do ativo** — dólar em ação americana e cripto."""

    # --- renda fixa ---
    rate_index: RateIndex | None = None
    rate_percent: RatePercent | None = None
    applied_on: date | None = None
    matures_on: date | None = None

    # --- as duas ---
    invested_amount: Money | None = None
    """Quanto se pagou, **em BRL**. Obrigatório, menos em ação brasileira, onde
    `quantity x average_price` já está em real e serve de default."""

    @model_validator(mode="after")
    def _fields_match_the_type(self) -> Self:
        if CLASS_OF_TYPE[self.type] is InvestmentClass.VARIABLE_INCOME:
            return self._validated_as_variable_income()
        return self._validated_as_fixed_income()

    def _validated_as_variable_income(self) -> Self:
        _require(
            self.type,
            symbol=self.symbol,
            quantity=self.quantity,
            average_price=self.average_price,
        )
        _reject(
            self.type,
            rate_index=self.rate_index,
            rate_percent=self.rate_percent,
            applied_on=self.applied_on,
            matures_on=self.matures_on,
        )
        if self.invested_amount is None and self.type in USD_TYPES:
            raise ValueError(
                "invested_amount é obrigatório para ativo cotado em dólar: "
                "quantity x average_price está em USD e não pode virar patrimônio em BRL"
            )
        return self

    def _validated_as_fixed_income(self) -> Self:
        _reject(
            self.type,
            symbol=self.symbol,
            quantity=self.quantity,
            average_price=self.average_price,
        )
        _require(
            self.type,
            name=self.name,
            invested_amount=self.invested_amount,
            applied_on=self.applied_on,
        )
        if self.type is InvestmentType.SAVINGS:
            # Poupança não tem taxa a contratar: a regra é a do Banco Central.
            self.rate_index = RateIndex.SAVINGS
            self.rate_percent = SAVINGS_RATE_PERCENT
        else:
            _require(self.type, rate_index=self.rate_index, rate_percent=self.rate_percent)
        if (
            self.matures_on is not None
            and self.applied_on is not None
            and self.matures_on < self.applied_on
        ):
            raise ValueError("matures_on não pode ser anterior a applied_on")
        return self


class InvestmentUpdateIn(PatchIn):
    """Todos os campos opcionais: é PATCH — ver `schemas.base.PatchIn`.

    `type` e `symbol` **não** se editam. Trocar o ativo de uma posição não é
    editá-la: o preço médio, o investido e o histórico de aportes continuariam
    apontando para a compra de outra coisa. Quem errou o ativo exclui e cadastra.
    """

    name: InvestmentName | None = None
    quantity: Quantity | None = None
    average_price: UnitPrice | None = None
    invested_amount: Money | None = None
    rate_index: RateIndex | None = None
    rate_percent: RatePercent | None = None
    applied_on: date | None = None
    matures_on: date | None = None


class ContributionIn(BaseModel):
    """Aporte: dinheiro que sai da conta e vira posição.

    Grava uma **despesa** em `transactions` e aumenta a posição no mesmo
    commit. A categoria é escolha de quem aporta — e precisa ser de despesa,
    senão o aporte entraria no saldo como receita.
    """

    model_config = ConfigDict(extra="forbid")

    amount: Money
    """Quanto saiu da conta, em BRL. É o valor do lançamento."""

    category_id: UUID
    quantity: Quantity | None = None
    """Cotas compradas. Obrigatório em renda variável, proibido em renda fixa."""

    unit_price: UnitPrice | None = None
    """Preço pago por cota, na moeda do ativo. Ausente em ativo em real é
    `amount / quantity`; em ativo em dólar é obrigatório, porque `amount` está
    em BRL e dividir daria um preço em moeda nenhuma."""

    occurred_on: date | None = None
    """Ausente é "hoje" — resolvido pelo `Clock`, no fuso da aplicação."""

    description: Description = ""


class EarningIn(BaseModel):
    """Provento: dividendo, juros sobre capital, rendimento creditado.

    Grava uma **receita** em `transactions` e **não** mexe na posição: o
    dinheiro caiu na conta, não virou mais cotas. Reinvestir é um aporte, e é
    uma segunda chamada de propósito — as duas coisas acontecem de verdade.
    """

    model_config = ConfigDict(extra="forbid")

    amount: Money
    category_id: UUID
    occurred_on: date | None = None
    description: Description = ""


# ------------------------------------------------------------------- apoio


def _require(kind: InvestmentType, **fields: Any) -> None:
    """Cobra os campos que o `type` escolhido torna obrigatórios.

    O `ValueError` vira 422 com o campo apontado, como qualquer recusa do
    Pydantic — não um 500 na violação do `NOT NULL` lá no banco.
    """
    missing = sorted(name for name, value in fields.items() if value is None)
    if missing:
        raise ValueError(f"campos obrigatórios para type={kind}: {', '.join(missing)}")


def _reject(kind: InvestmentType, **fields: Any) -> None:
    """Recusa o campo que não pertence àquele `type`.

    Aceitar e ignorar em silêncio é pior: a resposta `201` diria que a taxa de
    um CDB foi gravada numa posição de ação, onde essa coluna nem existe.
    """
    extra = sorted(name for name, value in fields.items() if value is not None)
    if extra:
        raise ValueError(f"campos não aplicáveis a type={kind}: {', '.join(extra)}")
