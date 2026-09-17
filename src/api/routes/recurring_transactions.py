"""Rotas de recorrências: criar só a regra, listar, editar, pausar e excluir.

Registrar a despesa e já repeti-la é `POST`/`PATCH /transactions` com `recurrence`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from dependencies.auth import get_current_user
from dependencies.services import get_recurring_transaction_service
from models.category import CategoryKind
from models.user import User
from schemas.recurring_transaction import (
    RecurringTransactionCreateIn,
    RecurringTransactionOut,
    RecurringTransactionPageOut,
    RecurringTransactionUpdateIn,
)
from services.recurring_transaction_service import RecurringTransactionService

router = APIRouter(
    prefix="/recurring-transactions",
    tags=["recurring-transactions"],
    dependencies=[Depends(get_current_user)],
    responses={401: {"description": "Ausência de token, token inválido ou expirado"}},
)

Responses = dict[int | str, dict[str, Any]]

NOT_FOUND: Responses = {404: {"description": "Recorrência inexistente ou de outro usuário"}}
BAD_CATEGORY: Responses = {422: {"description": "Categoria inexistente ou de outro usuário"}}


@router.get("", summary="Lista as recorrências do próprio usuário")
async def list_recurring_transactions(
    user: User = Depends(get_current_user),
    service: RecurringTransactionService = Depends(get_recurring_transaction_service),
    limit: int = Query(50, ge=1, le=100, description="Tamanho da página"),
    offset: int = Query(0, ge=0),
    kind: CategoryKind | None = Query(None, description="Filtra por receita ou despesa"),
) -> RecurringTransactionPageOut:
    page = await service.list_recurring(user, kind=kind, limit=limit, offset=offset)
    return RecurringTransactionPageOut(
        items=[RecurringTransactionOut.model_validate(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Cria uma receita ou despesa que se registra sozinha todo mês",
    responses=BAD_CATEGORY,
)
async def create_recurring_transaction(
    data: RecurringTransactionCreateIn,
    user: User = Depends(get_current_user),
    service: RecurringTransactionService = Depends(get_recurring_transaction_service),
) -> RecurringTransactionOut:
    rule = await service.create(user, data)
    return RecurringTransactionOut.model_validate(rule)


@router.get("/{recurring_transaction_id}", summary="Detalha uma recorrência", responses=NOT_FOUND)
async def read_recurring_transaction(
    recurring_transaction_id: UUID,
    user: User = Depends(get_current_user),
    service: RecurringTransactionService = Depends(get_recurring_transaction_service),
) -> RecurringTransactionOut:
    rule = await service.get(user, recurring_transaction_id)
    return RecurringTransactionOut.model_validate(rule)


@router.patch(
    "/{recurring_transaction_id}",
    summary="Atualiza, pausa ou retoma uma recorrência do próprio usuário",
    responses=NOT_FOUND | BAD_CATEGORY,
)
async def update_recurring_transaction(
    recurring_transaction_id: UUID,
    data: RecurringTransactionUpdateIn,
    user: User = Depends(get_current_user),
    service: RecurringTransactionService = Depends(get_recurring_transaction_service),
) -> RecurringTransactionOut:
    rule = await service.update(user, recurring_transaction_id, data)
    return RecurringTransactionOut.model_validate(rule)


@router.delete(
    "/{recurring_transaction_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Exclui uma recorrência; os lançamentos que ela já registrou ficam",
    responses=NOT_FOUND,
)
async def delete_recurring_transaction(
    recurring_transaction_id: UUID,
    user: User = Depends(get_current_user),
    service: RecurringTransactionService = Depends(get_recurring_transaction_service),
) -> Response:
    await service.delete(user, recurring_transaction_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
