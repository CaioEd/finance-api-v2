# Finance API

API REST de finanças pessoais, multiusuário. Cada pessoa registra receitas e despesas, consulta saldos
agregados (mês corrente, mês a mês, intervalo de datas) e baixa um PDF com o resumo de um período.

## Tech stack

| Camada | Escolha |
|---|---|
| Linguagem | Python 3.12+ |
| Web | FastAPI + Pydantic v2 |
| Banco | PostgreSQL 17, SQLAlchemy 2.0 async + asyncpg |
| Migrations | Alembic (modo async) |
| Auth | JWT próprio (PyJWT) + senha em argon2id |
| Testes | pytest + pytest-asyncio + httpx.AsyncClient, contra Postgres real |
| Qualidade | ruff (lint + format), mypy `--strict` |
| Execução | Docker Compose (api + db + db-test) |

## Pré-requisitos

- Docker e Docker Compose
- Python 3.12+ (só para rodar a suíte e as ferramentas fora do container)

## Como rodar

```bash
cp .env.example .env
openssl rand -base64 48          # cole o resultado em JWT_SECRET_KEY no .env

make up                          # constrói e sobe api + postgres

curl localhost:8000/health/ready
# {"status":"ok","database":"ok"}
```

Em `ENVIRONMENT=local` ou `test`, o container aplica as migrations e semeia a conta de
desenvolvimento sozinho na subida — não há passo manual. Em staging e produção não faz nem uma coisa
nem outra: migration é passo deliberado, não efeito colateral de subir um container.

Documentação interativa em **http://localhost:8000/docs** (e `/redoc`, `/openapi.json`).

Para rodar a API na máquina em vez do container — útil para depurar com breakpoint:

```bash
make install     # cria .venv e instala em modo editável com as deps de dev
docker compose up -d db
make migrate
make run         # uvicorn com reload em http://localhost:8000
```

### Conta de desenvolvimento

Já vem pronta, recriada a cada subida do container:

| | |
|---|---|
| e-mail | `admin@exemplo.com` |
| senha | `123456` |
| papel | `admin` |

Configurável por `DEV_ADMIN_EMAIL`, `DEV_ADMIN_USERNAME` e `DEV_ADMIN_PASSWORD` no `.env`. Para
repor na mão (útil depois de mexer na conta durante um teste manual): `make seed`.

O comando **recusa rodar** com `ENVIRONMENT` diferente de `local` ou `test` — a trava está dentro do
próprio comando (`cli.refuse_outside_dev`), não só no shell script que o chama. A conta é reposta a
cada subida: senha, papel `admin` e `is_active` voltam ao valor documentado mesmo que alguém os tenha
mudado.

> A senha tem 6 caracteres e a API exige 10 no registro. O seed grava direto pelo model, sem passar
> pelos schemas de entrada — aceitável num dado de desenvolvimento, e mais uma razão para a trava de
> ambiente existir.

### Administrador de verdade

Não existe endpoint público que crie admin: quem cria é a linha de comando. Este comando funciona em
qualquer ambiente.

```bash
docker compose exec api python -m cli create-admin --email chefe@empresa.com --username chefe
```

Omitir `--password` faz o comando pedir a senha sem eco, para ela não ficar no histórico do shell.

### Experimentando

```bash
# cria a conta e já devolve o par de tokens
curl -X POST localhost:8000/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"ana@exemplo.com","username":"ana","password":"senha-bem-comprida"}'

# usa o access_token da resposta
curl localhost:8000/api/v1/users/me -H "Authorization: Bearer $ACCESS_TOKEN"
```

## Endpoints

Tudo sob `/api/v1`, sem barra final. Autenticação por `Authorization: Bearer <access_token>`.

| Método | Rota | O que faz | Auth |
|---|---|---|---|
| POST | `/auth/register` | Cria a conta e devolve o par de tokens | — |
| POST | `/auth/login` | Autentica por **e-mail** e senha | — |
| POST | `/auth/refresh` | Rotaciona o refresh token e emite um par novo | — |
| POST | `/auth/logout` | Revoga o refresh token enviado (idempotente) | — |
| GET | `/users/me` | Dados do usuário autenticado | sim |
| PATCH | `/users/me` | Atualiza o próprio perfil | sim |
| POST | `/users/me/password` | Troca a senha e encerra todas as sessões | sim |
| DELETE | `/users/me` | Exclui a conta e tudo que pende dela | sim |
| GET | `/categories` | Lista as do sistema e as do próprio usuário (`?kind=income\|expense`) | sim |
| POST | `/categories` | Cria uma categoria do próprio usuário | sim |
| GET | `/categories/{id}` | Detalha uma categoria visível | sim |
| PATCH | `/categories/{id}` | Renomeia ou troca o tipo (própria; global só `admin`) | sim |
| DELETE | `/categories/{id}` | Exclui uma categoria (própria; global só `admin`) | sim |
| GET | `/health`, `/health/ready` | Liveness e readiness (fora de `/api/v1`) | — |

