"""Configuração de coleta, comum às duas suítes.

**Nada que abra conexão mora aqui.** Um fixture `autouse` no conftest raiz vale
para a árvore inteira de `tests/`, e era exatamente isso que fazia `tests/unit`
— que não toca I/O em uma linha sequer — precisar de um Postgres de pé para
começar a rodar. Os fixtures de banco e de HTTP vivem em
`tests/integration/conftest.py`, onde alcançam só quem realmente depende deles.
"""

from __future__ import annotations

from pathlib import Path

import pytest

INTEGRATION_DIR = Path(__file__).parent / "integration"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Marca como `integration` todo teste que mora em `tests/integration/`.

    Assim `pytest -m "not integration"` de fato pula o que exige Postgres, como
    o CLAUDE.md promete, sem depender de alguém lembrar do decorador em cada
    arquivo novo — que é o tipo de esquecimento que esta suíte inteira existe
    para não permitir.
    """
    for item in items:
        if item.path.is_relative_to(INTEGRATION_DIR):
            item.add_marker("integration")
