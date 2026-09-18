"""Rotas de investimentos.

A autenticação é declarada no router inteiro, não em cada rota. O escopo por
dono vem do repositório: não existe aqui um `if` comparando o dono da posição
com quem pediu, e nenhuma rota aceita o id de outro usuário.

`/summary` é declarada **antes** de `/{investment_id}`: o FastAPI casa as rotas
na ordem de declaração, e a ordem inversa faria "summary" chegar ao parâmetro
como um UUID malformado — 422 no lugar do resumo da carteira.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from core.errors import NotSearchableInvestmentTypeError
from dependencies.auth import get_current_user
from dependencies.services import get_investment_service
from dependencies.state import get_market_service
from models.investment import VARIABLE_INCOME_TYPES, InvestmentClass, InvestmentType
from models.user import User
from repositories.investment_repository import InvestmentFilters
from schemas.investment import (
    AllocationOut,
    AssetSearchOut,
    ContributionIn,
    EarningIn,
    InvestmentCreateIn,
    InvestmentMovementOut,
    InvestmentOut,
    InvestmentPageOut,
    InvestmentSummaryOut,
    InvestmentUpdateIn,
)
from schemas.transaction import TransactionOut
from services.investment_service import Allocation, InvestmentService, Movement
from services.market_service import SEARCH_LIMIT, MarketService

router = APIRouter(
    prefix="/investments",
    tags=["investments"],
    dependencies=[Depends(get_current_user)],
    responses={401: {"description": "Ausência de token, token inválido ou expirado"}},
)

# Anotadas por causa do `responses` do FastAPI, cuja chave é `int | str`.
Responses = dict[int | str, dict[str, Any]]

NOT_FOUND: Responses = {404: {"description": "Investimento inexistente ou de outro usuário"}}
BAD_FIELDS: Responses = {422: {"description": "Campos incompatíveis com o tipo do investimento"}}
BAD_CATEGORY: Responses = {
    422: {"description": "Categoria inexistente, de outro usuário ou do tipo errado"}
}
NOT_SEARCHABLE: Responses = {
    422: {"description": "Tipo de renda fixa: CDB e Tesouro não têm símbolo a pesquisar"}
}
PROVIDER_DOWN: Responses = {503: {"description": "Provedor de cotações indisponível"}}


@router.get("", summary="Lista os investimentos do próprio usuário")
async def list_investments(
    user: User = Depends(get_current_user),
    service: InvestmentService = Depends(get_investment_service),
    limit: int = Query(50, ge=1, le=100, description="Tamanho da página"),
    offset: int = Query(0, ge=0),
    investment_class: InvestmentClass | None = Query(
        None, alias="class", description="Filtra por renda fixa ou variável"
    ),
    investment_type: InvestmentType | None = Query(
        None, alias="type", description="Filtra por um tipo"
    ),
) -> InvestmentPageOut:
    page = await service.list_investments(
        user,
        filters=InvestmentFilters(investment_class=investment_class, type=investment_type),
        limit=limit,
        offset=offset,
    )
    return InvestmentPageOut(
        items=[InvestmentOut.model_validate(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Cadastra uma posição de renda fixa ou variável",
    responses=BAD_FIELDS,
)
async def create_investment(
    data: InvestmentCreateIn,
    user: User = Depends(get_current_user),
    service: InvestmentService = Depends(get_investment_service),
) -> InvestmentOut:
    """Cadastrar **não** lança nada: declara uma posição que já existe.

    A compra feita há dois anos não pode cair como despesa do mês corrente.
    Para registrar dinheiro saindo da conta agora, use `/contributions`.
    """
    investment = await service.create(user, data)
    return InvestmentOut.model_validate(investment)


@router.get("/summary", summary="Resumo da carteira: patrimônio, lucro e alocação")
async def read_summary(
    user: User = Depends(get_current_user),
    service: InvestmentService = Depends(get_investment_service),
) -> InvestmentSummaryOut:
    summary = await service.summary(user)
    return InvestmentSummaryOut(
        total_value=summary.total_value,
        total_invested=summary.total_invested,
        profit=summary.profit,
        profit_percent=summary.profit_percent,
        positions=summary.positions,
        by_class=[_allocation_out(share) for share in summary.by_class],
        by_type=[_allocation_out(share) for share in summary.by_type],
        value_updated_at=summary.value_updated_at,
    )


@router.get(
    "/assets",
    summary="Pesquisa ativos de renda variável no provedor do tipo escolhido",
    responses=NOT_SEARCHABLE | PROVIDER_DOWN,
)
async def search_assets(
    _: User = Depends(get_current_user),
    market: MarketService = Depends(get_market_service),
    investment_type: InvestmentType = Query(
        alias="type", description="Só os três de renda variável"
    ),
    query: str = Query(
        "", max_length=60, description="Código ou nome; vazio lista as criptomoedas"
    ),
    limit: int = Query(SEARCH_LIMIT, ge=1, le=25),
) -> list[AssetSearchOut]:
    """O que se escolhe aqui é o `symbol` de um `POST /investments`.

    Cripto é lista fechada de nove moedas e não consulta provedor nenhum; ação
    brasileira vai à BRAPI e americana à Twelve Data. Provedor sem credencial
    configurada devolve lista vazia, e não erro.
    """
    if investment_type not in VARIABLE_INCOME_TYPES:
        raise NotSearchableInvestmentTypeError()
    hits = await market.search_assets(investment_type, query, limit=limit)
    return [
        AssetSearchOut(
            type=investment_type,
            symbol=hit.symbol,
            name=hit.name,
            currency=hit.currency,
            exchange=hit.exchange,
            price=hit.price,
            logo_url=hit.logo_url,
        )
        for hit in hits
    ]


@router.get("/{investment_id}", summary="Detalha um investimento", responses=NOT_FOUND)
async def read_investment(
    investment_id: UUID,
    user: User = Depends(get_current_user),
    service: InvestmentService = Depends(get_investment_service),
) -> InvestmentOut:
    investment = await service.get(user, investment_id)
    return InvestmentOut.model_validate(investment)


@router.patch(
    "/{investment_id}",
    summary="Atualiza um investimento do próprio usuário",
    responses=NOT_FOUND | BAD_FIELDS,
)
async def update_investment(
    investment_id: UUID,
    data: InvestmentUpdateIn,
    user: User = Depends(get_current_user),
    service: InvestmentService = Depends(get_investment_service),
) -> InvestmentOut:
    investment = await service.update(user, investment_id, data)
    return InvestmentOut.model_validate(investment)


@router.delete(
    "/{investment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Exclui um investimento do próprio usuário",
    responses=NOT_FOUND,
)
async def delete_investment(
    investment_id: UUID,
    user: User = Depends(get_current_user),
    service: InvestmentService = Depends(get_investment_service),
) -> Response:
    """Os aportes e proventos já lançados permanecem, com `investment_id` nulo."""
    await service.delete(user, investment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{investment_id}/contributions",
    status_code=status.HTTP_201_CREATED,
    summary="Aporta: grava a despesa e aumenta a posição",
    responses=NOT_FOUND | BAD_CATEGORY,
)
async def contribute(
    investment_id: UUID,
    data: ContributionIn,
    user: User = Depends(get_current_user),
    service: InvestmentService = Depends(get_investment_service),
) -> InvestmentMovementOut:
    """A categoria precisa ser de **despesa**: o dinheiro está saindo da conta."""
    return _movement_out(await service.contribute(user, investment_id, data))


@router.post(
    "/{investment_id}/earnings",
    status_code=status.HTTP_201_CREATED,
    summary="Registra um provento: grava a receita, sem mexer na posição",
    responses=NOT_FOUND | BAD_CATEGORY,
)
async def register_earning(
    investment_id: UUID,
    data: EarningIn,
    user: User = Depends(get_current_user),
    service: InvestmentService = Depends(get_investment_service),
) -> InvestmentMovementOut:
    """A categoria precisa ser de **receita**: o dividendo caiu na conta.

    Reinvestir é um aporte, e é uma segunda chamada de propósito — o dinheiro
    entrou e depois saiu, e os dois fatos aconteceram.
    """
    return _movement_out(await service.register_earning(user, investment_id, data))


def _allocation_out(share: Allocation) -> AllocationOut:
    return AllocationOut(
        label=share.label,
        value=share.value,
        invested=share.invested,
        percent=share.percent,
        count=share.count,
    )


def _movement_out(movement: Movement) -> InvestmentMovementOut:
    return InvestmentMovementOut(
        investment=InvestmentOut.model_validate(movement.investment),
        transaction=TransactionOut.model_validate(movement.transaction),
    )