Transações, saldos e o relatório em PDF ainda não existem — ver [Roadmap](#roadmap).

### Categorias

Uma tabela só, dois donos possíveis:

- **do sistema** (`user_id IS NULL`): as 16 que a migration insere — 6 de receita, 10 de despesa.
  Todo mundo enxerga; **só o `admin` edita ou exclui** (para os demais, `403`). São dado de
  referência do produto, por isso nascem na migration e não no `seed-dev`, que só roda em
  `local`/`test`.
- **do usuário**: visíveis só para quem as criou. A categoria de outra pessoa não aparece na
  listagem e responde `404` pelo id — nunca `403`, que confirmaria a existência dela. Vale também
  para o `admin`: gerir a lista global não é enxergar a lista privada de ninguém (rotas
  administrativas sobre dados de terceiros são a fase 6).

O nome é único por dono e por tipo, **ignorando a caixa** ("Mercado" e "mercado" são a mesma coisa),
o que dois índices parciais impõem: um para as globais, outro para as do usuário. Um `UNIQUE
(user_id, name, kind)` simples não serviria — para o Postgres, `NULL` é distinto de `NULL`, e a
categoria global "Moradia" poderia ser cadastrada quantas vezes se quisesse.

Nomes diferentes só na caixa ou no espaço são o mesmo nome: a entrada é normalizada (pontas e
espaços internos colapsados) preservando a caixa que a pessoa digitou.

### Tokens

- **access token**: JWT, validade de 15 min, não é revogável (a janela de exposição é o próprio TTL).
- **refresh token**: string opaca, validade de 30 dias, guardada no banco apenas como SHA-256.
  Cada `/auth/refresh` **invalida o token usado** e emite outro. Reapresentar um token já rotacionado
  é tratado como roubo: toda a linhagem daquela sessão é revogada e a resposta é `401`.

### Formato de erro

Toda resposta de erro usa o mesmo envelope — não existe parser por endpoint:

```json
{ "error": { "code": "email_taken", "message": "Já existe uma conta com este e-mail.", "details": [] } }
```

Códigos em uso: `invalid_request`, `invalid_credentials`, `invalid_token`, `token_expired`,
`invalid_refresh_token`, `token_reuse_detected`, `forbidden`, `account_inactive`, `not_found`,
`method_not_allowed`, `conflict`, `email_taken`, `username_taken`, `category_name_taken`,
`validation_error`,
`service_unavailable`, `http_error`, `internal_error`. O catálogo completo vive em `core/errors.py`.

Uma exceção, deliberada: `Host` não permitido é rejeitado pelo middleware antes do roteamento e
responde texto puro (`Invalid host header`, `400`), fora do envelope.

## Configuração

Tudo vem do ambiente, via `pydantic-settings`. As quatro primeiras **não têm default**: a aplicação
recusa subir sem elas, em vez de subir com um valor errado.

| Variável | Default | Para que serve |
|---|---|---|
| `ENVIRONMENT` | — | `local` \| `test` \| `staging` \| `production` |
| `APP_TIMEZONE` | — | Fuso em que "hoje" e "mês corrente" são resolvidos (ex.: `America/Sao_Paulo`) |
| `DATABASE_URL` | — | DSN async: `postgresql+asyncpg://...` |
| `JWT_SECRET_KEY` | — | Assina o access token; mínimo de 32 caracteres |
| `DEBUG` | `false` | Precisa ser `false` quando `ENVIRONMENT=production` |
| `DOCS_ENABLED` | `true` | Liga/desliga `/docs`, `/redoc` e `/openapi.json` |
| `ALLOWED_HOSTS` | `*` | Lista separada por vírgula |
| `CORS_ORIGINS` | (vazio) | Lista separada por vírgula |
| `ACCESS_TOKEN_TTL_SECONDS` | `900` | Validade do access token |
| `REFRESH_TOKEN_TTL_DAYS` | `30` | Validade do refresh token |
| `ARGON2_TIME_COST` / `ARGON2_MEMORY_COST_KIB` / `ARGON2_PARALLELISM` | `3` / `65536` / `4` | Custo do hash de senha |
| `DB_ECHO`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT_SECONDS` | `false`, `5`, `10`, `30` | Engine do SQLAlchemy |
| `TEST_DATABASE_URL` | `...:5433/finance_test` | Só a suíte usa; o nome do banco precisa terminar em `_test` |
| `DEV_ADMIN_EMAIL` / `DEV_ADMIN_USERNAME` / `DEV_ADMIN_PASSWORD` | `admin@exemplo.com` / `admin` / `123456` | Conta semeada em `local`/`test`; ignorada nos demais ambientes |

Trocar `JWT_SECRET_KEY` invalida todos os access tokens em circulação. Os refresh tokens continuam
válidos — eles vivem no banco, não na chave.

## Estrutura do projeto

Organização **por camada**, com um arquivo por recurso dentro de cada uma.

```
src/
├── main.py                  fábrica create_app(); middlewares e handlers de erro
├── cli.py                   comandos operacionais (create-admin)
├── version.py
│
├── api/
│   ├── router.py            único lugar do projeto que faz include_router
│   └── routes/              um arquivo por recurso: traduz HTTP ↔ domínio
│       ├── auth.py          o único arquivo de rotas sem autenticação
│       ├── users.py
│       ├── categories.py
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

Fluxo de uma requisição: `api/routes` → `services` → `repositories` → banco. As dependências são
montadas em `dependencies/` e injetadas na rota.

## Desenvolvimento

| Comando | O que faz |
|---|---|
| `make install` | Cria `.venv` e instala em modo editável com as deps de dev |
| `make check` | lint + typecheck + testes — **é exatamente o que o CI roda** |
| `make test` | Sobe o Postgres de teste e roda a suíte |
| `make lint` / `make fmt` | `ruff check` + `format --check` / formata e aplica fixes |
| `make typecheck` | `mypy --strict` em `src/` |
| `make up` / `make down` / `make logs` | Sobe, derruba e acompanha o container da api |
| `make run` | Sobe a API na máquina, com reload |
| `make seed` | Cria/repõe a conta de desenvolvimento (só em `local`/`test`) |
| `make migrate` | Aplica as migrations pendentes |
| `make revision m="..."` | Gera uma migration e já a formata |
| `make clean` | Apaga os caches em `.cache/` |

`make help` lista tudo.

### Testes

```bash
make test                                  # suíte inteira
.venv/bin/pytest tests/unit                # só os unitários (sem I/O)
.venv/bin/pytest -k refresh -x             # filtra por nome, para no primeiro erro
```

- **Postgres real**, nunca SQLite: o desenho usa índice parcial, agregação com `FILTER` e `NUMERIC`, e
  nenhum dos três se comporta igual no SQLite. O serviço `db-test` guarda os dados em tmpfs.
- O schema é montado **pelas migrations** (`alembic upgrade head`), não por `create_all` — migration
  quebrada reprova o build.
- Cada teste roda dentro de uma transação com rollback no fim, então nenhum teste enxerga o dado do
  outro e a ordem de execução não importa.
- `tests/integration/test_authorization_matrix.py` declara, rota a rota, quem pode acessar o quê, e
  **reprova o build se uma rota nova entrar sem essa decisão**. Ao adicionar rota, declare-a lá.

### Migrations

```bash
make revision m="cria tabela categories"   # gera a partir da diferença entre models e banco
make migrate                               # aplica
```

Model novo precisa ser importado em `alembic/env.py`, senão o autogenerate não o enxerga. Sempre leia
a migration gerada antes de aplicar: o autogenerate erra com constraints implícitas.

## Convenções

Coisas que quebram se você fizer diferente:

- **Configuração só vem do ambiente.** Nada de valor de ambiente ou segredo no código.
- **Nada de `app` de módulo.** O servidor usa `uvicorn main:create_app --factory`; importar um módulo
  não pode abrir conexão nem exigir configuração.
- **`src/` é a raiz de import**, não um pacote: escreve-se `from core.config import ...`.
- **`services/` e `repositories/` não importam FastAPI.** Todo `Depends` mora em `dependencies/` ou
  em `api/routes/`.
- **Autenticação se declara no router inteiro**, nunca rota a rota — assim esquecer *fecha* a rota em
  vez de abri-la. A autorização por papel segue a mesma regra (`require_role`), com uma exceção
  declarada: quem pode alterar uma categoria depende da *linha*, não da rota, e a decisão mora em
  `CategoryService._mutable_or_fail`.
- **Datas:** competência é `date` no fuso da aplicação, instantes são `TIMESTAMPTZ` em UTC, e "hoje"
  vem sempre de `core.clock.Clock` — nunca de `date.today()`.
- **Dinheiro é `Decimal`** de ponta a ponta, `NUMERIC(14,2)` no banco, string no JSON. Nunca `float`.
- **Erros:** serviços levantam `DomainError`; a camada HTTP traduz. Nada de `HTTPException` fora de
  `api/routes/`.
- **Recurso de outro usuário responde `404`**, não `403` — `403` confirmaria que o recurso existe.

## Roadmap

| Fase | Escopo | Situação |
|---|---|---|
| 0 | Esqueleto: config, banco, erros, relógio, health, Alembic, Docker, CI | concluída |
| 1 | Identidade e sessão: `users`, `refresh_tokens`, auth, `/users/me`, CLI de admin | concluída |
| 2 | Categorias (globais + custom por usuário) | concluída |
| 3 | Transações (receitas e despesas numa entidade só) | pendente |
| 4 | Saldos: mês corrente, mês a mês, intervalo | pendente |
| 5 | Relatório em PDF | pendente |
| 6 | Rotas administrativas e endurecimento | pendente |
