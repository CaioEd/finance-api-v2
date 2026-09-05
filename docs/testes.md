# Testes

Os tipos que existem, o que cada um cobre e como rodá-los.

Para **números de cobertura** e o que ainda não é testado, veja `cobertura-de-testes.md`.

## Os tipos

| Tipo | O que cobre | Onde | Testes |
|---|---|---|---|
| **Unitários** | regra de negócio e contrato de entrada. Não abrem conexão — serviço se testa com repositório falso | `tests/unit/` | 120 |
| **Contrato de domínio** | o que só aquele recurso faz: rotação de refresh, categoria do sistema vs. do usuário, filtros da listagem | `tests/integration/test_auth.py`, `test_users.py`, `test_categories.py`, `test_admin_users.py` | 82 |
| **Matriz de autorização** | **quem** alcança cada rota: anônimo, autenticado e admin × rota pública, protegida e de admin | `tests/integration/test_authorization_matrix.py` | 78 |
| **Matriz de CRUD** | **o que** cada rota faz: criar → ler → listar → atualizar → excluir, os 404/422 e o PATCH parcial | `tests/integration/test_crud_contract.py` | 57 |
| **Infraestrutura** | health, envelope de erro e o `seed-dev` | `tests/integration/test_health.py`, `test_error_envelope.py`, `test_dev_seed.py` | 14 |

Só os unitários rodam sem Docker. Todo o resto sobe a aplicação de verdade contra o Postgres do
serviço `db-test`.

As duas matrizes se comparam com o schema OpenAPI da aplicação: **rota nova sem declaração reprova
o build**. A mensagem de falha diz onde declará-la — em `PUBLIC_ROUTES` / `PROTECTED_ROUTES` /
`ADMIN_ROUTES` na de autorização, em `RESOURCES` na de CRUD.

## Rodar tudo

```bash
make check      # lint + format + mypy + a suíte — exatamente o que o CI roda
make test       # só a suíte inteira
```

O banco de teste sobe sozinho. Não existe passo de preparação em nenhum alvo.

## Rodar só um tipo

```bash
make test-unit                                             # os 120 unitários, ~0,7 s, sem Docker
make test-integration                                      # tudo que exige Postgres

.venv/bin/pytest tests/integration/test_crud_contract.py         # só a matriz de CRUD
.venv/bin/pytest tests/integration/test_authorization_matrix.py  # só a de autorização
.venv/bin/pytest tests/integration/test_categories.py            # só o domínio de categorias
```

Recortes úteis dentro de um tipo:

```bash
.venv/bin/pytest tests/integration/test_crud_contract.py -k categorias
.venv/bin/pytest tests/integration/test_crud_contract.py -k transacoes
.venv/bin/pytest tests/integration/test_crud_contract.py -k usuarios-admin

.venv/bin/pytest tests/integration/test_authorization_matrix.py -k anonymous  # só a persona
.venv/bin/pytest tests/integration/test_authorization_matrix.py -k admin
```

`categorias`, `transacoes` e `usuarios-admin` são os nomes dos recursos declarados em `RESOURCES`;
cada invariante da matriz roda uma vez para cada um.

## Rodar um teste individual

O caminho do arquivo, `::`, e o nome da função:

```bash
.venv/bin/pytest tests/unit/test_patch_schemas.py::test_a_falsy_value_is_not_confused_with_absent
```

Teste parametrizado leva o caso entre colchetes, e aí **as aspas são obrigatórias** — sem elas o
shell come os colchetes:

```bash
.venv/bin/pytest "tests/integration/test_crud_contract.py::test_patch_ignores_the_fields_that_came_as_null[categorias]"
```

Para ver os nomes disponíveis num arquivo:

```bash
.venv/bin/pytest tests/integration/test_crud_contract.py --collect-only -o addopts="" -q
```

Quando não souber onde o teste mora, filtre por nome nas duas suítes de uma vez:

```bash
.venv/bin/pytest -k patch            # 59 testes, nas duas suítes
make test-unit k=patch               # o mesmo filtro, só nos unitários
```

No Windows o executável é `.venv\Scripts\pytest`; com o venv ativo, basta `pytest`.

## Quando rodar cada um

- **Escrevendo regra pura** (serviço, schema, `Clock`, `Settings`) → `make test-unit`. Sete décimos
  de segundo, sem Docker: dá para rodar a cada salvamento.
- **Mexeu em rota, repositório, model ou migration** → `make test-integration`. Nenhum unitário
  enxerga SQL, então a suíte rápida fica verde com o schema quebrado.
- **Acrescentou uma rota** → as duas matrizes reprovam até você declará-la. É o desenho funcionando.
- **Antes de commitar ou abrir PR** → `make check`. Rodar só `make test` deixa passar erro de lint e
  de tipo, que reprovam o CI do mesmo jeito.
- **Escreveu teste novo ou módulo novo** → `make coverage` e atualize `cobertura-de-testes.md` no
  mesmo commit.

## O banco de teste

Postgres real, nunca SQLite: o desenho depende de índice parcial, agregação com `FILTER` e
`NUMERIC`. Roda em `localhost:5433`, com os dados em tmpfs, e o schema vem **das migrations** — nunca
de `create_all`, para que migration quebrada reprove o build.

Cada teste roda dentro de uma transação com rollback no fim, então nenhum enxerga o dado do outro e
a ordem de execução não importa. `make db-test` sobe só o banco, se você quiser inspecioná-lo.
