# Finance API

API REST de finanças pessoais, multiusuário. Cada pessoa registra receitas e despesas, consulta
saldos agregados (mês corrente, mês a mês, intervalo de datas) e baixa um PDF com o resumo de um
período.

Hoje funcionam identidade e sessão: registro, login, refresh rotativo, perfil próprio e o CRUD
administrativo de usuários. Categorias, transações, saldos e o relatório em PDF ainda não existem.

| Fase | Escopo | Situação |
|---|---|---|
| 0 | Esqueleto: config, banco, erros, relógio, health, Alembic, Docker, CI | concluída |
| 1 | Identidade e sessão: `users`, `refresh_tokens`, auth, `/users/me`, CLI de admin | concluída |
| 2 | Categorias (globais + custom por usuário) | pendente |
| 3 | Transações (receitas e despesas numa entidade só) | pendente |
| 4 | Saldos: mês corrente, mês a mês, intervalo | pendente |
| 5 | Relatório em PDF | pendente |
| 6 | Rotas administrativas e endurecimento | parcial — CRUD de usuários entregue |

## Tech stack e requisitos

| Camada | Escolha |
|---|---|
| Linguagem | Python 3.12+ |
| Web | FastAPI + Pydantic v2 |
| Banco | PostgreSQL 17, SQLAlchemy 2.0 async + asyncpg |
| Migrations | Alembic (modo async) |
| Auth | JWT próprio (PyJWT) + senha em argon2id |
| Testes | pytest + pytest-asyncio + httpx.AsyncClient |
| Qualidade | ruff (lint + format), mypy `--strict` |
| Execução | Docker Compose (api + db + db-test) |

Para rodar, você precisa de:

- **Docker e Docker Compose**
- **Python 3.12+** — só para a suíte e as ferramentas fora do container
- **GNU Make** — já vem no Linux e no macOS; no Windows, `winget install ezwinports.make`,
  `choco install make` ou o make do WSL

Os alvos do Makefile rodam nos três sistemas: nenhum depende de utilitário de shell Unix, e o
diretório de executáveis do venv (`bin/` no Unix, `Scripts/` no Windows) é descoberto sozinho. Se o
seu Python não atende por `python3` (Unix) ou `py` (Windows), passe o nome:
`make install PYTHON=python3.12`.

## Como instalar e rodar

```bash
cp .env.example .env
openssl rand -base64 48          # cole o resultado em JWT_SECRET_KEY no .env

make up                          # constrói e sobe api + postgres
curl localhost:8000/health/ready
# {"status":"ok","database":"ok"}
```

Documentação interativa em **http://localhost:8000/docs** (e `/redoc`, `/openapi.json`).

Em `ENVIRONMENT=local` ou `test`, o container aplica as migrations e semeia a conta de
desenvolvimento sozinho na subida. Em staging e produção não faz nem uma coisa nem outra: migration
é passo deliberado, não efeito colateral de subir um container.

**Rodando na máquina**, em vez do container — útil para depurar com breakpoint:

```bash
make install                     # cria .venv e instala em modo editável com as deps de dev
docker compose up -d db
make migrate
make run                         # uvicorn com reload em http://localhost:8000
```

**Conta de desenvolvimento**, recriada a cada subida: `admin@exemplo.com` / `123456`, papel `admin`.
Configurável por `DEV_ADMIN_*` no `.env`, reposta na mão com `make seed`. O comando **recusa rodar**
fora de `local`/`test` — a trava está dentro dele (`cli.refuse_outside_dev`), não no script que o
chama.

**Administrador de verdade** não nasce de endpoint público, e sim da linha de comando (funciona em
qualquer ambiente). Omitir `--password` faz o comando pedi-la sem eco:

```bash
docker compose exec api python -m cli create-admin --email chefe@empresa.com --username chefe
```

