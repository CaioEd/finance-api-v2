# Finance API

API REST de finanças pessoais, multiusuário. Cada pessoa registra receitas e despesas, consulta
saldos agregados (mês corrente, mês a mês, intervalo de datas) e baixa um PDF com o resumo de um
período.

Hoje funcionam identidade e sessão (registro, login, refresh rotativo, perfil próprio e o CRUD
administrativo de usuários), as categorias e os lançamentos de receita e despesa. Saldos agregados e
o relatório em PDF ainda não existem.

| Fase | Escopo | Situação |
|---|---|---|
| 0 | Esqueleto: config, banco, erros, relógio, health, Alembic, Docker, CI | concluída |
| 1 | Identidade e sessão: `users`, `refresh_tokens`, auth, `/users/me`, CLI de admin | concluída |
| 2 | Categorias (globais + custom por usuário) | concluída |
| 3 | Transações (receitas e despesas numa entidade só) | concluída |
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

Três formas de subir, e você escolhe pela que quer depurar. Nenhuma tem passo de preparação: o
`.env` — com uma `JWT_SECRET_KEY` gerada na hora — e o `.venv` nascem sozinhos no primeiro alvo que
precisar deles.

| Comando | O que sobe | Quando usar |
|---|---|---|
| `make up` | banco + API, os dois no Docker | rodar a aplicação inteira |
| `make db` | só o Postgres, em `localhost:5432` | apontar outro processo para o banco |
| `make api` | só a API, na sua máquina, com reload | depurar com breakpoint |

```bash
make up                          # constrói, migra, semeia e espera ficar saudável
curl localhost:8000/health/ready
# {"status":"ok","database":"ok"}
```

`make up` só devolve o prompt depois que o healthcheck passa: terminou bem, a API está respondendo.
`make logs` segue o log; `make down` derruba os containers e preserva o volume do banco.

Documentação interativa em **http://localhost:8000/docs** (e `/redoc`, `/openapi.json`).

**`make api`** roda o uvicorn no Python da sua máquina contra o banco do Docker — é o caminho para
depurar com breakpoint. Continua sendo um comando só porque ele sobe o banco e aplica as migrations
antes de subir o servidor:

```bash
make api                         # equivale a: make db + make migrate + uvicorn --reload
```

Em `ENVIRONMENT=local` ou `test`, o container da API aplica as migrations e semeia a conta de
desenvolvimento sozinho na subida. Em staging e produção não faz nem uma coisa nem outra: migration
é passo deliberado, não efeito colateral de subir um container.

### Migrations

O schema vem sempre do Alembic — `create_all` não existe no projeto, nem em teste. Os quatro
comandos rodam no Python da sua máquina contra o banco publicado em `localhost:5432`, e todos sobem
o banco antes, se ele não estiver de pé:

```bash
make migrate                     # aplica as pendentes
make migrate-status              # revisão aplicada no banco e a mais recente do repositório
make migrate-down                # desfaz a última
make revision m="cria tabela transactions"
```

`make revision` aplica o que falta **antes** de gerar: fora do head, o autogenerate escreveria no
arquivo novo as migrations que você ainda não aplicou. Depois de gerar, formata e passa o ruff nos
arquivos — o lint do CI não perdoa a saída crua do alembic. Sem `m`, o comando recusa em vez de
gerar um arquivo sem nome. Model novo precisa ser importado em `alembic/env.py`, ou o autogenerate
não o enxerga.

### Dependências

`make install` cria o `.venv` e instala o projeto em modo editável com as dependências de dev. Você
raramente precisa chamá-lo: todo alvo que usa o venv o reconstrói sozinho quando o `pyproject.toml`
muda. A marca é `.venv/.install-stamp`, e é o `pyproject` que a invalida — mexeu na lista de
dependências, o próximo `make test` (ou `api`, ou `lint`) já reinstala. Na mão, só para forçar.

### Contas

**Conta de desenvolvimento**, recriada a cada `make up`: `admin@exemplo.com` / `123456`, papel
`admin`. Configurável por `DEV_ADMIN_*` no `.env` e reposta com `make seed`, que serve aos dois
fluxos por rodar no seu Python contra o banco publicado. O comando **recusa rodar** fora de
`local`/`test` — a trava está dentro dele (`cli.refuse_outside_dev`), não no script que o chama.

