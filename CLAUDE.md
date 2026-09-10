# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

API REST de finanças pessoais em FastAPI + SQLAlchemy async + Postgres. O código, os
comentários e os docstrings são em **português** — siga a língua ao escrever código novo.

## Comandos

```bash
make up                     # tudo no Docker: banco + API, migrada e semeada
make db                     # só o Postgres de desenvolvimento (localhost:5432)
make api                    # só a API, local, com reload — sobe o db e migra antes
make down / logs            # derruba os containers / segue o log da api

make migrate                # alembic upgrade head
make migrate-status         # alembic current + heads
make migrate-down           # alembic downgrade -1
make revision m="mensagem"  # migra, autogenerate, ruff nos arquivos gerados

make check                  # lint + typecheck + testes — exatamente o que o CI roda
make fmt                    # ruff check --fix + ruff format
make test                   # sobe o db-test e roda a suíte inteira
make test-unit              # só os unitários; sem banco. Um domínio: make test-unit k=admin
make test-api               # só a suíte de API: TestClient + SQLite em memória, sem Docker
make test-integration       # só o que exige Postgres
make coverage               # mede a cobertura e grava .cache/coverage.json
make install                # recria o venv; raramente necessário na mão (ver abaixo)
```

`make help` lista tudo, agrupado pelos mesmos assuntos. **Nenhum alvo tem passo de preparação**:

- **`.env`** nasce do `.env.example` com uma `JWT_SECRET_KEY` gerada, na primeira vez que um alvo
  precisar dele. A regra não declara pré-requisito de propósito — assim o make só a executa quando
  o arquivo não existe, e nunca por cima de um `.env` já ajustado.
- **`.venv`** se reinstala sozinho: os alvos que usam o venv dependem de `.venv/.install-stamp`, que
  depende do `pyproject.toml`. Mexeu nas dependências, o próximo `make test` reinstala. Por isso
  `make install` deixou de ser passo obrigatório.
- **O banco sobe sozinho** para quem precisa dele: `migrate`, `seed` e `api` dependem de `db`;
  `test`, `test-integration` e `coverage` dependem de `db-test`. Os dois usam `--wait`, então quando
  a receita seguinte começa o Postgres já aceita conexão.

Os alvos funcionam em Linux, macOS e Windows — nenhum depende de utilitário de shell Unix, e
`$(PY)` resolve `bin/` ou `Scripts/` conforme o venv que existe. Receita nova segue a regra: só
`docker ...`, `$(PY) -m <módulo>` ou `$(PYTHON) -c "<script>"`; nada de `rm`, `find`, `grep` ou
comentário `#` dentro da receita (cmd.exe não conhece nenhum dos quatro).

Um teste só (o venv precisa estar ativo ou use o caminho completo — no Windows,
`.venv\Scripts\pytest`):

```bash
.venv/bin/pytest tests/unit/test_clock.py::test_current_month_follows_the_local_date
.venv/bin/pytest -m "not integration"        # pula o que exige Postgres
.venv/bin/pytest tests/unit -x -q
```

São **três** suítes, e duas delas não tocam em banco externo:

- `tests/unit/` — regra pura, sem I/O.
- `tests/api/` — a aplicação inteira pelo `TestClient`, contra um **SQLite em memória** criado do
  `Base.metadata` e destruído a cada teste. É onde mora o contrato HTTP: as duas matrizes (quem
  alcança cada rota, o que cada rota faz) e a checagem de que todo endpoint é exercitado. No fim da
  rodada ela imprime quantos endpoints receberam um 2xx e quantos ficaram sem cobertura.
  `tests/api/sqlite_backend.py` documenta as cinco traduções de schema e os seus limites; as que
  são código escrito à mão têm teste próprio em `tests/api/test_balance.py`.
- `tests/integration/` — **Postgres real** (serviço `db-test`, porta 5433, dados em tmpfs). Fica
  aqui tudo que o SQLite não reproduz, e é por isso que esta suíte não some: migrations (lá o
  schema nasce delas), as categorias do sistema semeadas por migration, índice parcial, `NUMERIC` e
  as funções de data (`date_trunc`, que o saldo mensal usa). `make db-test` sobe só esse banco. A URL vem de `TEST_DATABASE_URL`, cujo
  default mora em `tests/integration/conftest.py` — junto de todos os fixtures que abrem conexão. O
  conftest raiz não pode ter nenhum, sob pena de a suíte unitária voltar a exigir banco.

