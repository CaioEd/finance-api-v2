# Finance API

API REST de finanças pessoais em FastAPI.

## Estado

| Fase | Escopo | Situação |
|---|---|---|
| 0 | Esqueleto executável: config, banco, erros, relógio, health, Alembic, Docker, CI | **concluída** |
| 1 | Identidade e sessão: `users`, `refresh_tokens`, auth, `/users/me` | pendente |
| 2 | Categorias | pendente |
| 3 | Transações | pendente |
| 4 | Saldos | pendente |
| 5 | Relatório PDF | pendente |
| 6 | Admin e endurecimento | pendente |

## Começando

```bash
cp .env.example .env      # ENVIRONMENT, APP_TIMEZONE e DATABASE_URL são obrigatórios
make install              # cria .venv e instala em modo editável com as deps de dev
make up                   # sobe api + postgres
curl localhost:8000/health/ready
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
- **Nada de `app` de módulo.** O servidor usa `uvicorn finance_api.main:create_app --factory`, para que
  importar o módulo não abra conexão nem exija configuração.
- **Datas:** competência é `date` no fuso da aplicação, instantes são `TIMESTAMPTZ` em UTC, e "hoje" vem
  sempre de `core.clock.Clock` — nunca de `date.today()`.
- **Erros:** serviços levantam `DomainError`; a camada HTTP traduz. Toda resposta de erro usa o envelope
  `{"error": {"code", "message", "details"}}`.
- **Organização por domínio.** Cada domínio é um pacote com `endpoints`, `schemas`, `models`,
  `repositories` e `services`. `api.py` é o único lugar que faz `include_router`.
