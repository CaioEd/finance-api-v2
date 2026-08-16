"""Toda resposta de erro tem o mesmo formato, inclusive as que o FastAPI gera.

Cliente que precisa de um parser por endpoint é cliente que vai errar em um
deles. O envelope é `{"error": {"code", "message", "details"}}`.
"""

from __future__ import annotations

from httpx import AsyncClient

from finance_api.core.errors import (
    ConflictError,
    DomainError,
    ForbiddenError,
    NotFoundError,
    UnprocessableError,
)


async def test_unknown_route_uses_the_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/nao-existe")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Not Found", "details": []}
    }


async def test_wrong_method_uses_the_error_envelope(client: AsyncClient) -> None:
    response = await client.post("/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_domain_errors_carry_status_and_code() -> None:
    assert (NotFoundError.status_code, NotFoundError.code) == (404, "not_found")
    assert (ConflictError.status_code, ConflictError.code) == (409, "conflict")
    assert (ForbiddenError.status_code, ForbiddenError.code) == (403, "forbidden")
    assert (UnprocessableError.status_code, UnprocessableError.code) == (
        422,
        "validation_error",
    )


def test_domain_error_accepts_a_specific_message_and_details() -> None:
    error = DomainError("mensagem específica", details=[{"field": "amount"}])

    assert error.message == "mensagem específica"
    assert error.details == [{"field": "amount"}]
