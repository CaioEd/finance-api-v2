"""Os recortes de `/transactions` e `/balance` em PDF, com os mesmos filtros.

A renderização roda em `run_in_threadpool`: montar PDF é CPU, e o event loop é um só.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.concurrency import run_in_threadpool

from core.clock import MONTH_KEY_PATTERN, parse_month
from core.pdf import render_pdf
from dependencies.auth import get_current_user
from dependencies.services import get_report_service
from models.category import CategoryKind
from models.user import User
from repositories.transaction_repository import TransactionFilters
from services.balance_service import DEFAULT_MONTHS, MAX_MONTHS
from services.report_service import MAX_ROWS, Report, ReportService

router = APIRouter(
    prefix="/reports",
    tags=["reports"],
    dependencies=[Depends(get_current_user)],
    responses={401: {"description": "Ausência de token, token inválido ou expirado"}},
)

# Anotadas por causa do `responses` do FastAPI, cuja chave é `int | str`.
Responses = dict[int | str, dict[str, Any]]

PDF_MEDIA_TYPE = "application/pdf"

# Declarado à mão: sem isto o OpenAPI anunciaria JSON.
PDF_DOWNLOAD: Responses = {
    200: {
        "description": "O relatório em PDF, como anexo para download",
        "content": {PDF_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
    }
}

TOO_MANY_ROWS: Responses = {
    422: {"description": f"O recorte tem mais de {MAX_ROWS} lançamentos, ou o período é inválido"}
}

BAD_PERIOD: Responses = {
    422: {"description": f"Início posterior ao fim, ou mais de {MAX_MONTHS} meses de série"}
}

MONTH_QUERY = (
    "Mês de competência, no formato YYYY-MM. Ausente, a janela é de "
    f"{DEFAULT_MONTHS} meses ancorada na outra ponta"
)


async def _download(report: Report) -> Response:
    """Anexo para download, fora do cache do navegador."""
    content = await run_in_threadpool(render_pdf, report.document)
    return Response(
        content=content,
        media_type=PDF_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{report.filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get(
    "/transactions",
    summary="Extrato de lançamentos em PDF",
    response_class=Response,
    responses=PDF_DOWNLOAD | TOO_MANY_ROWS,
)
async def export_transactions(
    user: User = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
    kind: CategoryKind | None = Query(None, description="Filtra por receita ou despesa"),
    category_id: UUID | None = Query(None, description="Filtra por uma categoria"),
    occurred_from: date | None = Query(None, description="Competência a partir de (inclusive)"),
    occurred_to: date | None = Query(None, description="Competência até (inclusive)"),
) -> Response:
    """Os filtros de `GET /transactions`, sem paginação: acima de `MAX_ROWS`, 422."""
    report = await service.transactions(
        user,
        filters=TransactionFilters(
            kind=kind,
            category_id=category_id,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
        ),
    )
    return await _download(report)


@router.get(
    "/balance/monthly",
    summary="Saldo mês a mês em PDF",
    response_class=Response,
    responses=PDF_DOWNLOAD | BAD_PERIOD,
)
async def export_monthly_balance(
    user: User = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
    from_month: str | None = Query(None, pattern=MONTH_KEY_PATTERN, description=MONTH_QUERY),
    to_month: str | None = Query(None, pattern=MONTH_KEY_PATTERN, description=MONTH_QUERY),
) -> Response:
    report = await service.monthly_balance(
        user,
        first=parse_month(from_month) if from_month else None,
        last=parse_month(to_month) if to_month else None,
    )
    return await _download(report)


@router.get(
    "/balance/range",
    summary="Saldo de um intervalo de datas em PDF",
    response_class=Response,
    responses=PDF_DOWNLOAD | BAD_PERIOD,
)
async def export_range_balance(
    user: User = Depends(get_current_user),
    service: ReportService = Depends(get_report_service),
    occurred_from: date = Query(description="Competência a partir de (inclusive)"),
    occurred_to: date = Query(description="Competência até (inclusive)"),
) -> Response:
    report = await service.range_balance(user, first_day=occurred_from, last_day=occurred_to)
    return await _download(report)
