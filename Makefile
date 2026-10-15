PYTHON ?= python3
VENV ?= .venv
COMPOSE ?= docker compose
RUNNER_UID ?= $(shell id -u)
RUNNER_GID ?= $(shell id -g)
RUNS ?= 30
COVERAGE_MIN ?= 90
VENV_PYTHON := $(VENV)/bin/python
RUN = RUNNER_UID=$(RUNNER_UID) RUNNER_GID=$(RUNNER_GID) $(COMPOSE) run --build --rm

.PHONY: help demo test benchmark live setup local local-demo local-test local-benchmark lint format typecheck build clean

help:
	@echo 'Docker: make demo | test | benchmark [RUNS=30] | live'
	@echo 'Local:  make local (setup + demo + tests)'
	@echo '        make setup | local-demo | local-test | local-benchmark'
	@echo 'Checks: make lint | typecheck | build'

demo:
	@mkdir -p artifacts
	$(RUN) runner python -m tool_loop demo --output artifacts/demo

test:
	@mkdir -p artifacts
	$(RUN) runner python -m pytest -p no:cacheprovider --cov --cov-fail-under=$(COVERAGE_MIN) --cov-report=term-missing --cov-report=xml:artifacts/coverage.xml

benchmark:
	@mkdir -p artifacts
	$(RUN) runner python -m tool_loop benchmark --runs $(RUNS) --output artifacts/benchmark

live:
	@test -n "$$OPENAI_API_KEY" || (echo 'Export OPENAI_API_KEY to use the explicit live path.' >&2; exit 1)
	@test -n "$$OPENAI_MODEL" || (echo 'Export OPENAI_MODEL to choose the live model explicitly.' >&2; exit 1)
	@mkdir -p artifacts
	RUNNER_UID=$(RUNNER_UID) RUNNER_GID=$(RUNNER_GID) $(COMPOSE) --profile live run --build --rm live python -m tool_loop live --model "$$OPENAI_MODEL" --output artifacts/live

$(VENV_PYTHON):
	$(PYTHON) -m venv $(VENV)

$(VENV)/.installed: requirements.lock requirements-dev.lock requirements-build.lock pyproject.toml | $(VENV_PYTHON)
	$(VENV_PYTHON) -m pip install --only-binary=:all: --require-hashes -r requirements-dev.lock
	$(VENV_PYTHON) -m pip install --no-deps --no-build-isolation --editable .
	@touch $@

setup: $(VENV)/.installed

local: setup
	$(MAKE) local-demo local-test

local-demo: setup
	$(VENV_PYTHON) -m tool_loop demo --output artifacts/demo

local-test: setup
	@mkdir -p artifacts
	$(VENV_PYTHON) -m pytest --cov --cov-fail-under=$(COVERAGE_MIN) --cov-report=term-missing --cov-report=xml:artifacts/coverage.xml

local-benchmark: setup
	$(VENV_PYTHON) -m tool_loop benchmark --runs $(RUNS) --output artifacts/benchmark

lint: setup
	$(VENV_PYTHON) -m ruff check .
	$(VENV_PYTHON) -m ruff format --check .

format: setup
	$(VENV_PYTHON) -m ruff check --fix .
	$(VENV_PYTHON) -m ruff format .

typecheck: setup
	$(VENV_PYTHON) -m mypy

build: setup
	$(VENV_PYTHON) -m build --no-isolation

clean:
	rm -rf build dist .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml
