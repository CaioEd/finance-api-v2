"""Acesso a dados de recorrências.

Mesmo desenho de `repositories.transaction_repository`: o escopo por dono é
imposto **aqui** — toda consulta de usuário nasce de
`RecurringTransaction.user_id == user_id` — e toda consulta traz a categoria
junto (`contains_eager`), de onde sai o `kind`.

A exceção é `lock_due`, a consulta do agendador, que atravessa todos os donos
de propósito: registrar o lançamento de todo mundo é o trabalho dela. Nenhuma
rota a alcança.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from models.category import Category, CategoryKind
from models.recurring_transaction import RecurringTransaction


def _conditions(user_id: UUID, kind: CategoryKind | None) -> list[ColumnElement[bool]]:
    """Um `WHERE` só para a listagem e a contagem — senão o `total` mente."""
    conditions: list[ColumnElement[bool]] = [RecurringTransaction.user_id == user_id]
    if kind is not None:
        conditions.append(Category.kind == kind)
    return conditions


def _with_category() -> Select[tuple[RecurringTransaction]]:
    return (
        select(RecurringTransaction)
        .join(Category, RecurringTransaction.category_id == Category.id)
        .options(contains_eager(RecurringTransaction.category))
    )


class RecurringTransactionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_recurring(
        self, user_id: UUID, *, kind: CategoryKind | None, limit: int, offset: int
    ) -> Sequence[RecurringTransaction]:
        statement = (
            _with_category()
            .where(*_conditions(user_id, kind))
            # Pelo dia do mês, que é como se pensa numa conta fixa ("a do dia
            # 5"); o id desempata para a paginação ser estável.
            .order_by(RecurringTransaction.day_of_month, RecurringTransaction.id)
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.scalars(statement)
        return result.all()

    async def count_recurring(self, user_id: UUID, *, kind: CategoryKind | None) -> int:
        statement = (
            select(func.count())
            .select_from(RecurringTransaction)
            .join(Category, RecurringTransaction.category_id == Category.id)
            .where(*_conditions(user_id, kind))
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def get_owned(
        self, rule_id: UUID, user_id: UUID, *, lock: bool = False
    ) -> RecurringTransaction | None:
        """Devolve `None` para recorrência de terceiro — que a borda traduz em 404.

        `lock=True` trava a linha até o commit. É o que a edição pede: trocar o
        dia ou reativar recalcula a próxima data a partir da que está gravada,
        e o agendador avançando essa mesma data no meio do caminho faria a
        edição regravar o valor antigo — e o mês já registrado sairia de novo.
        Com a linha travada, o agendador a pula (`SKIP LOCKED`) e a própria
        edição registra o que tiver vencido.
        """
        statement = _with_category().where(
            RecurringTransaction.id == rule_id, RecurringTransaction.user_id == user_id
        )
        if lock:
            statement = statement.with_for_update(of=RecurringTransaction)
        result = await self._session.scalars(statement)
        return result.first()

    async def lock_due(self, today: date, *, limit: int) -> Sequence[RecurringTransaction]:
        """Trava e devolve até `limit` recorrências ativas com ocorrência vencida.

        `FOR UPDATE ... SKIP LOCKED` é o que torna o agendador seguro com mais
        de uma réplica rodando ao mesmo tempo: cada linha é de quem a travou
        primeiro, e os outros seguem para as que sobraram em vez de esperar —
        ou de registrar o mesmo mês outra vez. A trava vive até o commit, que é
        o mesmo em que os lançamentos entram e a próxima data avança; depois
        dele a linha não está mais vencida, e ninguém mais a encontra.

        `of=RecurringTransaction` trava só a regra: o JOIN com `categories` não
        pode prender a categoria, que é de todos no caso das globais.
        """
        statement = (
            _with_category()
            .where(
                RecurringTransaction.is_active.is_(True),
                RecurringTransaction.next_occurrence_on <= today,
            )
            .order_by(RecurringTransaction.next_occurrence_on, RecurringTransaction.id)
            .limit(limit)
            .with_for_update(skip_locked=True, of=RecurringTransaction)
        )
        result = await self._session.scalars(statement)
        return result.all()

    def add(self, rule: RecurringTransaction) -> None:
        self._session.add(rule)

    async def delete(self, rule: RecurringTransaction) -> None:
        await self._session.delete(rule)
