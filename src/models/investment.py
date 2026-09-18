"""Models de investimento: o ativo cotado e a posição de cada usuário.

São duas tabelas porque são dois fatos com donos diferentes:

- **`investment_assets`** é catálogo **global**. A cotação da PETR4 é pública e
  vale o mesmo para todo mundo; guardá-la por usuário faria o agendador bater
  na BRAPI uma vez por dono da mesma ação. Com a tabela compartilhada, cem
  usuários com PETR4 custam **uma** requisição por rodada — e é isso que torna
  o plano gratuito viável (BRAPI grátis: 1 ativo por requisição, 20 por minuto;
  Twelve Data grátis: 8 créditos por minuto, 800 por dia).
- **`investments`** é a posição, e essa é de quem a cadastrou. O escopo por
  dono é imposto no repositório, como no resto do projeto.

Renda fixa não tem ativo: CDB, LCI, Tesouro e poupança não têm símbolo para
cotar. O que os avalia é o índice (`rate_index`) e a taxa contratada
(`rate_percent`) acruados desde `applied_on` — por isso `asset_id` é nulável.

**`current_value` é sempre em BRL**, inclusive para ação americana e cripto: é
o número que soma no patrimônio, e um total que mistura moedas não soma. A
moeda de origem fica em `investment_assets.currency`, e a conversão é do
agendador, que lê `USD/BRL` da Twelve Data na mesma rodada.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base, TimestampMixin

# O dinheiro do domínio tem a mesma forma do resto da API — ver `models.transaction`.
MONEY_MAX_DIGITS = 14
MONEY_DECIMAL_PLACES = 2

# Quantidade e preço unitário não são dinheiro: 0,00012345 BTC e uma ação a
# R$ 48,6137 precisam de casas que `NUMERIC(14,2)` não tem.
UNIT_MAX_DIGITS = 24
UNIT_DECIMAL_PLACES = 8

RATE_MAX_DIGITS = 9
RATE_DECIMAL_PLACES = 4

SYMBOL_MAX_LENGTH = 24
ASSET_NAME_MAX_LENGTH = 120
INVESTMENT_NAME_MAX_LENGTH = 120
CURRENCY_LENGTH = 3
LOGO_URL_MAX_LENGTH = 500

# Nome que `NAMING_CONVENTION` dá à FK de `asset_id`, lido por quem traduz a violação.
FK_INVESTMENT_ASSET = "fk_investments_asset_id_investment_assets"


class InvestmentClass(StrEnum):
    """As duas famílias do módulo. Derivada do tipo, nunca gravada.

    Guardá-la como coluna criaria uma segunda fonte para o mesmo fato — a
    mesma decisão que `models.transaction` toma sobre `kind`: um CDB marcado
    como renda variável seria irrecuperável, e a alocação da carteira
    discordaria da lista que o usuário vê.
    """

    FIXED_INCOME = "fixed_income"
    VARIABLE_INCOME = "variable_income"


class InvestmentType(StrEnum):
    """O que o usuário escolhe ao cadastrar. É ele que decide todo o resto."""

    # Renda variável — cotada por símbolo numa API externa.
    BR_STOCK = "br_stock"
    US_STOCK = "us_stock"
    CRYPTO = "crypto"
    # Renda fixa — avaliada por índice e taxa, sem símbolo.
    CDB = "cdb"
    LCI = "lci"
    TREASURY = "treasury"
    SAVINGS = "savings"


# Tipo novo sem entrada aqui estoura no `KeyError`, que é melhor do que cair em
# silêncio numa das duas metades.
CLASS_OF_TYPE: dict[InvestmentType, InvestmentClass] = {
    InvestmentType.BR_STOCK: InvestmentClass.VARIABLE_INCOME,
    InvestmentType.US_STOCK: InvestmentClass.VARIABLE_INCOME,
    InvestmentType.CRYPTO: InvestmentClass.VARIABLE_INCOME,
    InvestmentType.CDB: InvestmentClass.FIXED_INCOME,
    InvestmentType.LCI: InvestmentClass.FIXED_INCOME,
    InvestmentType.TREASURY: InvestmentClass.FIXED_INCOME,
    InvestmentType.SAVINGS: InvestmentClass.FIXED_INCOME,
}

VARIABLE_INCOME_TYPES = frozenset(
    kind for kind, family in CLASS_OF_TYPE.items() if family is InvestmentClass.VARIABLE_INCOME
)
FIXED_INCOME_TYPES = frozenset(
    kind for kind, family in CLASS_OF_TYPE.items() if family is InvestmentClass.FIXED_INCOME
)


class RateIndex(StrEnum):
    """A que a renda fixa está atrelada.

    `PREFIXED` é o caso sem índice: a taxa contratada já é o rendimento anual,
    e `rate_percent` vale 11.45 para "11,45% ao ano". Nos demais,
    `rate_percent` é **percentual do índice** — 102 para "102% do CDI".
    """

    CDI = "cdi"
    SELIC = "selic"
    IPCA = "ipca"
    SAVINGS = "savings"
    PREFIXED = "prefixed"


def _enum_column(enum_type: type[StrEnum], name: str, length: int) -> Enum:
    """VARCHAR com CHECK, não ENUM nativo — mesma decisão de `models.category.kind_column`.

    O ENUM do Postgres exige `ALTER TYPE` para ganhar um valor, e isso não roda
    dentro da transação de uma migration junto de outras instruções.
    """
    return Enum(
        enum_type,
        native_enum=False,
        create_constraint=False,
        length=length,
        name=name,
        values_callable=lambda enum: [member.value for member in enum],
    )


class InvestmentAsset(TimestampMixin, Base):
    """Um ativo cotável do catálogo global: PETR4, AAPL, BTC.

    Nasce quando o primeiro usuário o cadastra e nunca é excluído junto com a
    posição — o catálogo sobrevive a quem o povoou, e a próxima posição no
    mesmo símbolo já encontra a última cotação gravada.
    """

    __tablename__ = "investment_assets"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )

    type: Mapped[InvestmentType] = mapped_column(
        _enum_column(InvestmentType, "investment_type", 16), nullable=False
    )

    # O código como o usuário o conhece, não o símbolo de consulta do provedor:
    # a Twelve Data cota cripto como `BTC/USD`, e montar esse par é do cliente
    # HTTP — mudar de provedor não pode obrigar a reescrever dado.
    symbol: Mapped[str] = mapped_column(String(SYMBOL_MAX_LENGTH), nullable=False)

    name: Mapped[str] = mapped_column(String(ASSET_NAME_MAX_LENGTH), nullable=False)
    currency: Mapped[str] = mapped_column(String(CURRENCY_LENGTH), nullable=False)

    # `NULL` é "ainda não cotado": o ativo entra no catálogo no cadastro e a
    # primeira cotação só chega na rodada seguinte. Zero diria que o ativo não
    # vale nada, que é uma afirmação diferente de não saber.
    price: Mapped[Decimal | None] = mapped_column(
        Numeric(UNIT_MAX_DIGITS, UNIT_DECIMAL_PLACES), nullable=True
    )

    previous_close: Mapped[Decimal | None] = mapped_column(
        Numeric(UNIT_MAX_DIGITS, UNIT_DECIMAL_PLACES), nullable=True
    )
    change_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(RATE_MAX_DIGITS, RATE_DECIMAL_PLACES), nullable=True
    )

    # Quando o provedor mediu o preço — é o que ordena a fila do agendador.
    quoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    logo_url: Mapped[str | None] = mapped_column(String(LOGO_URL_MAX_LENGTH), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "type IN ({})".format(
                ", ".join(f"'{member.value}'" for member in sorted(VARIABLE_INCOME_TYPES))
            ),
            name="variable_income_only",
        ),
        # `upper` pelo mesmo motivo que as categorias usam `lower`: "petr4" e
        # "PETR4" são o mesmo papel, e duas linhas para ele seriam duas
        # requisições por rodada e dois preços divergentes na mesma tela.
        Index(
            "uq_investment_assets_type_symbol",
            "type",
            text("upper(symbol)"),
            unique=True,
        ),
        # A fila do agendador: o mais desatualizado primeiro, nulos na frente.
        Index("ix_investment_assets_quoted_at", "quoted_at"),
    )

    def __repr__(self) -> str:
        return f"<InvestmentAsset {self.symbol} ({self.type}) {self.price} {self.currency}>"


class Investment(TimestampMixin, Base):
    """A posição de um usuário — em um ativo, ou em um contrato de renda fixa."""

    __tablename__ = "investments"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    type: Mapped[InvestmentType] = mapped_column(
        _enum_column(InvestmentType, "investment_type", 16), nullable=False
    )

    # `NULL` em renda fixa, que não tem símbolo.
    asset_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        # Sem `ondelete`: o catálogo não é excluído pela API, e NO ACTION
        # garante que uma limpeza futura não leve junto a posição de ninguém.
        ForeignKey("investment_assets.id"),
        nullable=True,
    )

    # `lazy="raise"` troca um `MissingGreenlet` no meio da serialização por um
    # erro que diz o que faltou — o `contains_eager` do repositório.
    asset: Mapped[InvestmentAsset | None] = relationship(lazy="raise")

    # Como o usuário chama a posição: dois CDBs do mesmo banco com vencimentos
    # diferentes precisam ser distinguíveis na lista.
    name: Mapped[str] = mapped_column(String(INVESTMENT_NAME_MAX_LENGTH), nullable=False)

    quantity: Mapped[Decimal | None] = mapped_column(
        Numeric(UNIT_MAX_DIGITS, UNIT_DECIMAL_PLACES), nullable=True
    )

    # Preço médio pago, na moeda do ativo. Recalculado a cada aporte pela média
    # ponderada — é o que dispensa guardar o histórico de compras aqui.
    average_price: Mapped[Decimal | None] = mapped_column(
        Numeric(UNIT_MAX_DIGITS, UNIT_DECIMAL_PLACES), nullable=True
    )

    # Quanto saiu do bolso, **em BRL**. Não é `quantity x average_price`: para
    # ação americana e cripto esse produto está em dólar, e compará-lo com um
    # `current_value` em real produziria um lucro que é só a cotação do câmbio.
    invested_amount: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_MAX_DIGITS, MONEY_DECIMAL_PLACES), nullable=False, server_default=text("0")
    )

    rate_index: Mapped[RateIndex | None] = mapped_column(
        _enum_column(RateIndex, "rate_index", 16), nullable=True
    )
    # O que a taxa significa depende de `rate_index` — ver a tabela em `RateIndex`.
    rate_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(RATE_MAX_DIGITS, RATE_DECIMAL_PLACES), nullable=True
    )

    applied_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # `NULL` é sem prazo — poupança e CDB de liquidez diária.
    matures_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Materializado, e não calculado na leitura: recalcular na consulta obrigaria
    # toda listagem a fazer a conversão de moeda, e o total da carteira passaria
    # a depender de quando a tela foi aberta.
    current_value: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_MAX_DIGITS, MONEY_DECIMAL_PLACES), nullable=False, server_default=text("0")
    )

    value_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # CHECKs simples só: a regra cruzada ("renda variável exige ativo") é do
        # serviço, onde a recusa sai como 422 com o campo apontado em vez de
        # 500 na violação de constraint. O banco guarda o que não pode ser
        # negociado em nenhum caminho.
        CheckConstraint("quantity IS NULL OR quantity >= 0", name="quantity_not_negative"),
        CheckConstraint(
            "average_price IS NULL OR average_price >= 0", name="average_price_not_negative"
        ),
        CheckConstraint("invested_amount >= 0", name="invested_amount_not_negative"),
        CheckConstraint("rate_percent IS NULL OR rate_percent > 0", name="rate_percent_positive"),
        CheckConstraint("current_value >= 0", name="current_value_not_negative"),
        CheckConstraint(
            "matures_on IS NULL OR applied_on IS NULL OR matures_on >= applied_on",
            name="matures_after_applied",
        ),
        # Toda listagem é "os meus"; sem isto é varredura de tabela inteira.
        Index("ix_investments_user_id", "user_id"),
        # O agendador varre as posições de um ativo para recalcular o valor.
        Index("ix_investments_asset_id", "asset_id"),
        # A rodada de renda fixa acrua só o que tem índice — e nenhuma posição
        # de renda variável tem, o que faz o índice parcial ser metade da tabela.
        Index(
            "ix_investments_rate_index",
            "rate_index",
            postgresql_where=text("rate_index IS NOT NULL"),
            sqlite_where=text("rate_index IS NOT NULL"),
        ),
    )

    @property
    def investment_class(self) -> InvestmentClass:
        return CLASS_OF_TYPE[self.type]

    @property
    def is_variable_income(self) -> bool:
        return self.investment_class is InvestmentClass.VARIABLE_INCOME

    def __repr__(self) -> str:
        return f"<Investment {self.name} ({self.type}) R$ {self.current_value}>"


class InvestmentRate(TimestampMixin, Base):
    """A última leitura de um índice do Banco Central, anualizada.

    Uma linha por índice, global como o catálogo de ativos: o CDI é o mesmo para
    todo mundo. Existe para que o acrual da renda fixa seja aritmética local —
    quatro chamadas públicas por rodada, e não uma por posição.

    Guardar já anualizado é o que permite a um cálculo só avaliar as quatro
    modalidades: as séries do SGS vêm em % ao dia útil (CDI, SELIC) ou em % ao
    mês (IPCA, poupança), e converter na leitura espalharia essa diferença por
    quem acrua. A conversão mora em `providers.bcb.annualize`.
    """

    __tablename__ = "investment_rates"

    index: Mapped[RateIndex] = mapped_column(
        _enum_column(RateIndex, "rate_index", 16), primary_key=True
    )

    annual_percent: Mapped[Decimal] = mapped_column(
        Numeric(RATE_MAX_DIGITS, RATE_DECIMAL_PLACES), nullable=False
    )

    # O dia a que a leitura se refere, que não é o dia em que ela foi buscada: o
    # SGS publica com atraso, e um CDI de ontem é dado correto.
    reference_date: Mapped[date] = mapped_column(Date, nullable=False)

    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<InvestmentRate {self.index} {self.annual_percent}% a.a.>"
