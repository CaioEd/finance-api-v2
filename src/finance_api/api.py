"""Montagem do router raiz da API versionada.

Este é o *único* lugar do projeto que faz `include_router`. Nenhuma rota é
declarada aqui: cada domínio expõe o seu próprio `router` (e, quando existir,
`admin_router`) em `domains/<dominio>/endpoints.py`, inclusive os endpoints
agregados. Ver §2 do documento de arquitetura.
"""

from __future__ import annotations

from fastapi import APIRouter

api_router = APIRouter(prefix="/api/v1")

# Fase 1+: api_router.include_router(auth.router) etc.
