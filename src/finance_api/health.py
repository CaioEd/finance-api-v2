"""Endpoints operacionais.

Ficam fora de `/api/v1` de propósito: não são contrato de produto, não são
versionados e não exigem autenticação. Por isso também não moram em `domains/`.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from finance_api import __version__
from finance_api.core.config import Environment
from finance_api.core.database import Database
from finance_api.core.errors import ServiceUnavailableError

router = APIRouter(tags=["health"])


class HealthOut(BaseModel):
    status: str
    version: str
    environment: Environment


class ReadinessOut(BaseModel):
    status: str
    database: str


@router.get("/health", summary="Liveness: o processo está de pé")
async def health(request: Request) -> HealthOut:
    return HealthOut(
        status="ok",
        version=__version__,
        environment=request.app.state.settings.environment,
    )


@router.get("/health/ready", summary="Readiness: dependências respondem")
async def readiness(request: Request) -> ReadinessOut:
    database: Database = request.app.state.database
    try:
        await database.ping()
    except Exception as exc:  # qualquer falha aqui é indisponibilidade, não erro de negócio
        raise ServiceUnavailableError(
            "Banco de dados indisponível.", details=[{"cause": type(exc).__name__}]
        ) from exc
    return ReadinessOut(status="ok", database="ok")
