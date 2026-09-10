# Testes

Os tipos que existem, o que cada um cobre e como rodá-los.

Para **números de cobertura** e o que ainda não é testado, veja `cobertura-de-testes.md`.

## Os tipos

| Tipo | O que cobre | Onde | Testes | Postgres |
|---|---|---|---|---|
| **Unitários** | regra de negócio e contrato de entrada. Não abrem conexão — serviço se testa com repositório falso | `tests/unit/` | 146 | não |
| **Matriz de autorização** | **quem** alcança cada rota: anônimo, autenticado e admin × rota pública, protegida e de admin | `tests/api/test_authorization_matrix.py` | 90 | não |
| **Matriz de CRUD** | **o que** cada rota faz: criar → ler → listar → atualizar → excluir, os 404/422 e o PATCH parcial | `tests/api/test_crud_contract.py` | 57 | não |
| **Tradução do SQLite** | o agrupamento mensal do saldo, única consulta que depende de uma função traduzida à mão | `tests/api/test_balance.py` | 4 | não |
| **Operacionais** | liveness e readiness | `tests/api/test_health.py` | 2 | não |
| **Contrato de domínio** | o que só aquele recurso faz: rotação de refresh, categoria do sistema vs. do usuário, o saldo agregado e o seu escopo por dono | `tests/integration/test_auth.py`, `test_users.py`, `test_categories.py`, `test_admin_users.py`, `test_balance.py` | 107 | sim |
| **Infraestrutura** | migrations, envelope de erro, health contra o banco real e o `seed-dev` | `tests/integration/test_health.py`, `test_error_envelope.py`, `test_dev_seed.py` | 14 | sim |

Os cinco primeiros tipos — 299 dos 420 testes — rodam **sem Docker e sem banco nenhum**. Só o que
depende do Postgres de verdade (migrations, dado semeado por migration, `NUMERIC`, índice parcial e
o `date_trunc` do saldo mensal) sobe o serviço `db-test`.

As duas matrizes se comparam com o schema OpenAPI da aplicação: **rota nova sem declaração reprova
o build**. A mensagem de falha diz onde declará-la — em `PUBLIC_ROUTES` / `PROTECTED_ROUTES` /
`ADMIN_ROUTES` na de autorização, em `RESOURCES` na de CRUD.

## A suíte de API

`tests/api/` sobe a aplicação inteira com o `TestClient` do FastAPI contra um **SQLite em memória**,
criado e destruído a cada teste. Ela existe para que o contrato HTTP — rota, status, envelope de
erro, forma da resposta, escopo por dono — possa ser verificado numa máquina sem Docker.

```bash
make test-api                                 # os 153 testes, ~15 s, sem Docker nem banco
make test-api k=categorias                    # um recorte; vai direto para o -k do pytest
.venv/bin/pytest tests/api                    # o mesmo, chamando o pytest na mão
```

No fim da rodada ela imprime **quantos endpoints a suíte exercitou e quantos ficaram sem
cobertura** — um endpoint só conta como coberto quando alguma requisição recebeu dele um 2xx:

```
------------------------ cobertura de endpoints da API -------------------------
27 de 27 endpoints cobertos (100%)
```

Endpoint novo entra nessa lista como lacuna até alguém escrever o teste. O relatório não reprova a
rodada; o passo a passo para fechar a lacuna está na skill `endpoint-novo`
(`.claude/skills/endpoint-novo/`).

O SQLite é uma **tradução** do schema, não o banco de produção — `tests/api/sqlite_backend.py`
documenta as cinco diferenças (`gen_random_uuid()`, índice parcial, `TIMESTAMPTZ`, `date_trunc` e o
`CAST` que o acompanha) e o que elas custam. Duas delas são código escrito à mão, e por isso têm
teste próprio em `tests/api/test_balance.py`. O que depende do Postgres de verdade continua em
`tests/integration/`.

## Rodar tudo

