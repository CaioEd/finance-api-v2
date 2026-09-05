"""Contrato público do domínio de categorias.

`user_id` não aparece na saída: quem consome já sabe quem é (o token diz), e o
que ele precisa saber sobre a linha é se pode alterá-la — daí `is_global`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, StringConstraints

from models.category import CATEGORY_NAME_MAX_LENGTH, CategoryKind
from schemas.base import PatchIn


def _normalize_name(value: object) -> object:
    """Tira espaço das pontas e colapsa o do meio, preservando a caixa.

    A caixa é do usuário — "Contas de casa" continua assim na tela. Só a
    *unicidade* ignora a caixa, e ela é imposta pelo índice (ver
    `models.category`). Sem colapsar o espaço interno, "Plano  de saúde" e
    "Plano de saúde" passariam pelo índice como nomes diferentes.
    """
    return " ".join(value.split()) if isinstance(value, str) else value


type CategoryName = Annotated[
    str,
    BeforeValidator(_normalize_name),
    StringConstraints(min_length=2, max_length=CATEGORY_NAME_MAX_LENGTH),
]


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    kind: CategoryKind
    is_global: bool
    """Categoria do sistema: aparece para todos e não é editável."""

    created_at: datetime


class CategoryCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CategoryName
    kind: CategoryKind


class CategoryUpdateIn(PatchIn):
    """Todos os campos opcionais: é PATCH — ver `schemas.base.PatchIn`."""

    name: CategoryName | None = None
    kind: CategoryKind | None = None
