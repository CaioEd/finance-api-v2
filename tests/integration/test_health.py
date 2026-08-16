from __future__ import annotations

import pytest
from httpx import AsyncClient

from finance_api import __version__


async def test_health_reports_version_and_environment(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__, "environment": "test"}


@pytest.mark.integration
async def test_readiness_reaches_the_database(client: AsyncClient) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
