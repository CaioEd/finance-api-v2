"""Trabalho periódico em segundo plano, dentro do processo da API.

Infraestrutura pura: roda uma corrotina na subida e depois a cada intervalo, e
não sabe o que ela faz. O que roda — hoje, registrar as recorrências vencidas —
é montado em `jobs/` e ligado no `lifespan` de `main.py`.

Dentro do processo, e não num cron de fora, porque o trabalho é idempotente e
seguro em paralelo (cada rodada trava o que processa): com duas réplicas, as
duas rodam, e nenhuma lança o que a outra lançou. Isso dispensa um serviço a
mais no deploy só para chamar uma função a cada quinze minutos.

Duas decisões:

- **Uma rodada na subida.** Um deploy, um restart ou uma máquina que dormiu não
  podem adiar em um intervalo inteiro o que já venceu.
- **Falha não mata o laço.** Exceção de uma rodada vai para o log e a seguinte
  tenta de novo; um banco fora do ar por um minuto não pode desligar o
  agendador até o próximo deploy.
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
        """Agenda o laço no event loop corrente; chamar duas vezes não cria dois."""
        if self.running:
            return
        self._task = asyncio.create_task(self._loop(), name=self._name)

    async def stop(self) -> None:
        """Cancela o laço e espera ele terminar — inclusive no meio de uma rodada.

        Cancelar no meio é seguro para quem escreve em transação: o que não foi
        comitado sofre rollback, e a rodada seguinte, noutro processo, refaz.
        """
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
