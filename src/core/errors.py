"""Exceções de domínio e o envelope único de erro da API.

Serviços levantam `DomainError`; ninguém abaixo da camada HTTP conhece
`HTTPException`. Os handlers registrados aqui garantem que *toda* resposta de
erro — inclusive as que o próprio FastAPI produz — tenha o mesmo formato:

    {"error": {"code": "...", "message": "...", "details": [...]}}
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class DomainError(Exception):
    """Erro de negócio, traduzido para HTTP na fronteira."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "invalid_request"
    message: str = "Requisição inválida."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or []
        super().__init__(self.message)


class NotFoundError(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "Recurso não encontrado."


class ConflictError(DomainError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "Conflito com o estado atual do recurso."


class UnprocessableError(DomainError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "validation_error"
    message = "Dados inválidos."


class AuthenticationError(DomainError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "invalid_credentials"
    message = "E-mail ou senha inválidos."
    # Mensagem única de propósito: distinguir "e-mail não existe" de "senha
    # errada" entrega a lista de contas cadastradas a quem perguntar.


class InvalidTokenError(DomainError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "invalid_token"
    message = "Token inválido."


class TokenExpiredError(DomainError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "token_expired"
    message = "Token expirado."


class InvalidRefreshTokenError(DomainError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "invalid_refresh_token"
    message = "Refresh token inválido ou expirado."


class TokenReuseError(DomainError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "token_reuse_detected"
    message = "Refresh token já utilizado. Todas as sessões desta linhagem foram encerradas."


class ForbiddenError(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    message = "Acesso negado."


class AccountInactiveError(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "account_inactive"
    message = "Conta desativada."


class UserNotFoundError(NotFoundError):
    code = "user_not_found"
    message = "Usuário não encontrado."


class SelfTargetError(ForbiddenError):
    code = "self_target_forbidden"
    message = "Use os endpoints de /users/me para alterar ou excluir a própria conta."
    # A administração não age sobre a conta de quem administra: é o que impede o
    # último admin de se rebaixar ou se excluir e deixar o sistema sem ninguém.


class EmailTakenError(ConflictError):
    code = "email_taken"
    message = "Já existe uma conta com este e-mail."


class UsernameTakenError(ConflictError):
    code = "username_taken"
    message = "Este nome de usuário já está em uso."


class CategoryNameTakenError(ConflictError):
    code = "category_name_taken"
    message = "Já existe uma categoria com este nome."


class CategoryInUseError(ConflictError):
    code = "category_in_use"
    message = "Esta categoria tem lançamentos e não pode ser excluída."
    # Apagar os lançamentos junto seria perder histórico financeiro para
    # remover um rótulo. Quem quer mesmo se livrar da categoria move os
    # lançamentos para outra antes.


class InvalidCategoryError(UnprocessableError):
    code = "invalid_category"
    message = "Categoria inexistente ou de outro usuário."
    # 422 e não 404: o recurso da requisição é o lançamento, e ele não é o que
    # está faltando. Categoria de terceiro e categoria inexistente devolvem
    # exatamente isto, pela mesma razão que credencial inválida tem mensagem
    # única — a resposta não confirma o que existe na conta alheia.


class TransactionNotFoundError(NotFoundError):
    code = "transaction_not_found"
    message = "Lançamento não encontrado."


class InvalidPeriodError(UnprocessableError):
    code = "invalid_period"
    message = "O início do período não pode ser posterior ao fim."
    # 422 e não 404: o pedido é sintaticamente válido e não aponta para recurso
    # nenhum — o que está errado é a combinação das duas pontas, e só o serviço
    # de saldos enxerga isso.


class PeriodTooLongError(UnprocessableError):
    code = "period_too_long"
    message = "O período pedido é longo demais."
    # A série mês a mês devolve uma linha por mês do intervalo, inclusive os
    # vazios. Sem teto, `from_month=0001-01` pediria vinte e quatro mil linhas
    # de zero — resposta cara de montar e inútil de ler.


class ServiceUnavailableError(DomainError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"
    message = "Serviço indisponível."


def error_payload(
    code: str, message: str, details: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or []}}


_HTTP_STATUS_CODES: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: "invalid_request",
    status.HTTP_401_UNAUTHORIZED: "invalid_credentials",
    status.HTTP_403_FORBIDDEN: "forbidden",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "validation_error",
}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(part) for part in error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=error_payload("validation_error", "Dados inválidos.", details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_STATUS_CODES.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(code, str(exc.detail)),
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("erro não tratado em %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_payload("internal_error", "Erro interno."),
        )
