"""Trabalho periódico dentro do processo da API.

Roda uma corrotina na subida e depois a cada intervalo, sem saber o que ela faz
(o job é montado em `jobs/` e ligado em `main.py`). Dispensa um cron à parte
porque o trabalho é idempotente e seguro com várias réplicas.

Roda já na subida, para um restart não adiar o que venceu, e uma rodada que
falha só vai para o log: a seguinte tenta de novo.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class PeriodicJob:
    def __init__(
        self, job: Callable[[], Awaitable[object]], *, interval_seconds: float, name: str
    ) -> None:
        self._job = job
        self._interval_seconds = interval_seconds
        self._name = name
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Chamar duas vezes não cria dois laços."""
        if self.running:
            return
        self._task = asyncio.create_task(self._loop(), name=self._name)

    async def stop(self) -> None:
        """Cancela até no meio de uma rodada: o que não foi comitado sofre rollback."""
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self._job()
            except Exception:
                logger.exception(
                    "trabalho periódico %r falhou; nova tentativa no próximo ciclo", self._name
                )
            await asyncio.sleep(self._interval_seconds)
