"""Quanto da API esta suíte realmente exercita.

A matriz de autorização e a de CRUD já reprovam o build quando uma rota nova
entra sem declaração — mas as duas falam de rotas **declaradas**. Este módulo
responde a outra pergunta, e responde sempre, inclusive quando tudo passa:
*quais endpoints a suíte chamou e viu responder com sucesso?*

Um endpoint conta como coberto quando alguma requisição desta suíte recebeu
dele uma resposta 2xx. O critério é exigente de propósito: chamar um endpoint e
receber 401 prova que a rota existe, não que ela faz o que promete — e uma
contagem que somasse os 401 diria "100%" sobre uma API que ninguém exercitou.

O resumo sai no fim da rodada e **não reprova nada**. Endpoint novo aparece na
lista dos descobertos até alguém escrever o teste: essa é a informação que
falta, e travar quem está no meio da implementação não a produz mais rápido.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

type Endpoint = tuple[str, str]
"""Método e o molde do caminho, como o OpenAPI o registra."""

_universe: list[tuple[Endpoint, re.Pattern[str]]] = []
"""Os endpoints publicados, do mais específico para o mais genérico."""

_seen: dict[Endpoint, set[int]] = {}
"""Status observados por endpoint. Chave ausente é endpoint nunca chamado."""

SUCCESS = range(200, 300)


def register(openapi: Callable[[], dict[str, Any]]) -> None:
    """Declara o universo a partir do schema OpenAPI da aplicação.

    Do schema, e não de `app.routes`: o FastAPI guarda os routers incluídos
    como objetos opacos, e varrer as rotas devolveria uma lista sem nenhum
    endpoint — um relatório que anuncia 100% sem ter olhado nada.

    Recebe a função, e não o dicionário: montar o schema custa uns 130 ms, a
    suíte cria uma aplicação por teste e todas publicam exatamente o mesmo
    contrato. Chamada uma vez, o resto da rodada não paga por ela.
    """
    if _universe:
        return
    for path, operations in openapi()["paths"].items():
        for method in operations:
            _universe.append(((method.upper(), path), _matcher(path)))
    # Caminho literal antes de caminho com parâmetro: `/users/me` casaria com
    # um `/users/{user_id}` que venha a existir, e o literal é o certo.
    _universe.sort(key=lambda entry: ("{" in entry[0][1], entry[0][1]))


def _matcher(path: str) -> re.Pattern[str]:
    """O molde do OpenAPI como expressão regular; `{id}` casa um segmento."""
    literals = [re.escape(part) for part in re.split(r"\{[^}]+\}", path)]
    return re.compile("^" + "[^/]+".join(literals) + "$")


def record(method: str, path: str, status: int) -> None:
    """Anota uma requisição. Caminho que não é de nenhum endpoint é ignorado."""
    endpoint = _resolve(method.upper(), path)
    if endpoint is not None:
        _seen.setdefault(endpoint, set()).add(status)


def _resolve(method: str, path: str) -> Endpoint | None:
    for endpoint, matcher in _universe:
        if endpoint[0] == method and matcher.match(path):
            return endpoint
    return None


@dataclass(frozen=True, slots=True)
class Coverage:
    """O retrato da rodada: o que respondeu 2xx e o que não respondeu."""

    covered: list[Endpoint]
    uncovered: list[Endpoint]

    @property
    def total(self) -> int:
        return len(self.covered) + len(self.uncovered)

    @property
    def complete(self) -> bool:
        return not self.uncovered

    @property
    def headline(self) -> str:
        share = len(self.covered) * 100 // self.total
        line = f"{len(self.covered)} de {self.total} endpoints cobertos ({share}%)"
        if self.complete:
            return line
        return f"{line} — {len(self.uncovered)} sem cobertura"

    @property
    def gaps(self) -> list[str]:
        """Uma linha por endpoint descoberto, dizendo o que a suíte viu dele."""
        lines = []
        for method, path in self.uncovered:
            status = sorted(_seen.get((method, path), set()))
            seen = ", ".join(str(code) for code in status)
            evidence = f"visto: {seen}" if seen else "nunca chamado"
            lines.append(f"  {method:<6} {path}  ({evidence})")
        return lines


def summary() -> Coverage | None:
    """O retrato da rodada, ou `None` se nenhuma requisição foi feita.

    Sem requisição não há o que relatar — é o que acontece quando um `-k`
    desmarca a suíte inteira, e um "0 de 25" ali seria só ruído.
    """
    if not _universe or not _seen:
        return None

    covered = [endpoint for endpoint, _ in _universe if _succeeded(endpoint)]
    uncovered = [endpoint for endpoint, _ in _universe if not _succeeded(endpoint)]
    return Coverage(covered=sorted(covered), uncovered=sorted(uncovered))


def _succeeded(endpoint: Endpoint) -> bool:
    return any(status in SUCCESS for status in _seen.get(endpoint, set()))
