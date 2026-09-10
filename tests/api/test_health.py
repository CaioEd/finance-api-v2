"""Os dois endpoints operacionais, que ficam fora de `/api/v1`.

Não são contrato de produto — são o que o orquestrador consulta para decidir se
o container entrou no ar. Estão aqui, e não só em `tests/integration/`, porque
"todos os endpoints" inclui os dois: sem eles a contagem de cobertura desta
suíte nunca fecharia, e a lacuna seria permanente em vez de informativa.

O readiness passa pelo `Database` de verdade (ver `sqlite_backend.SqliteDatabase`),
então o que ele mede aqui é o mesmo `SELECT 1` que mede em produção — só que
contra o SQLite da suíte.
"""

from __future__ import annotations

from tests.api.client import ApiClient
from version import __version__


def test_health_reports_version_and_environment(client: ApiClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__, "environment": "test"}


def test_readiness_reaches_the_database(client: ApiClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
