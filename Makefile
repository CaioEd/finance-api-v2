.DEFAULT_GOAL := help
VENV := .venv

# Roda em Linux, macOS e Windows: nenhuma receita usa utilitário de shell, só
# `docker`, `python -m` e `python -c`. Outro interpretador: make install PYTHON=python3.12
ifeq ($(OS),Windows_NT)
PYTHON ?= py -3
EXE := .exe
VENV_BIN := $(VENV)/Scripts
else
PYTHON ?= python3
EXE :=
VENV_BIN := $(VENV)/bin
endif

# Com o venv no disco, o layout real manda: um Python de MSYS2 gera `bin/` no Windows.
ifneq ($(wildcard $(VENV)/Scripts/python*),)
VENV_BIN := $(VENV)/Scripts
endif
ifneq ($(wildcard $(VENV)/bin/python*),)
VENV_BIN := $(VENV)/bin
endif

PY := $(VENV_BIN)/python$(EXE)

# cmd.exe precisa de `\` no caminho; sob sh a contrabarra seria escape.
ifeq ($(OS),Windows_NT)
ifneq ($(findstring cmd,$(SHELL)),)
PY := $(subst /,\,$(PY))
endif
endif

# Ferramenta se chama por `python -m`, nunca pelo executável do venv: é a única
# forma de o pip se atualizar no Windows, onde o pip.exe está aberto enquanto roda.

# Marca da última instalação; depende do pyproject, então mexer nas dependências
# faz o próximo alvo que use o venv reinstalar sozinho.
VENV_STAMP := $(VENV)/.install-stamp

.PHONY: help up db api down logs seed migrate migrate-status migrate-down \
        revision test test-unit test-api test-integration coverage db-test \
        lint fmt typecheck check install clean

help:  ## Lista os alvos disponíveis
	@$(PYTHON) -c "import os,re; from pathlib import Path; c,b,r=('\033[36m','\033[1m','\033[0m') if os.environ.get('TERM') else ('','',''); [print('\n'+b+g.group(1)+r) if g else print('  '+c+t.group(1).ljust(18)+r+' '+t.group(2)) for l in Path('$(firstword $(MAKEFILE_LIST))').read_text(encoding='utf-8').splitlines() for g in [re.match(r'##@ (.*)',l)] for t in [re.match(r'([a-z][a-z-]*):.*?## (.*)',l)] if g or t]"

##@ Rodar a aplicação

up: .env  ## Tudo no Docker: banco + API, migrada e semeada (localhost:8000/docs)
	docker compose up -d --build --wait api

db: .env  ## Só o banco de desenvolvimento, em localhost:5432
	docker compose up -d --wait db

api: migrate  ## Só a API, na máquina, com reload — contra o banco de `make db`
	$(PY) -m uvicorn main:create_app --factory --reload

down:  ## Derruba os containers; o volume do banco continua
	docker compose down

logs:  ## Segue o log da API no Docker
	docker compose logs -f api

seed: $(VENV_STAMP) db  ## Repõe a conta de desenvolvimento (só em ENVIRONMENT=local|test)
	$(PY) -m cli seed-dev

##@ Migrations

migrate: $(VENV_STAMP) db  ## Aplica as migrations pendentes
	$(PY) -m alembic upgrade head

migrate-status: $(VENV_STAMP) db  ## Revisão aplicada no banco e a mais recente do repositório
	$(PY) -m alembic current
	$(PY) -m alembic heads

migrate-down: $(VENV_STAMP) db  ## Desfaz a última migration aplicada
	$(PY) -m alembic downgrade -1

# Sem `m` o alembic geraria um arquivo de slug vazio. Com `m`, vira linha vazia,
# que o make pula.
REVISION_GUARD = $(if $(strip $(m)),,@$(PYTHON) -c "raise SystemExit('[make] falta a mensagem. Exemplo: make revision m=\"cria tabela users\"')")

# `migrate` primeiro: fora do head, o autogenerate incluiria no arquivo novo as
# migrations que faltam aplicar. O ruff depois, porque o lint do CI não perdoa a
# saída crua do gerador. (comentário fora da receita: cmd.exe não conhece `#`.)
revision: migrate  ## Gera uma migration: make revision m="cria tabela users"
	$(REVISION_GUARD)
	$(PY) -m alembic revision --autogenerate -m "$(m)"
	$(PY) -m ruff format alembic/versions
	$(PY) -m ruff check --fix alembic/versions

##@ Testes

test: $(VENV_STAMP) db-test  ## A suíte inteira, contra o Postgres de teste
	$(PY) -m pytest

test-unit: $(VENV_STAMP)  ## Só os unitários: sem Docker, sem banco. Um domínio: make test-unit k=admin
	$(PY) -m pytest tests/unit $(if $(k),-k "$(k)")

test-api: $(VENV_STAMP)  ## Só a suíte de API: TestClient + SQLite em memória, sem Docker
	$(PY) -m pytest tests/api $(if $(k),-k "$(k)")

test-integration: $(VENV_STAMP) db-test  ## Só o que exige Postgres
	$(PY) -m pytest tests/integration

coverage: $(VENV_STAMP) db-test  ## Mede a cobertura; atualize docs/cobertura-de-testes.md com o resultado
	$(PY) -m pytest --cov --cov-report=term-missing --cov-report=json:.cache/coverage.json

db-test: .env  ## Só o banco de teste, em localhost:5433 (dados em tmpfs)
	docker compose up -d --wait db-test

##@ Qualidade

lint: $(VENV_STAMP)  ## ruff check + format --check
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

fmt: $(VENV_STAMP)  ## Formata e aplica os fixes automáticos
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .

typecheck: $(VENV_STAMP)  ## mypy --strict em src/
	$(PY) -m mypy

check: lint typecheck test  ## Tudo que o CI roda

##@ Ambiente

install:  ## Cria o venv e (re)instala a aplicação com as dependências de dev
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"
	$(PY) -c "from pathlib import Path; Path('$(VENV_STAMP)').touch()"

clean:  ## Remove caches
	@$(PYTHON) -c "import shutil; from pathlib import Path; shutil.rmtree('.cache', ignore_errors=True); [shutil.rmtree(p, ignore_errors=True) for p in Path('.').rglob('__pycache__') if '$(VENV)' not in p.parts and '.git' not in p.parts]"

$(VENV_STAMP): pyproject.toml
	$(MAKE) install

.env:
	@$(PYTHON) -c "import secrets; from pathlib import Path; k='JWT_SECRET_KEY='; src=Path('.env.example').read_text(encoding='utf-8').splitlines(); Path('.env').write_text('\n'.join(k+secrets.token_urlsafe(48) if l.startswith(k) else l for l in src)+'\n', encoding='utf-8'); print('[make] .env criado a partir de .env.example, com JWT_SECRET_KEY gerada')"
