# Finance API

API REST de finanças pessoais em FastAPI.

## Estado

| Fase | Escopo | Situação |
|---|---|---|
| 0 | Esqueleto executável: config, banco, erros, relógio, health, Alembic, Docker, CI | **concluída** |
| 1 | Identidade e sessão: `users`, `refresh_tokens`, auth, `/users/me`, CLI de admin | **concluída** |
| 2 | Categorias | pendente |
| 3 | Transações | pendente |
| 4 | Saldos | pendente |
| 5 | Relatório PDF | pendente |
| 6 | Admin e endurecimento | pendente |

## Começando

```bash
cp .env.example .env                       # obrigatórios: ENVIRONMENT, APP_TIMEZONE,
                                           # DATABASE_URL e JWT_SECRET_KEY
openssl rand -base64 48                    # gere a sua JWT_SECRET_KEY e cole no .env
make install                               # cria .venv e instala com as deps de dev
make up                                    # sobe api + postgres
docker compose exec api alembic upgrade head
curl localhost:8000/health/ready
```

Primeiro administrador (não existe endpoint público que crie admin):

```bash
docker compose exec api python -m cli create-admin --email admin@exemplo.com --username admin
```

Documentação da API em `http://localhost:8000/docs` (desligável por `DOCS_ENABLED`).

## Desenvolvimento

```bash
make check      # lint + typecheck + testes (é o que o CI roda)
make test       # sobe o postgres de teste e roda a suíte
make fmt        # ruff format + fixes automáticos
make revision m="cria tabela users"   # nova migration
make migrate    # aplica as pendentes
```

A suíte usa **Postgres real** (serviço `db-test`, dados em tmpfs), nunca SQLite: o desenho depende de
índice parcial, agregação com `FILTER` e `NUMERIC`, e nenhum dos três se comporta igual no SQLite.

## Convenções

- **Configuração só vem do ambiente.** `ENVIRONMENT`, `APP_TIMEZONE` e `DATABASE_URL` não têm default —
  a aplicação recusa subir sem eles em vez de subir errada.
- **Nada de `app` de módulo.** O servidor usa `uvicorn main:create_app --factory`, para que importar o
  módulo não abra conexão nem exija configuração.
- **`src/` é a raiz de import**, não um pacote: escreve-se `from core.config import ...`, não
  `from finance_api.core.config import ...`. A instalação (`pip install -e .`) remove o prefixo `src/`.
- **Datas:** competência é `date` no fuso da aplicação, instantes são `TIMESTAMPTZ` em UTC, e "hoje" vem
  sempre de `core.clock.Clock` — nunca de `date.today()`.
- **Erros:** serviços levantam `DomainError`; a camada HTTP traduz. Toda resposta de erro usa o envelope
  `{"error": {"code", "message", "details"}}`.
- **Organização por camada, um arquivo por recurso:** `api/routes/`, `services/`, `repositories/`,
  `models/`, `schemas/`, `core/`, `dependencies/`. `api/router.py` é o único lugar que faz
  `include_router`.
- **`services/` e `repositories/` não importam FastAPI.** Toda a fiação de `Depends` mora em
  `dependencies/`, então regra de negócio e SQL são testáveis sem framework web.
- **Sem pasta `shared/`.** Infraestrutura vai para `core/`; tipo de domínio vai para o `models/` do
  recurso que o define. Pasta genérica vira depósito.
- **Caches de ferramenta ficam em `.cache/`** (ruff, mypy, pytest), fora da raiz e no `.gitignore`.
  `make clean` apaga.
