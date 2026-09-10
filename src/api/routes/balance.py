"""Rotas de saldo: mês corrente, mês a mês e intervalo de datas.

Endpoints agregados moram no arquivo do seu domínio, e não no router raiz —
`api/router.py` só agrega. Como nas demais rotas, a autenticação é declarada no
router inteiro e o escopo por dono vem do repositório: não existe aqui um `if`
comparando o dono do lançamento com quem perguntou, e nenhuma rota aceita o id
de outro usuário.

As três são **leitura pura**: nenhuma escreve, e por isso nenhuma tem `POST`.
Saldo não é um recurso que se guarde — é uma pergunta sobre os lançamentos, e
guardá-lo criaria uma segunda fonte para o mesmo fato, que é a decisão que
`models.transaction` já recusa ao derivar `kind` da categoria.

A tradução do mês `YYYY-MM` para intervalo de datas acontece aqui, na borda: o
serviço trabalha em `MonthRange`, e o formato da API não vaza para dentro dele.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query

from core.clock import MONTH_KEY_PATTERN, parse_month
from dependencies.auth import get_current_user
from dependencies.services import get_balance_service
from models.user import User
from repositories.balance_repository import Totals
from schemas.balance import BalanceOut, MonthBalanceOut, MonthlyBalanceOut, RangeBalanceOut
from services.balance_service import DEFAULT_MONTHS, MAX_MONTHS, BalanceService, MonthBalance

router = APIRouter(
    prefix="/balance",
    tags=["balance"],
    dependencies=[Depends(get_current_user)],
    responses={401: {"description": "Ausência de token, token inválido ou expirado"}},
)

# Anotadas por causa do `responses` do FastAPI, cuja chave é `int | str`.
Responses = dict[int | str, dict[str, Any]]

BAD_PERIOD: Responses = {
    422: {"description": f"Início posterior ao fim, ou mais de {MAX_MONTHS} meses de série"}
}

MONTH_QUERY = (
    "Mês de competência, no formato YYYY-MM. Ausente, a janela é de "
    f"{DEFAULT_MONTHS} meses ancorada na outra ponta"
)


def _totals_out(totals: Totals) -> BalanceOut:
    return BalanceOut(income=totals.income, expense=totals.expense, net=totals.net)


def _month_out(balance: MonthBalance) -> MonthBalanceOut:
    return MonthBalanceOut(
        month=balance.month.key,
        first_day=balance.month.first_day,
        last_day=balance.month.last_day,
        income=balance.totals.income,
        expense=balance.totals.expense,
        net=balance.totals.net,
    )


@router.get("/current", summary="Saldo do mês corrente")
async def read_current_balance(
    user: User = Depends(get_current_user),
    service: BalanceService = Depends(get_balance_service),
) -> MonthBalanceOut:
    """O mês corrente é o do `Clock`, no fuso da aplicação — não o do relógio de quem pergunta."""
    return _month_out(await service.current_month(user))


@router.get("/monthly", summary="Saldo mês a mês", responses=BAD_PERIOD)
async def read_monthly_balance(
    user: User = Depends(get_current_user),
    service: BalanceService = Depends(get_balance_service),
    from_month: str | None = Query(None, pattern=MONTH_KEY_PATTERN, description=MONTH_QUERY),
    to_month: str | None = Query(None, pattern=MONTH_KEY_PATTERN, description=MONTH_QUERY),
) -> MonthlyBalanceOut:
    """Série cronológica, inclusive nas duas pontas e sem mês faltando.

    O `pattern` recusa `2026-13` como 422 com o campo apontado, antes de
    `parse_month` ser chamado — é o mesmo motivo de `Money` repetir no schema o
    `CHECK` que o banco já impõe.
    """
    series = await service.monthly(
        user,
        first=parse_month(from_month) if from_month else None,
        last=parse_month(to_month) if to_month else None,
    )
    return MonthlyBalanceOut(
        months=[_month_out(month) for month in series.months],
        total=_totals_out(series.totals),
    )


@router.get("/range", summary="Saldo de um intervalo de datas", responses=BAD_PERIOD)
async def read_range_balance(
    user: User = Depends(get_current_user),
    service: BalanceService = Depends(get_balance_service),
    occurred_from: date = Query(description="Competência a partir de (inclusive)"),
    occurred_to: date = Query(description="Competência até (inclusive)"),
) -> RangeBalanceOut:
    """As duas pontas são obrigatórias: intervalo sem intervalo é `/balance/current`.

    Os nomes repetem os filtros de `GET /transactions` de propósito — o mesmo
    par de datas nas duas rotas devolve o saldo exatamente da lista que a outra
    mostra, e quem consome não precisa aprender dois vocabulários.
    """
    balance = await service.in_range(user, first_day=occurred_from, last_day=occurred_to)
    return RangeBalanceOut(
        first_day=balance.first_day,
        last_day=balance.last_day,
        income=balance.totals.income,
        expense=balance.totals.expense,
        net=balance.totals.net,
    )
