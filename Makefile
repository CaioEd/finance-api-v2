.DEFAULT_GOAL := help
VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: help install up down logs test test-db lint fmt typecheck check migrate revision run clean

help:  ## Lista os alvos disponíveis
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Cria o venv e instala a aplicação em modo editável com as deps de dev
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

up:  ## Sobe api + banco
	docker compose up -d --build api

down:  ## Derruba tudo (mantém o volume do banco)
	docker compose down

logs:  ## Segue o log da api
	docker compose logs -f api

test-db:  ## Sobe só o banco de teste e espera ficar saudável
	docker compose up -d --wait db-test

test: test-db  ## Roda a suíte inteira contra o Postgres de teste
	$(VENV)/bin/pytest

lint:  ## ruff check + format --check
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

fmt:  ## Formata e aplica os fixes automáticos
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/ruff format .

typecheck:  ## mypy --strict em src/
	$(VENV)/bin/mypy

check: lint typecheck test  ## Tudo que o CI roda

seed:  ## Cria/repõe a conta de desenvolvimento (só em ENVIRONMENT=local|test)
	docker compose exec api python -m cli seed-dev

migrate:  ## Aplica as migrations pendentes
	$(VENV)/bin/alembic upgrade head

revision:  ## Gera uma migration: make revision m="cria tabela users"
	$(VENV)/bin/alembic revision --autogenerate -m "$(m)"
	@# O autogenerate emite aspas simples e linhas longas; o lint do CI não perdoa.
	@# Formatar primeiro: é o format que quebra as linhas que o check reclamaria.
	$(VENV)/bin/ruff format alembic/versions
	$(VENV)/bin/ruff check --fix alembic/versions

run:  ## Sobe a API na máquina, com reload
	$(VENV)/bin/uvicorn main:create_app --factory --reload

clean:  ## Remove caches
	rm -rf .cache
	find . -path ./.venv -prune -o -type d -name __pycache__ -print0 | xargs -0 rm -rf