**Configuração** vem toda do ambiente, via `pydantic-settings`. O `.env.example` lista cada variável
com o que ela faz — é a referência. Quatro **não têm default**, e a aplicação recusa subir sem elas:
`ENVIRONMENT`, `APP_TIMEZONE`, `DATABASE_URL` e `JWT_SECRET_KEY`.

Os outros alvos do Makefile: `make check` (o que o CI roda), `make lint`, `make fmt`,
`make typecheck`, `make down`, `make logs`, `make revision m="..."`, `make clean`. `make help` lista
todos.

## Arquitetura

Organização **por camada**, com um arquivo por recurso dentro de cada uma. Fluxo de uma requisição:
`api/routes` → `services` → `repositories` → banco, com as dependências montadas em `dependencies/`.

```
src/
├── main.py                  fábrica create_app(); middlewares e handlers de erro
├── cli.py                   comandos operacionais (create-admin)
│
├── api/
│   ├── router.py            único lugar do projeto que faz include_router
│   └── routes/              um arquivo por recurso: traduz HTTP ↔ domínio
│       ├── auth.py          o único arquivo de rotas sem autenticação
│       ├── users.py         perfil próprio (/users/me)
│       ├── admin_users.py   CRUD de usuários, sob require_role(ADMIN)
│       └── health.py
│
├── schemas/                 Pydantic v2 de entrada/saída; nunca expõem o model
├── models/                  mapeamento ORM e constraints; um arquivo por tabela
├── repositories/            toda a construção de query
├── services/                regra de negócio, em Python puro (zero FastAPI)
│
├── core/                    infraestrutura transversal
│   ├── config.py            Settings
│   ├── database.py          Base declarativa, TimestampMixin, engine, sessionmaker
│   ├── security.py          argon2, JWT, tokens opacos
│   ├── clock.py             fonte única de "agora"
│   └── errors.py            catálogo de erros + handlers
│
└── dependencies/            toda a fiação de Depends, e só ela
    ├── database.py          get_session
    ├── state.py             settings, clock, hasher e codec vindos de app.state
    ├── auth.py              get_current_user, require_role
    ├── repositories.py
    └── services.py
```

Tudo sob `/api/v1`, sem barra final. Autenticação por `Authorization: Bearer <access_token>`.

| Método | Rota | O que faz | Acesso |
|---|---|---|---|
| POST | `/auth/register` | Cria a conta e devolve o par de tokens | — |
| POST | `/auth/login` | Autentica por **e-mail** e senha | — |
| POST | `/auth/refresh` | Rotaciona o refresh token e emite um par novo | — |
| POST | `/auth/logout` | Revoga o refresh token enviado (idempotente) | — |
| GET | `/users/me` | Dados do usuário autenticado | autenticado |
| PATCH | `/users/me` | Atualiza o próprio perfil | autenticado |
| POST | `/users/me/password` | Troca a senha e encerra todas as sessões | autenticado |
| DELETE | `/users/me` | Exclui a conta e tudo que pende dela | autenticado |
| GET | `/admin/users` | Lista usuários, com filtro e paginação | admin |
| POST | `/admin/users` | Cria usuário, com papel e estado à escolha | admin |
| PATCH | `/admin/users/{user_id}` | Atualiza usuário, inclusive papel e `is_active` | admin |
| DELETE | `/admin/users/{user_id}` | Exclui usuário e tudo que pende dele | admin |
| GET | `/health`, `/health/ready` | Liveness e readiness (fora de `/api/v1`) | — |

Toda resposta de erro usa o mesmo envelope — não existe parser por endpoint. O catálogo de códigos
vive em `core/errors.py`:

```json
{ "error": { "code": "email_taken", "message": "Já existe uma conta com este e-mail.", "details": [] } }
```

### Decisões que quebram se você fizer diferente

- **Autenticação se declara no router inteiro**, nunca rota a rota — assim esquecer *fecha* a rota
  em vez de abri-la. Autorização por papel idem, com `require_role(...)`.
