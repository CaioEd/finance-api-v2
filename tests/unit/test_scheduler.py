"""Laço do agendador e a ligação dele na subida da aplicação, com um job de mentira."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import main
from core.clock import Clock
from core.config import Settings
from core.database import Database
from core.scheduler import PeriodicJob
from main import create_app

VALID = {
    "environment": "local",
    "app_timezone": "America/Sao_Paulo",
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
    "jwt_secret_key": "chave-de-teste-com-comprimento-mais-que-suficiente",
}

TICK = 0.01


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **{**VALID, **overrides})  # type: ignore[arg-type]


async def wait_until(condition: Any, *, timeout: float = 1.0) -> None:
    """Espera ativa curta: o laço roda noutra task, e o teste só o observa."""
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(TICK / 2)


async def test_the_job_runs_right_away_and_then_on_every_interval() -> None:
    calls = 0

    async def job() -> None:
        nonlocal calls
        calls += 1

    periodic = PeriodicJob(job, interval_seconds=TICK, name="teste")
    periodic.start()
    try:
        await wait_until(lambda: calls >= 1)
        await wait_until(lambda: calls >= 3)
    finally:
        await periodic.stop()

    assert not periodic.running


async def test_the_first_round_does_not_wait_for_the_interval() -> None:
    """Um deploy não pode adiar em quinze minutos o que já venceu."""
    ran = asyncio.Event()

    async def job() -> None:
        ran.set()

    periodic = PeriodicJob(job, interval_seconds=3600, name="teste")
    periodic.start()
    try:
        await asyncio.wait_for(ran.wait(), timeout=1.0)
    finally:
        await periodic.stop()


async def test_a_failing_round_is_logged_and_the_loop_goes_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0

    async def job() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("banco fora do ar")

    periodic = PeriodicJob(job, interval_seconds=TICK, name="recorrências")
    periodic.start()
    try:
        await wait_until(lambda: calls >= 2)
    finally:
        await periodic.stop()

    assert "recorrências" in caplog.text
    assert "banco fora do ar" in caplog.text


async def test_starting_twice_does_not_run_two_loops() -> None:
    running = 0
    peak = 0

    async def job() -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(TICK)
        running -= 1

    periodic = PeriodicJob(job, interval_seconds=TICK, name="teste")
    periodic.start()
    periodic.start()
    try:
        await asyncio.sleep(TICK * 5)
    finally:
        await periodic.stop()

    assert peak == 1


async def test_stop_cancels_a_round_in_progress_and_is_idempotent() -> None:
    started = asyncio.Event()

    async def job() -> None:
        started.set()
        await asyncio.sleep(3600)

    periodic = PeriodicJob(job, interval_seconds=TICK, name="teste")
    periodic.start()
    await asyncio.wait_for(started.wait(), timeout=1.0)

    await asyncio.wait_for(periodic.stop(), timeout=1.0)
    await periodic.stop()

    assert not periodic.running


# ------------------------------------------------------------ na aplicação


async def test_the_application_registers_recurrences_while_it_is_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Liga o agendador na subida, com o banco e o relógio do `app.state`, e o para na saída."""
    seen: list[tuple[Database, Clock]] = []

    async def fake_register(database: Database, clock: Clock) -> int:
        seen.append((database, clock))
        return 0

    monkeypatch.setattr(main, "register_due_recurrences", fake_register)
    app = create_app(_settings(recurring_scheduler_interval_seconds=3600))

    async with app.router.lifespan_context(app):
        await wait_until(lambda: len(seen) >= 1)
        assert seen[0] == (app.state.database, app.state.clock)

    rounds = len(seen)
    await asyncio.sleep(TICK * 3)
    assert len(seen) == rounds, "o laço continuou depois da aplicação encerrar"


async def test_the_scheduler_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def fake_register(database: Database, clock: Clock) -> int:
        nonlocal calls
        calls += 1
        return 0

    monkeypatch.setattr(main, "register_due_recurrences", fake_register)
    app = create_app(_settings(recurring_scheduler_enabled=False))

    async with app.router.lifespan_context(app):
        await asyncio.sleep(TICK * 3)

    assert calls == 0


def test_the_interval_must_be_positive() -> None:
    with pytest.raises(ValueError, match="recurring_scheduler_interval_seconds"):
        _settings(recurring_scheduler_interval_seconds=0)
