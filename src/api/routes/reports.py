"""Rotas de relatório: os mesmos recortes da API, em PDF.

Cada rota daqui é o par de uma rota de leitura que já existe, e aceita
**exatamente** os mesmos filtros:

- `GET /reports/transactions` espelha `GET /transactions` — `kind`,
  `category_id`, `occurred_from`, `occurred_to`;
- `GET /reports/balance/monthly` espelha `GET /balance/monthly` — `from_month`,
  `to_month`;
- `GET /reports/balance/range` espelha `GET /balance/range` — `occurred_from`,
  `occurred_to`.

Assim a tela não aprende um segundo vocabulário para exportar: o botão de baixar
manda para cá a mesma query string que ela já usou para montar a lista, e o PDF
sai do mesmo recorte. Receitas e despesas não têm rota própria — são o `kind` do
extrato, como em `GET /transactions`, e o filtro vira o título e o nome do
arquivo (`receitas-2026-01-01_2026-09-12.pdf`).

Não há `/reports/balance/current`: o mês corrente é o intervalo entre as duas
pontas que `GET /balance/current` devolve, e `/reports/balance/range` já as
aceita. Uma rota a mais para três números seria uma quarta forma de perguntar a
mesma coisa.

**A renderização sai do event loop.** Montar PDF é trabalho de CPU e o servidor
tem um loop só: uma tabela de mil linhas renderizada no loop atrasaria toda
requisição em voo. `run_in_threadpool` é a fronteira certa para isso, e é
justamente por isso que quem monta o documento (`ReportService`) devolve dados,
não bytes — serviço não conhece FastAPI.
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

PDF_DOWNLOAD: Responses = {
    200: {
        "description": "O relatório em PDF, como anexo para download",
        "content": {PDF_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
    }
}
"""Declarado à mão porque a resposta não é JSON.

Sem isto o OpenAPI anunciaria `application/json` e todo cliente gerado a partir
dele tentaria desserializar o PDF.
"""

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
    """O PDF pronto para o navegador salvar.

    Três cabeçalhos, e os três importam:

    - `Content-Disposition: attachment` é o que faz o navegador **baixar** em vez
      de abrir o arquivo dentro da aba, e é onde vai o nome do arquivo;
    - `Cache-Control: no-store` mantém o extrato fora do cache do disco — ele
      tem saldo e descrição de gasto, e o navegador guardaria isso mesmo depois
      do logout;
    - o `Content-Length`, que o próprio `Response` calcula do corpo, é o que dá
      barra de progresso em vez de um download de tamanho desconhecido.

    Para o front ler o nome do arquivo quando a API está noutra origem, o
    `Content-Disposition` precisa estar em `expose_headers` do CORS — ver
    `main.create_app`.
    """
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
    """Os mesmos filtros de `GET /transactions`, sem `limit` nem `offset`.

    Paginar um relatório não faria sentido: o arquivo é baixado inteiro, de uma
    vez. O que existe no lugar é um teto — recorte com mais de `MAX_ROWS`
    lançamentos é recusado com 422, dizendo quantos são.
    """
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
    """A tradução de `YYYY-MM` para intervalo acontece aqui, como em `/balance/monthly`."""
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
    """As duas pontas são obrigatórias, como em `GET /balance/range`."""
    report = await service.range_balance(user, first_day=occurred_from, last_day=occurred_to)
    return await _download(report)
