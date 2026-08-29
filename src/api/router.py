"""Router raiz da API versionada.

Único ponto do projeto que agrega rotas — e só agrega: nenhuma rota é
declarada aqui. Cada recurso tem o seu arquivo em `api/routes/`, inclusive os
endpoints agregados (saldo, relatório), que no sistema antigo moravam todos no
arquivo de rotas do projeto.
"""

from __future__ import annotations

from fastapi import APIRouter

from api.routes import auth, categories, users

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(categories.router)
