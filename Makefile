.DEFAULT_GOAL := help
VENV := .venv

# ---------------------------------------------------------------- portabilidade
#
# O mesmo Makefile roda em Linux, macOS e Windows. Três diferenças a acomodar:
#
#   1. o venv instala os executáveis em `bin/` (Unix) e em `Scripts/` (Windows);
#   2. o interpretador de bootstrap é `python3` (Unix) e o launcher `py` (Windows);
#   3. cmd.exe não tem grep, awk, find nem rm — então nenhuma receita depende de
#      utilitário de shell: o que não é docker é `python -c`.
#
# Se o seu Python não atender por esses nomes: `make install PYTHON=python3.12`.
#
# `OS` só está definida no Windows — inclusive sob Git Bash/MSYS, que é o que
# queremos: lá o Python continua sendo o nativo, com layout `Scripts/`.
ifeq ($(OS),Windows_NT)
PYTHON ?= py -3
EXE := .exe
VENV_BIN := $(VENV)/Scripts
else
PYTHON ?= python3
EXE :=
VENV_BIN := $(VENV)/bin
endif

# Com o venv já criado, quem decide é o layout que está no disco: um Python de
# MSYS2 gera `bin/` mesmo no Windows, e o palpite acima erraria.
ifneq ($(wildcard $(VENV)/Scripts/python*),)
VENV_BIN := $(VENV)/Scripts
endif
ifneq ($(wildcard $(VENV)/bin/python*),)
VENV_BIN := $(VENV)/bin
endif

PY := $(VENV_BIN)/python$(EXE)

# cmd.exe resolve caminho relativo com `\` sempre e com `/` só às vezes; sob sh
# (Linux, macOS, Git Bash) a contrabarra seria escape, então a troca é condicional.
ifeq ($(OS),Windows_NT)
ifneq ($(findstring cmd,$(SHELL)),)
PY := $(subst /,\,$(PY))
endif
endif

# As ferramentas são chamadas como `python -m ...`, nunca pelo executável do venv:
# um único nome resolve nos três sistemas, e é a única forma de o pip conseguir
# atualizar a si mesmo no Windows (o pip.exe está aberto enquanto roda).

.PHONY: help install up down logs test test-unit test-integration test-db lint fmt typecheck check seed migrate revision run clean

help:  ## Lista os alvos disponíveis
	@$(PYTHON) -c "import os,re; from pathlib import Path; c,r = ('\033[36m','\033[0m') if os.environ.get('TERM') else ('',''); [print('  ' + c + m[1].ljust(17) + r + ' ' + m[2]) for l in Path('$(firstword $(MAKEFILE_LIST))').read_text(encoding='utf-8').splitlines() for m in [re.match(r'([A-Za-z_-]+):.*?## (.*)', l)] if m]"

install:  ## Cria o venv e instala a aplicação em modo editável com as deps de dev
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

up:  ## Sobe api + banco
	docker compose up -d --build api

down:  ## Derruba tudo (mantém o volume do banco)
	docker compose down

logs:  ## Segue o log da api
	docker compose logs -f api

test-db:  ## Sobe só o banco de teste e espera ficar saudável
	docker compose up -d --wait db-test

test: test-db  ## Roda a suíte inteira contra o Postgres de teste
	$(PY) -m pytest

test-unit:  ## Só os unitários: sem Docker, sem banco. Um domínio: make test-unit k=admin
	$(PY) -m pytest tests/unit $(if $(k),-k "$(k)")

test-integration: test-db  ## Roda só o que exige Postgres
	$(PY) -m pytest tests/integration

lint:  ## ruff check + format --check
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

fmt:  ## Formata e aplica os fixes automáticos
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .

typecheck:  ## mypy --strict em src/
	$(PY) -m mypy

check: lint typecheck test  ## Tudo que o CI roda

# `-T` dispensa o pseudo-TTY: o comando não é interativo, e sem isso o Git Bash
# do Windows reclama que a entrada não é um terminal.
seed:  ## Cria/repõe a conta de desenvolvimento (só em ENVIRONMENT=local|test)
	docker compose exec -T api python -m cli seed-dev

migrate:  ## Aplica as migrations pendentes
	$(PY) -m alembic upgrade head

# O autogenerate emite aspas simples e linhas longas; o lint do CI não perdoa.
# Formatar primeiro: é o format que quebra as linhas que o check reclamaria.
# (comentário fora da receita de propósito: cmd.exe não conhece `#`.)
revision:  ## Gera uma migration: make revision m="cria tabela users"
	$(PY) -m alembic revision --autogenerate -m "$(m)"
	$(PY) -m ruff format alembic/versions
	$(PY) -m ruff check --fix alembic/versions

run:  ## Sobe a API na máquina, com reload
	$(PY) -m uvicorn main:create_app --factory --reload

clean:  ## Remove caches
	@$(PYTHON) -c "import shutil; from pathlib import Path; shutil.rmtree('.cache', ignore_errors=True); [shutil.rmtree(p, ignore_errors=True) for p in Path('.').rglob('__pycache__') if '$(VENV)' not in p.parts and '.git' not in p.parts]"
