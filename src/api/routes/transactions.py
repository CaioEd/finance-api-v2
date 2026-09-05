"""Rotas de transações.

A autenticação é declarada no router inteiro, não em cada rota. O escopo por
dono vem do repositório: não existe aqui um `if` comparando o dono do
lançamento com quem pediu, e nenhuma rota aceita o id de outro usuário.

Não há rota de admin sobre lançamento de terceiro — isso é fase 6.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from dependencies.auth import get_current_user
from dependencies.services import get_transaction_service
from models.category import CategoryKind
from models.user import User
from repositories.transaction_repository import TransactionFilters
from schemas.transaction import (
    TransactionCreateIn,
    TransactionOut,
    TransactionPageOut,
    TransactionUpdateIn,
)
from services.transaction_service import TransactionService

router = APIRouter(
    prefix="/transactions",
    tags=["transactions"],
    dependencies=[Depends(get_current_user)],
    responses={401: {"description": "Ausência de token, token inválido ou expirado"}},
)

# Anotadas por causa do `responses` do FastAPI, cuja chave é `int | str`.
Responses = dict[int | str, dict[str, Any]]

NOT_FOUND: Responses = {404: {"description": "Lançamento inexistente ou de outro usuário"}}
BAD_CATEGORY: Responses = {422: {"description": "Categoria inexistente ou de outro usuário"}}


@router.get("", summary="Lista os lançamentos do próprio usuário")
async def list_transactions(
    user: User = Depends(get_current_user),
    service: TransactionService = Depends(get_transaction_service),
    limit: int = Query(50, ge=1, le=100, description="Tamanho da página"),
    offset: int = Query(0, ge=0),
    kind: CategoryKind | None = Query(None, description="Filtra por receita ou despesa"),
    category_id: UUID | None = Query(None, description="Filtra por uma categoria"),
    occurred_from: date | None = Query(None, description="Competência a partir de (inclusive)"),
    occurred_to: date | None = Query(None, description="Competência até (inclusive)"),
) -> TransactionPageOut:
    page = await service.list_transactions(
        user,
        filters=TransactionFilters(
            kind=kind,
            category_id=category_id,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
        ),
        limit=limit,
        offset=offset,
    )
    return TransactionPageOut(
        items=[TransactionOut.model_validate(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Registra uma receita ou despesa",
    responses=BAD_CATEGORY,
)
async def create_transaction(
    data: TransactionCreateIn,
    user: User = Depends(get_current_user),
    service: TransactionService = Depends(get_transaction_service),
) -> TransactionOut:
    transaction = await service.create(user, data)
    return TransactionOut.model_validate(transaction)


@router.get("/{transaction_id}", summary="Detalha um lançamento", responses=NOT_FOUND)
async def read_transaction(
    transaction_id: UUID,
    user: User = Depends(get_current_user),
    service: TransactionService = Depends(get_transaction_service),
) -> TransactionOut:
    transaction = await service.get(user, transaction_id)
    return TransactionOut.model_validate(transaction)


@router.patch(
    "/{transaction_id}",
    summary="Atualiza um lançamento do próprio usuário",
    responses=NOT_FOUND | BAD_CATEGORY,
)
async def update_transaction(
    transaction_id: UUID,
    data: TransactionUpdateIn,
    user: User = Depends(get_current_user),
    service: TransactionService = Depends(get_transaction_service),
) -> TransactionOut:
    transaction = await service.update(user, transaction_id, data)
    return TransactionOut.model_validate(transaction)


@router.delete(
    "/{transaction_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Exclui um lançamento do próprio usuário",
    responses=NOT_FOUND,
)
async def delete_transaction(
    transaction_id: UUID,
    user: User = Depends(get_current_user),
    service: TransactionService = Depends(get_transaction_service),
) -> Response:
    await service.delete(user, transaction_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
