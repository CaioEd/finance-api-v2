"""O cliente HTTP da suíte de API.

`TestClient` em vez do `httpx.AsyncClient` da suíte de integração: ele fala com
a aplicação por dentro, sem socket, e o teste fica síncrono — sem `await` em
cada chamada e sem um event loop declarado a cada arquivo.

Ele guarda duas coisas que a suíte precisa e o `TestClient` cru não oferece:

- **o registro de cobertura.** Toda requisição passa por `request()`, então
  nenhum endpoint é exercitado sem entrar na conta de `endpoint_coverage` —
  não existe lista de rotas testadas para alguém esquecer de atualizar.
- **acesso ao banco no event loop certo.** O `TestClient` roda a aplicação num
  portal (uma thread com o seu próprio loop), e o banco em memória vive dentro
  de uma conexão só. Abrir sessão de fora desse loop é usar uma conexão de um
  loop em outro — `in_the_database` empresta o portal para que o teste escreva
  no mesmo lugar em que a aplicação lê.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import Database
from tests.api import endpoint_coverage

T = TypeVar("T")

__all__ = ["ApiClient", "Response"]
"""`Response` é reexportado de propósito.

O `TestClient` do Starlette fala httpx2, e é o tipo dele que volta de cada
chamada. Reexportar aqui evita que todo arquivo de teste precise saber com qual
cliente HTTP o Starlette foi construído.
"""


class ApiClient(TestClient):
    """`TestClient` que se lembra do que chamou e alcança o banco da aplicação."""

    def __init__(self, app: FastAPI, database: Database) -> None:
        # `follow_redirects=False` (o `TestClient` segue por padrão): um 307 de
        # barra final seguido em silêncio esconderia justamente a mudança de
        # rota que a matriz de autorização existe para detectar.
        super().__init__(app, follow_redirects=False)
        self._database = database

    def request(self, method: str, url: Any, **kwargs: Any) -> Response:
        response = super().request(method, url, **kwargs)
        # A requisição de verdade, e não o `url` recebido: é ela que já teve os
        # `params` embutidos e a query separada do caminho.
        sent = response.request
        endpoint_coverage.record(sent.method, sent.url.path, response.status_code)
        return response

    def in_the_database(self, work: Callable[[AsyncSession], Awaitable[T]]) -> T:
        """Roda um trecho de trabalho no banco da aplicação e comita.

        Para o que não tem rota: promover alguém a administrador, por exemplo,
        que de propósito não se faz por endpoint nenhum.
        """

        async def run() -> T:
            async with self._database.sessionmaker() as session:
                result = await work(session)
                await session.commit()
                return result

        return self.portal.call(run)
