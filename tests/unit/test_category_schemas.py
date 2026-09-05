"""Normalização e limites do nome de categoria.

Sem I/O: o que se testa aqui é o contrato de entrada, e ele é resolvido antes
de qualquer sessão de banco existir. A unicidade que depende do índice está em
`tests/integration/test_categories.py`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.category import CategoryCreateIn, CategoryUpdateIn


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("  Padaria  ", "Padaria"),
        ("Plano   de saúde", "Plano de saúde"),
        ("\tContas\nde casa ", "Contas de casa"),
        ("Padaria", "Padaria"),
    ],
)
def test_the_name_loses_the_extra_whitespace(entrada: str, esperado: str) -> None:
    assert CategoryCreateIn(name=entrada, kind="expense").name == esperado


def test_the_case_is_preserved() -> None:
    """Só a unicidade ignora a caixa; o que a pessoa digitou é o que ela vê."""
    assert CategoryCreateIn(name="Contas de CASA", kind="expense").name == "Contas de CASA"


@pytest.mark.parametrize("name", ["", "   ", "a", "x" * 61])
def test_a_name_out_of_bounds_is_rejected(name: str) -> None:
    with pytest.raises(ValidationError):
        CategoryCreateIn(name=name, kind="expense")


def test_an_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CategoryCreateIn(name="Padaria", kind="transferencia")  # type: ignore[arg-type]


def test_unknown_fields_are_rejected() -> None:
    """`user_id` e `is_global` são do servidor, não do cliente."""
    with pytest.raises(ValidationError):
        CategoryCreateIn(name="Padaria", kind="expense", user_id="alguem")  # type: ignore[call-arg]


def test_the_update_leaves_out_what_was_not_sent() -> None:
    data = CategoryUpdateIn(name="Padaria")

    assert data.changes() == {"name": "Padaria"}


def test_the_update_leaves_out_what_came_as_null() -> None:
    """Nulo é "não mexa", igual a ausente — ver `schemas.base.PatchIn`."""
    data = CategoryUpdateIn(name="Padaria", kind=None)

    assert data.changes() == {"name": "Padaria"}
