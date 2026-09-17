"""Registra os lançamentos das recorrências vencidas, de todos os usuários."""

from __future__ import annotations

import logging

from core.clock import Clock
from core.database import Database
from repositories.category_repository import CategoryRepository
from repositories.recurring_transaction_repository import RecurringTransactionRepository
from repositories.transaction_repository import TransactionRepository
from services.recurring_transaction_service import RecurringTransactionService

logger = logging.getLogger(__name__)

BATCH_SIZE = 100
"""Regras por transação: um lote só travaria todas as recorrências até o fim da rodada."""


async def register_due_recurrences(
    database: Database, clock: Clock, *, batch_size: int = BATCH_SIZE
) -> int:
    """Roda lotes até acabar o que venceu; devolve quantos lançamentos entraram.

    Um lote por sessão: se um falha, só ele volta na próxima rodada.
    """
    registered = 0
    while True:
        async with database.sessionmaker() as session:
            service = RecurringTransactionService(
                unit_of_work=session,
                recurrences=RecurringTransactionRepository(session),
                transactions=TransactionRepository(session),
                categories=CategoryRepository(session),
                clock=clock,
            )
            batch = await service.register_due(limit=batch_size)
        registered += batch.occurrences
        # Lote incompleto: acabou (o que outra réplica travou é dela).
        if batch.rules < batch_size:
            break
    if registered:
        logger.info("recorrências: %d lançamento(s) registrado(s)", registered)
    return registered
