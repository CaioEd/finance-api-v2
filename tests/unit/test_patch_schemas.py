"""O contrato de PATCH, exercitado sobre os quatro schemas que o usam.

Sem I/O: o que decide o que é gravado é resolvido antes de existir sessão de
banco. O efeito no recurso está nos testes de integração de cada domínio.

Os casos são parametrizados de propósito. O que se protege aqui não é um
domínio, é a regra comum: schema de update novo que esqueça de herdar
`PatchIn` entra nesta lista e falha na hora.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from schemas.base import PatchIn
from schemas.category import CategoryUpdateIn
from schemas.transaction import TransactionUpdateIn
from schemas.user import AdminUserUpdateIn, UserUpdateIn

UPDATE_SCHEMAS = [UserUpdateIn, AdminUserUpdateIn, CategoryUpdateIn, TransactionUpdateIn]

# Um campo de cada schema, com um valor válido, e o resto do contrato para
# preencher de nulo — é o corpo que um formulário manda quando o usuário mexeu
# num campo só.
ONE_FIELD_CASES: list[tuple[type[PatchIn], dict[str, Any], dict[str, Any]]] = [
    (
        UserUpdateIn,
        {"first_name": "Ana Paula", "email": None, "username": None, "last_name": None},
        {"first_name": "Ana Paula"},
    ),
    (
        AdminUserUpdateIn,
        {
            "is_active": False,
            "email": None,
            "username": None,
            "first_name": None,
            "last_name": None,
            "role": None,
        },
        {"is_active": False},
    ),
    (
        CategoryUpdateIn,
        {"name": "Padaria", "kind": None},
        {"name": "Padaria"},
    ),
    (
        TransactionUpdateIn,
        {
            "amount": "12.00",
            "category_id": None,
            "occurred_on": None,
            "description": None,
        },
        {"amount": Decimal("12.00")},
    ),
]


@pytest.mark.parametrize("schema", UPDATE_SCHEMAS)
def test_every_update_schema_follows_the_patch_contract(schema: type[PatchIn]) -> None:
    assert issubclass(schema, PatchIn)


@pytest.mark.parametrize(("schema", "payload", "esperado"), ONE_FIELD_CASES)
def test_one_field_sent_amid_nulls_changes_only_that_field(
    schema: type[PatchIn], payload: dict[str, Any], esperado: dict[str, Any]
) -> None:
    """O caso que quebrava: nulo virava valor a gravar, e a coluna é NOT NULL."""
    assert schema.model_validate(payload).changes() == esperado


@pytest.mark.parametrize("schema", UPDATE_SCHEMAS)
def test_an_empty_body_changes_nothing(schema: type[PatchIn]) -> None:
    assert schema().changes() == {}


@pytest.mark.parametrize(("schema", "payload", "_esperado"), ONE_FIELD_CASES)
def test_a_body_of_only_nulls_changes_nothing(
    schema: type[PatchIn], payload: dict[str, Any], _esperado: dict[str, Any]
) -> None:
    todos_nulos = dict.fromkeys(payload)

    assert schema.model_validate(todos_nulos).changes() == {}


@pytest.mark.parametrize("schema", UPDATE_SCHEMAS)
def test_an_unknown_field_is_rejected(schema: type[PatchIn]) -> None:
    """`extra="forbid"` é herdado: descartar em silêncio faria o 200 mentir."""
    with pytest.raises(ValidationError):
        schema.model_validate({"campo_que_nao_existe": "x"})


def test_a_full_body_still_changes_everything() -> None:
    """O outro extremo do pedido: mandar tudo continua atualizando tudo."""
    data = TransactionUpdateIn.model_validate(
        {
            "amount": "99.90",
            "category_id": (category_id := uuid4()),
            "occurred_on": "2026-09-03",
            "description": "Feira",
        }
    )

    assert data.changes() == {
        "amount": Decimal("99.90"),
        "category_id": category_id,
        "occurred_on": date(2026, 9, 3),
        "description": "Feira",
    }


def test_a_falsy_value_is_not_confused_with_absent() -> None:
    """`False` e `""` são valores; só `None` significa "não mexa"."""
    assert AdminUserUpdateIn(is_active=False).changes() == {"is_active": False}
    assert UserUpdateIn(last_name="").changes() == {"last_name": ""}
    assert TransactionUpdateIn(description="").changes() == {"description": ""}
