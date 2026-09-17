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
"""Regras por transação.

Lote, e não tudo de uma vez: a rodada trava as regras que processa até o commit,
e uma transação única seguraria todas as recorrências do sistema enquanto
milhares de lançamentos entram — inclusive contra o usuário que só queria
editar a sua.
"""


async def register_due_recurrences(
    database: Database, clock: Clock, *, batch_size: int = BATCH_SIZE
) -> int:
    """Roda lotes até não sobrar regra vencida; devolve quantos lançamentos entraram.

    Cada lote numa sessão nova: o commit de um lote não depende do seguinte, e
    uma falha no meio perde só o lote que falhou — que continua vencido e volta
    na próxima rodada.
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
        # Lote incompleto é a consulta dizendo que acabou. As que outra réplica
        # travou foram puladas e são dela; esperar por elas aqui não adiantaria.
        if batch.rules < batch_size:
            break
    if registered:
        logger.info("recorrências: %d lançamento(s) registrado(s)", registered)
    return registered