**Endpoint novo entra com teste de API junto.** As duas matrizes reprovam o build até a rota ser
declarada, e o relatório de cobertura a lista como lacuna até ela receber um 2xx. O passo a passo
está na skill `endpoint-novo` (`.claude/skills/endpoint-novo/`).

## Arquitetura

O documento de referência é `docs/finance-api-v2-architecture.md` — ele explica *por que* cada
decisão é o que é, e o `README.md` tem a tabela de fases (0 concluída; 1 em andamento).
Quando algo no código contrariar o documento, o documento é a intenção.

### Layout de import

`src/` **é a raiz de import, não um pacote**. O build (hatchling `sources = ["src"]`) remove o
prefixo, então os módulos ficam no topo:

```python
from core.config import get_settings     # correto
from src.core.config import ...          # errado
from finance_api.core.config import ...  # errado (nome antigo, já removido)
```

Ao criar um módulo de topo novo, acrescente-o a `known-first-party` no `pyproject.toml`, ou o
isort do ruff o classifica como third-party e o CI quebra.

### Fábrica de aplicação, não `app` de módulo

`src/main.py` exporta apenas `create_app(settings)`. Não existe `app` global: importar o módulo
não pode resolver configuração nem abrir conexão. O servidor roda com
`uvicorn main:create_app --factory`.

Objetos de processo (`settings`, `Database`, e — na fase 1 — `Clock`, `PasswordHasher`,
`TokenCodec`) moram em `app.state` e chegam aos endpoints pelas dependências de
`core/dependencies.py`. Nada de singleton de módulo.

> Ponto de atenção da fase 1 em andamento: `core/dependencies.py` já lê `app.state.clock`,
> `app.state.password_hasher` e `app.state.token_codec`, mas `create_app` ainda não os popula.
> Quem for ligar auth precisa fechar essa ponta.

### Camadas

Organização **por domínio** (`src/domains/<dominio>/`), com o mesmo esquema de arquivos em cada um:

| Camada | Faz | Nunca faz |
|---|---|---|
| `endpoints.py` | traduz HTTP ↔ domínio | SQL, regra, `if role ==` |
| `schemas.py` | contrato Pydantic de entrada/saída | expor o model do ORM |
| `models.py` | mapeamento e constraints | regra de negócio |
| `repositories.py` | **toda** a construção de query; impõe o escopo por usuário | levantar `HTTPException` |
| `services.py` | regra, orquestração, transação; levanta `DomainError` | conhecer `Request`/`Response` |
| `core/` | infra transversal | conhecer domínio específico |

- `src/api.py` é o **único** lugar que faz `include_router`; ele não declara rota nenhuma.
  Endpoint agregado (`/balance/monthly`) mora no `endpoints.py` do seu domínio.
- **Não existe pacote `shared/`**, e não deve passar a existir: infra vai para `core/`, tipo de
  domínio vai para o domínio dono do conceito.
- Exceção de camada deliberada: `core/dependencies.py` importa `domains.users.models` — autenticar
  é carregar um usuário, e abstrair isso só moveria o acoplamento.

### PATCH atualiza só o que foi escolhido

Todo schema de entrada de PATCH herda de `schemas.base.PatchIn`, e o serviço aplica `data.changes()`
— nunca um `model_dump(exclude_unset=True)` escrito na mão. **Campo ausente e campo nulo significam
a mesma coisa: não mexa.** Quem monta um PATCH quase nunca o faz à mão: formulário, "Try it out" do
Swagger e cliente gerado do OpenAPI mandam `null` no que o usuário não preencheu, e gravar esse
`null` violaria o `NOT NULL` da coluna — a resposta saía como `409 conflict`, obrigando a reenviar
o cadastro inteiro para trocar um campo.

A equivalência vale enquanto nenhuma coluna editável for anulável. Coluna nova em que `null` queira
dizer "limpe este campo" precisa de um sentinela que separe "ausente" de "nulo"; não dá para
expressar isso com `None`.

### Erros

Serviços levantam `DomainError` (`core/errors.py`); ninguém abaixo da camada HTTP conhece
`HTTPException`. Os handlers registrados em `register_exception_handlers` garantem que **toda**
resposta de erro — inclusive `422` do Pydantic e `404`/`405` do Starlette — saia como
`{"error": {"code", "message", "details"}}`. Erro novo = subclasse nova com `status_code`,
`code` e `message`, não um `raise HTTPException`.

Duas regras de contrato que vêm do desenho: recurso de terceiro responde **404, não 403** (403
confirmaria a existência), e credencial inválida tem **mensagem única** para e-mail inexistente e
senha errada, com `dummy_verify()` para equalizar o tempo de resposta.