- **Recurso de outro usuário responde `404`**, não `403`: `403` confirmaria que ele existe. O escopo
  por dono é imposto no repositório, não no endpoint.
- **Nada de `app` de módulo.** O servidor usa `uvicorn main:create_app --factory`; importar um
  módulo não pode abrir conexão nem exigir configuração.
- **`src/` é a raiz de import**, não um pacote: escreve-se `from core.config import ...`.
- **`services/` e `repositories/` não importam FastAPI**, e serviço não conhece `HTTPException`:
  ele levanta `DomainError` e a camada HTTP traduz.
- **Datas:** competência é `date` no fuso da aplicação, instantes são `TIMESTAMPTZ` em UTC, e "hoje"
  vem sempre de `core.clock.Clock` — nunca de `date.today()`.
- **Dinheiro é `Decimal`** de ponta a ponta, `NUMERIC(14,2)` no banco, string no JSON. Nunca `float`.
- **O schema vem das migrations**, nunca de `create_all` — nem em teste. Model novo precisa ser
  importado em `alembic/env.py`, senão o autogenerate não o enxerga.
- **Access token** é JWT de 15 min, não revogável. **Refresh token** é string opaca de 30 dias,
  guardada só como SHA-256 e invalidada a cada uso; reapresentar um já rotacionado derruba a
  linhagem inteira daquela sessão.

## Como rodar todos os testes

A suíte tem duas metades, e só uma precisa de banco:

| | Onde mora | Precisa de Postgres |
|---|---|---|
| Unitários | `tests/unit/` | **não** — nem de Docker, nem de rede |
| Integração | `tests/integration/` | sim (serviço `db-test`, dados em tmpfs) |

```bash
make test                                  # as duas metades; sobe o db-test antes
make test-unit                             # só os unitários, em cerca de um segundo
make test-integration                      # só o que exige Postgres
make check                                 # lint + typecheck + testes: o que o CI roda
```

**Só um domínio.** O filtro `k` vai direto para o `-k` do pytest, que casa com o nome do arquivo e
com o nome do teste:

```bash
make test-unit k=admin                     # CRUD administrativo de usuários
make test-unit k=security                  # hash de senha, JWT e tokens opacos
make test-unit k=clock                     # relógio e competência
make test-unit k="clock or config"         # mais de um domínio
```

Com o venv ativo (ou pelo caminho completo), o pytest aceita um arquivo ou um teste só. No Windows o
executável fica em `.venv\Scripts\pytest`:

```bash
.venv/bin/pytest tests/unit/test_admin_user_service.py
.venv/bin/pytest tests/unit/test_clock.py::test_current_month_follows_the_local_date
.venv/bin/pytest -m "not integration"      # tudo que não precisa de banco
```

O que a suíte garante, e que vale saber antes de escrever teste novo:

- **Unitário não toca I/O**, e isso é garantido, não combinado: um fixture em
  `tests/unit/conftest.py` transforma qualquer tentativa de conectar no Postgres em falha de teste.
  Serviço se testa com um duble que implementa o `Protocol` que o próprio serviço declara.
- **Os fixtures de banco moram em `tests/integration/conftest.py`**, nunca no conftest raiz:
  fixture `autouse` na raiz vale para a árvore inteira de `tests/` e faria a suíte unitária exigir
  um Postgres de pé só para começar a coletar.
- **Postgres real**, nunca SQLite: o desenho usa índice parcial, agregação com `FILTER` e `NUMERIC`,
  e nenhum dos três se comporta igual no SQLite.
- **Cada teste roda numa transação com rollback** no fim, então nenhum enxerga o dado do outro e a
  ordem de execução não importa.
- **`tests/integration/test_authorization_matrix.py` reprova o build se uma rota nova entrar sem
  decisão de autorização.** Ao adicionar rota, declare-a lá.
