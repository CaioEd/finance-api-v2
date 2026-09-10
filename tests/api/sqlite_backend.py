"""O Postgres da aplicação, traduzido para um SQLite em memória.

Existe para que a suíte de API rode em qualquer lugar — máquina sem Docker, CI
mínimo, `pytest` no meio de uma refatoração — sem depender do serviço
`db-test`. O que ela verifica é o contrato HTTP: rota, status, envelope de
erro, forma da resposta e escopo por dono. Nada disso é assunto do banco.

O que **não** vem para cá continua em `tests/integration/`, contra Postgres de
verdade, e é por isso que aquela suíte não desaparece:

- o schema, que lá nasce das migrations e aqui nasce do `Base.metadata` —
  migration quebrada precisa reprovar o build, e `create_all` esconde isso;
- as categorias do sistema, dado de referência que a migration insere;
- `NUMERIC` e a semântica de índice parcial do Postgres;
- o agrupamento por mês do saldo: aqui ele passa por um `date_trunc` de
  mentira (ver abaixo), e quem responde por ele de verdade é
  `tests/integration/test_balance.py`.

Cinco traduções bastam para que o mesmo código de aplicação rode nos dois bancos
sem uma linha de `if`:

1. **`gen_random_uuid()` não existe no SQLite.** O `server_default` sai do
   schema de teste; quem preenche o id no INSERT é o `default=uuid4` do model,
   que continua valendo nos dois bancos.
2. **Índice parcial.** `postgresql_where` é ignorado por outro dialeto, e um
   `UNIQUE (lower(name), kind)` sobre a tabela inteira proibiria dois usuários
   de terem a mesma categoria. A cláusula é copiada para `sqlite_where`.
3. **`TIMESTAMPTZ`.** O SQLite guarda data como texto e a devolve como
   `datetime` ingênuo; `RefreshToken.is_usable_at` compara com um instante
   ciente e estouraria em `TypeError`. `UtcDateTime` grava em UTC e devolve
   ciente, que é o que a coluna faz em produção.
4. **`date_trunc('month', …)`**, de que `BalanceRepository.monthly_totals`
   depende, entra como função de usuário na conexão. Ela implementa `month` e
   **recusa** qualquer outra unidade: improvisar `week` — que no Postgres
   começa na segunda — faria a suíte afirmar um agrupamento que a produção não
   produz.
5. **`CAST(… AS DATE)`**, que acompanha o `date_trunc`, vira nada. `DATE` não é
   tipo do SQLite: o nome cai em afinidade NUMERIC, e o CAST passa a valer a
   regra do prefixo numérico — `'2026-09-01'` viraria `2026`, e o saldo mensal
   sairia agrupado por ano sem ninguém notar.

E uma adaptação de mensagem: o Postgres nomeia a constraint violada, e
`repositories.user_repository.translate_integrity_error` lê esse nome para
distinguir e-mail de nome de usuário. O SQLite diz só `users.email` — o nome é
reposto a partir do próprio metadata, para que o 409 daqui traga o mesmo `code`
que o de produção, e não um `conflict` genérico que esconderia a diferença.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import MetaData, Table, UniqueConstraint, event, types
from sqlalchemy.dialects import sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.expression import Cast

from core.database import NAMING_CONVENTION, Base, Database

# Importados só para registrar as tabelas no metadata, como em `alembic/env.py`.
# Model novo em `models/` precisa entrar nesta lista, ou a tabela não é criada.
from models import category as _category  # noqa: F401
from models import refresh_token as _refresh_token  # noqa: F401
from models import transaction as _transaction  # noqa: F401
from models import user as _user  # noqa: F401

if TYPE_CHECKING:
    from sqlalchemy.engine.interfaces import Dialect, ExceptionContext
    from sqlalchemy.sql.compiler import SQLCompiler

IN_MEMORY_URL = "sqlite+aiosqlite://"
"""Sem caminho: o banco vive dentro da conexão e morre junto com ela."""


class UtcDateTime(sqlite.DATETIME):
    """`DateTime(timezone=True)` com o comportamento do `TIMESTAMPTZ`.

    O SQLite não tem tipo de data: guarda texto, e o dialeto o converte de
    volta para um `datetime` ingênuo, tendo descartado o fuso na ida. Aqui a
    ida normaliza para UTC e a volta reetiqueta — os dois lados da conversão
    que o Postgres faz sozinho.
    """

    def bind_processor(self, dialect: Dialect) -> Callable[[Any], Any]:
        to_text = super().bind_processor(dialect)

        def process(value: datetime | None) -> Any:
            if value is not None and value.tzinfo is not None:
                value = value.astimezone(UTC).replace(tzinfo=None)
            return to_text(value)

        return process

    def result_processor(self, dialect: Dialect, coltype: object) -> Callable[[Any], Any]:
        to_datetime = super().result_processor(dialect, coltype)

        def process(value: Any) -> datetime | None:
            moment: datetime | None = to_datetime(value)
            if moment is not None and moment.tzinfo is None:
                moment = moment.replace(tzinfo=UTC)
            return moment

        return process


def sqlite_schema() -> MetaData:
    """O metadata da aplicação, copiado e ajustado para o SQLite.

    Copiado, e não alterado no lugar: `Base.metadata` é o mesmo objeto que o
    autogenerate do Alembic lê, e ajustá-lo aqui mudaria em silêncio o que uma
    migration gerada no mesmo processo enxerga.
    """
    schema = MetaData(naming_convention=NAMING_CONVENTION)
    for table in Base.metadata.sorted_tables:
        _translate(table.to_metadata(schema))
    return schema


def _translate(table: Table) -> None:
    """Os dois ajustes de DDL descritos no topo do módulo, tabela a tabela."""
    for column in table.columns:
        default = column.server_default
        if default is not None and "gen_random_uuid" in str(getattr(default, "arg", "")):
            column.server_default = None
    for index in table.indexes:
        condition = index.dialect_options["postgresql"].get("where")
        if condition is not None:
            index.dialect_options["sqlite"]["where"] = condition


SCHEMA = sqlite_schema()
"""Traduzido uma vez por processo: o resultado não depende de nenhuma engine."""


def _unique_constraint_names() -> dict[str, str]:
    """`tabela.coluna` → o nome que o Postgres traria na mensagem de violação.

    Sai do metadata, e não de uma lista escrita à mão: a convenção de nomes é
    aplicada no momento em que a constraint se liga à tabela, então o que está
    aqui é exatamente o que existe no banco de produção.
    """
    names: dict[str, str] = {}
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint) and constraint.name:
                columns = ", ".join(f"{table.name}.{column.name}" for column in constraint.columns)
                names[columns] = str(constraint.name)
    return names


UNIQUE_CONSTRAINT_NAMES = _unique_constraint_names()

UNIQUE_VIOLATION = "UNIQUE constraint failed: "


def sqlite_engine() -> AsyncEngine:
    """Um banco em memória por engine — e, na suíte, um por teste.

    `StaticPool` porque o banco *é* a conexão: um pool que a reciclasse jogaria
    fora o schema junto. Como é uma conexão só, tudo acontece no event loop do
    portal do `TestClient`, e nunca em dois.
    """
    engine = create_async_engine(IN_MEMORY_URL, poolclass=StaticPool)

    # `colspecs` é atributo de classe do dialeto; atribuir na instância o
    # sombreia só para esta engine, sem vazar o tipo para nenhuma outra
    # conexão SQLite que o processo venha a abrir.
    dialect = engine.sync_engine.dialect
    dialect.colspecs = {**dialect.colspecs, types.DateTime: UtcDateTime}

    event.listen(engine.sync_engine, "connect", _enforce_foreign_keys)
    event.listen(engine.sync_engine, "connect", _register_date_trunc)
    event.listen(engine.sync_engine, "handle_error", _name_the_violated_constraint)
    return engine


def _enforce_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    """No SQLite a checagem de chave estrangeira é opcional, e vem desligada.

    Sem ela, `DELETE /users/me` deixaria categorias e lançamentos órfãos e
    passaria: o `ON DELETE CASCADE` de que a exclusão da conta depende não
    seria exercitado por teste nenhum.
    """
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


MONTH = "month"


def _date_trunc(unit: str, value: str | None) -> str | None:
    """O `date_trunc` do Postgres, na única forma de que a aplicação precisa.

    `BalanceRepository.monthly_totals` agrupa o saldo por
    `date_trunc('month', occurred_on)`, e o SQLite não tem a função. Como
    `occurred_on` é `DATE` — texto `YYYY-MM-DD` —, truncar no mês é recortar os
    sete primeiros caracteres.

    Recusa qualquer outra unidade em vez de improvisar. `date_trunc('week', …)`
    tem regra própria no Postgres (a semana começa na segunda), e devolver algo
    plausível faria esta suíte afirmar um agrupamento que a produção não
    produz — que é o modo exato como um banco de teste diferente do de produção
    passa a mentir.
    """
    if value is None:
        return None
    if unit != MONTH:
        raise ValueError(f"o date_trunc desta suíte só implementa {MONTH!r}, não {unit!r}")
    return f"{value[:7]}-01"


def _register_date_trunc(dbapi_connection: Any, _record: Any) -> None:
    dbapi_connection.create_function("date_trunc", 2, _date_trunc)


@compiles(Cast, "sqlite")
def _cast_to_date_is_noise(element: Cast, compiler: SQLCompiler, **kw: Any) -> str:
    """`CAST(x AS DATE)` no SQLite destrói a data; aqui ele desaparece.

    `DATE` não é um tipo do SQLite: o nome cai em afinidade NUMERIC, e o CAST
    passa a valer a regra do prefixo numérico — `CAST('2026-09-01' AS DATE)` é
    `2026`. O saldo mensal sairia agrupado por ano, com a resposta ainda no
    formato certo e os números errados.

    Não há o que converter: a coluna guarda a data como texto e `_date_trunc`
    devolve texto no mesmo formato, que é o que o processador de resultado do
    SQLAlchemy espera de um `Date`. O CAST é ruído, e o único efeito dele seria
    perder o mês e o dia.

    Registrado para o dialeto `sqlite` e só para ele: a suíte de integração
    compila em `postgresql`, onde este código não roda. Qualquer outro CAST,
    inclusive em SQLite, segue pelo compilador normal.
    """
    if isinstance(element.type, types.Date):
        return compiler.process(element.clause, **kw)
    return compiler.visit_cast(element, **kw)


def _name_the_violated_constraint(context: ExceptionContext) -> IntegrityError | None:
    """Repõe na mensagem o nome da constraint, que o SQLite não informa.

    `translate_integrity_error` distingue e-mail duplicado de nome de usuário
    duplicado pelo nome da constraint — é o banco que decide o conflito, porque
    um SELECT prévio teria janela de corrida com o INSERT. Sem o nome, os dois
    casos sairiam como `409 conflict` e a suíte afirmaria um contrato que a
    aplicação não cumpre.

    Devolver `None` deixa a exceção original subir intacta, que é o certo para
    tudo que não seja violação de unicidade — inclusive a de chave estrangeira,
    onde o SQLite não diz *qual* FK falhou e não há o que repor.
    """
    original = context.original_exception
    if not isinstance(original, sqlite3.IntegrityError):
        return None

    message = str(original)
    if not message.startswith(UNIQUE_VIOLATION):
        return None

    name = UNIQUE_CONSTRAINT_NAMES.get(message.removeprefix(UNIQUE_VIOLATION))
    if name is None:
        return None

    return IntegrityError(
        context.statement, context.parameters, sqlite3.IntegrityError(f"{message} ({name})")
    )


class SqliteDatabase(Database):
    """O `Database` da aplicação recebendo a engine pronta, em vez de um DSN.

    Herda `ping()` e `dispose()`, e é o que faz `/health/ready` responder aqui
    pelo mesmo caminho que em produção — um duble que sempre dissesse "ok"
    tornaria o teste do readiness uma formalidade.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessionmaker = async_sessionmaker(
            bind=engine, expire_on_commit=False, autoflush=False
        )


async def create_schema(engine: AsyncEngine) -> None:
    """Cria as tabelas a partir do metadata traduzido — ver o topo do módulo."""
    async with engine.begin() as connection:
        await connection.run_sync(SCHEMA.create_all)