**Administrador de verdade** não nasce de endpoint público, e sim da linha de comando (funciona em
qualquer ambiente). Omitir `--password` faz o comando pedi-la sem eco:

```bash
.venv/bin/python -m cli create-admin --email chefe@empresa.com --username chefe   # na máquina
docker compose exec api python -m cli create-admin --email chefe@empresa.com --username chefe
```

A segunda forma é a que vale onde só existe o container — staging e produção.

**Configuração** vem toda do ambiente, via `pydantic-settings`. O `.env.example` lista cada variável
com o que ela faz: é a referência, e o `.env` nasce dele. Quatro **não têm default**, e a aplicação
recusa subir sem elas: `ENVIRONMENT`, `APP_TIMEZONE`, `DATABASE_URL` e `JWT_SECRET_KEY`.

`make help` lista todos os alvos, agrupados por assunto — rodar, migrations, testes, qualidade e
ambiente.

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
│       ├── categories.py    categorias do sistema e do usuário
│       ├── transactions.py  lançamentos de receita e despesa
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
| GET | `/categories` | Lista as do sistema e as próprias (`?kind=income\|expense`) | autenticado |
| POST | `/categories` | Cria uma categoria própria | autenticado |
| GET | `/categories/{id}` | Detalha uma categoria visível | autenticado |
| PATCH | `/categories/{id}` | Renomeia ou troca o tipo | dono; global só admin |
| DELETE | `/categories/{id}` | Exclui a categoria | dono; global só admin |
| GET | `/transactions` | Lista os lançamentos, com filtro e paginação | autenticado |
| POST | `/transactions` | Registra uma receita ou despesa | autenticado |
| GET | `/transactions/{id}` | Detalha um lançamento próprio | autenticado |
| PATCH | `/transactions/{id}` | Atualiza um lançamento próprio | autenticado |
| DELETE | `/transactions/{id}` | Exclui um lançamento próprio | autenticado |
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
  em vez de abri-la. Autorização por papel idem, com `require_role(...)`. Exceção única e declarada:
  quem pode alterar uma categoria depende da *linha*, não da rota (a mesma serve a global e a do
  usuário), e a decisão mora em `CategoryService._mutable_or_fail`.
- **Recurso de outro usuário responde `404`**, não `403`: `403` confirmaria que ele existe. O escopo
  por dono é imposto no repositório, não no endpoint.
- **Categoria com `user_id IS NULL` é do sistema:** todo mundo enxerga, só o `admin` altera. A
  unicidade do nome sai de dois índices parciais sobre `lower(name)` — num `UNIQUE` do Postgres
  `NULL` é distinto de `NULL`, e a global "Moradia" poderia repetir à vontade. As 16 categorias
  padrão nascem na própria migration, que é dado de referência do produto e não `seed-dev`.
- **Nada de `app` de módulo.** O servidor usa `uvicorn main:create_app --factory`; importar um
  módulo não pode abrir conexão nem exigir configuração.
- **`src/` é a raiz de import**, não um pacote: escreve-se `from core.config import ...`.
- **`services/` e `repositories/` não importam FastAPI**, e serviço não conhece `HTTPException`:
  ele levanta `DomainError` e a camada HTTP traduz.
- **Datas:** competência é `date` no fuso da aplicação, instantes são `TIMESTAMPTZ` em UTC, e "hoje"
  vem sempre de `core.clock.Clock` — nunca de `date.today()`.
- **Dinheiro é `Decimal`** de ponta a ponta, `NUMERIC(14,2)` no banco, string no JSON. Nunca `float`.
- **Lançamento não guarda o próprio tipo:** receita ou despesa é o `kind` da categoria à qual ele
  está preso. Duas fontes para o mesmo fato é como uma despesa acaba lançada em "Salário" e o total
  do mês passa a discordar da lista na tela. Por isso `amount` é sempre positivo — o sinal é
  consequência do tipo, não um segundo jeito de dizê-lo.
- **A FK de `transactions.category_id` não declara `ON DELETE`.** O NO ACTION do Postgres é checado
  no fim da instrução: excluir a conta cascateia para categorias e lançamentos juntos e passa, e
  excluir uma categoria que ainda tem lançamento falha, virando `409 category_in_use`. Com
  `RESTRICT`, que é checado na hora, excluir a conta quebraria.
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
make db-test                               # só o banco de teste, para chamar o pytest na mão
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