Única resposta fora do envelope: `TrustedHostMiddleware` roda antes do roteamento e rejeita `Host`
desconhecido em texto puro. É intencional e está comentado em `main.py`.

### Tempo e dinheiro

- **"Hoje" vem sempre de `core.clock.Clock`**, nunca de `date.today()`. O relógio recebe o instante
  por parâmetro (`instant`), então teste fixa o tempo passando uma função — sem biblioteca de
  freeze. Ruff bloqueia datetime naive (`DTZ`).
- Competência é `date` no fuso da aplicação (`APP_TIMEZONE`); instantes são `TIMESTAMPTZ` em UTC.
  `TimestampMixin` (`core/database.py`) dá `created_at`/`updated_at`; a data do fato é coluna
  própria do domínio.
- Mês na API é string `YYYY-MM`, não data.
- Valor é `NUMERIC(14,2)` com `CHECK (amount > 0)`, `Decimal` de ponta a ponta e **string no JSON**.

### Autenticação

Access token é JWT de 15 min, sem estado e sem lista de revogação; carrega só `sub`, `role`,
`jti`, `iat`, `exp`, `typ`. Refresh é **opaco** (`token_urlsafe(32)`), gravado apenas como SHA-256,
rotacionado a cada uso e agrupado por `family_id` — um refresh já revogado reaparecendo derruba a
família inteira (`AuthService.refresh`).

Autenticação é o **default**: cada domínio cria `APIRouter(dependencies=[Depends(get_current_user)])`
e autorização usa `require_role(...)` **no router inteiro**, nunca rota a rota — assim esquecer a
declaração fecha a rota em vez de abrir. Escopo por usuário é imposto **no repositório**, não no
endpoint.

### Configuração

`core/config.py` é a única fonte. `ENVIRONMENT`, `APP_TIMEZONE`, `DATABASE_URL` e `JWT_SECRET_KEY`
**não têm default** — a aplicação recusa subir sem eles. `Settings` é `frozen`, resolvida por
`get_settings()` com `lru_cache` (nunca no import). Listas vêm do ambiente como CSV via `CsvList`,
não JSON. Variável nova entra também no `.env.example`.

### Migrations

Migrations são a fonte da verdade do schema — **nunca `create_all`**, nem em teste. Ao criar um
domínio com model:

1. crie o pacote em `src/domains/`;
2. importe o `models` do domínio em `alembic/env.py` (há um comentário marcando o lugar), senão o
   autogenerate não enxerga a tabela;
3. `make revision m="..."` e revise o arquivo gerado;
4. `api_router.include_router(...)` em `src/api.py`.

A convenção de nomes de constraint vive em `core/database.py` (`NAMING_CONVENTION`) e o código
depende dela: `AuthService._conflict_from` distingue e-mail de username duplicado pelo nome da
constraint (`uq_users_username`).

## Qualidade

`ruff check` + `ruff format --check` + `mypy --strict` sobre `src/` + `pytest` — tudo no CI
(`.github/workflows/ci.yml`), tudo em `make check`. Linha de 100 colunas, `from __future__ import
annotations` no topo de todo módulo, caches de ferramenta em `.cache/`.

Testes de integração usam `httpx.AsyncClient` com `ASGITransport` contra a app real (o fixture
`app` entra no `lifespan_context` à mão, senão `app.state.database` não existe); os de API usam o
`TestClient`, que sobe o lifespan sozinho e roda tudo no event loop do seu portal. Testes unitários
não tocam I/O: serviço se testa com repositório *fake* implementando um `Protocol`, não com mock de
SQLAlchemy. Isso é garantido, não combinado: um fixture em `tests/unit/conftest.py`
transforma qualquer tentativa de conectar no Postgres em falha de teste.

**A cobertura anda junto com o código.** Teste novo ou módulo novo pedem `make coverage` e a
atualização de `docs/cobertura-de-testes.md` **no mesmo commit** — os três blocos: a tabela de topo,
a tabela por módulo e as duas listas de lacunas. Lacuna que virou teste sai da lista; código novo
sem teste entra nela, mesmo que derrube o número. Documento de cobertura desatualizado é pior que
documento nenhum: ele afirma com precisão numérica uma coisa que deixou de ser verdade.

A medição depende de `concurrency = ["thread", "greenlet"]` no `pyproject.toml`. A ponte async do
SQLAlchemy executa dentro de um greenlet e, sem essa declaração, o rastreador perde tudo que roda
depois de um `await` no banco — o `auth_service` aparecia com 51% em vez de 91%. Número que despenca
sem explicação é este problema, não teste faltando.
