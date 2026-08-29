"""Escopo unitário: a garantia de que nada aqui fala com o banco.

Teste unitário se escreve contra um duble (`Protocol`), não contra Postgres.
Isso é fácil de afirmar e fácil de violar sem querer: basta um fixture novo,
ou um import que arraste uma sessão junto, e a suíte volta a exigir container.

O fixture abaixo transforma essa regra em falha de teste em vez de comentário.
"""

from __future__ import annotations

from typing import Never

import asyncpg
import pytest


@pytest.fixture(autouse=True)
def database_is_out_of_reach(monkeypatch: pytest.MonkeyPatch) -> None:
    """Derruba qualquer tentativa de conectar no Postgres.

    O alvo é o driver, e não o socket: tudo que este projeto faz em direção ao
    banco — SQLAlchemy, Alembic, `asyncpg` direto — passa por aqui, e bloquear
    o socket inteiro reprovaria por engano no Windows, onde o `socketpair` que
    o próprio asyncio usa é emulado com um `connect` em 127.0.0.1.
    """

    def refuse(*args: object, **kwargs: object) -> Never:
        raise AssertionError(
            "um teste em tests/unit tentou conectar no Postgres. Teste unitário "
            "usa duble (Protocol), não banco: ou faltou o duble, ou o teste é de "
            "integração e o lugar dele é tests/integration/."
        )

    monkeypatch.setattr(asyncpg, "connect", refuse)
    monkeypatch.setattr(asyncpg, "create_pool", refuse)