```bash
make check      # lint + format + mypy + a suíte — exatamente o que o CI roda
make test       # só a suíte inteira
```

O banco de teste sobe sozinho. Não existe passo de preparação em nenhum alvo.

## Rodar só um tipo

```bash
make test-unit                                      # os 146 unitários, ~1 s, sem Docker
make test-api                                       # os 153 de API, ~15 s, sem Docker
make test-integration                               # tudo que exige Postgres

.venv/bin/pytest -m "not integration"               # unitários + API: tudo que dispensa banco

.venv/bin/pytest tests/api/test_crud_contract.py          # só a matriz de CRUD
.venv/bin/pytest tests/api/test_authorization_matrix.py   # só a de autorização
.venv/bin/pytest tests/integration/test_categories.py     # só o domínio de categorias
```

Recortes úteis dentro de um tipo:

```bash
.venv/bin/pytest tests/api/test_crud_contract.py -k categorias
.venv/bin/pytest tests/api/test_crud_contract.py -k transacoes
.venv/bin/pytest tests/api/test_crud_contract.py -k usuarios-admin

.venv/bin/pytest tests/api/test_authorization_matrix.py -k anonymous  # só a persona
.venv/bin/pytest tests/api/test_authorization_matrix.py -k admin
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
.venv/bin/pytest "tests/api/test_crud_contract.py::test_patch_ignores_the_fields_that_came_as_null[categorias]"
```

Para ver os nomes disponíveis num arquivo:

```bash
.venv/bin/pytest tests/api/test_crud_contract.py --collect-only -o addopts="" -q
```

Quando não souber onde o teste mora, filtre por nome nas três suítes de uma vez:

```bash
.venv/bin/pytest -k patch            # nas três suítes de uma vez
make test-unit k=patch               # o mesmo filtro, só nos unitários
make test-api k=patch                # o mesmo filtro, só na suíte de API
```

No Windows o executável é `.venv\Scripts\pytest`; com o venv ativo, basta `pytest`.

## Quando rodar cada um

- **Escrevendo regra pura** (serviço, schema, `Clock`, `Settings`) → `make test-unit`. Sete décimos
  de segundo, sem Docker: dá para rodar a cada salvamento.
- **Mexeu em rota, endpoint ou contrato de entrada e saída** → `make test-api`. Treze segundos,
  sem Docker: é a suíte que exercita a API inteira.
- **Mexeu em repositório, model ou migration** → `make test-integration`. Nem o unitário nem a suíte
  de API enxergam o schema do Postgres, e as duas ficam verdes com uma migration quebrada.
- **Acrescentou uma rota** → as duas matrizes reprovam até você declará-la, e o relatório de
  cobertura de endpoints a lista como lacuna até ela ganhar um teste. É o desenho funcionando; a
  skill `endpoint-novo` tem o passo a passo.
- **Antes de commitar ou abrir PR** → `make check`. Rodar só `make test` deixa passar erro de lint e
  de tipo, que reprovam o CI do mesmo jeito.
- **Escreveu teste novo ou módulo novo** → `make coverage` e atualize `cobertura-de-testes.md` no
  mesmo commit.

## O banco de teste

**Postgres real** para `tests/integration/`: o desenho depende de índice parcial, `NUMERIC` e
`date_trunc`, e é ali que migration quebrada precisa reprovar o build. Roda em `localhost:5433`, com
os dados em tmpfs, e o schema vem **das migrations** — nunca de `create_all`.

**SQLite em memória** para `tests/api/`, criado do `Base.metadata` e jogado fora a cada teste. É
uma tradução deliberada e limitada do schema, boa o bastante para o contrato HTTP e explicitamente
insuficiente para o resto — ver `tests/api/sqlite_backend.py`.

Cada teste roda dentro de uma transação com rollback no fim, então nenhum enxerga o dado do outro e
a ordem de execução não importa. `make db-test` sobe só o banco, se você quiser inspecioná-lo.
