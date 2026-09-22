# InsightGPT — one-command developer workflow.
#
#   make setup       # FIRST RUN: set up everything from a fresh clone
#   make doctor      # diagnose an existing install (changes nothing)
#   make repair      # clean rebuild + recreate, then re-verify
#   make up          # build + start the whole stack (docker compose up --build)
#   make bootstrap   # first-run: pull models, build warehouse, index documents
#   make down        # stop the stack (volumes preserved)
#   make logs        # tail all service logs
#   make seed        # (re)build the warehouse from synthetic data (host or worker)
#   make reindex     # re-embed changed documents into Qdrant
#   make test        # run every service's offline test suite
#   make lint        # ruff over every Python package
#
# Compose lives in docker/compose.yml but is always driven from the repo root.
# `--env-file .env` is REQUIRED with `-f docker/compose.yml`: compose takes its
# project directory from the compose file's folder, so without the flag it looks
# for `docker/.env`, never reads the root one, and every ${VAR:-default} in the
# topology silently falls back to its default. (The root `compose.yaml` exists so
# that a plain `docker compose up` from the root works too.)

COMPOSE := docker compose --env-file .env -f docker/compose.yml

.DEFAULT_GOAL := help
.PHONY: help setup doctor repair env up down restart bootstrap logs ps seed reindex test lint eval ci-local clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## FIRST RUN: check prerequisites, configure, build, start, seed and verify
	python scripts/setup.py

doctor: ## Diagnose the install and name what is broken (changes nothing)
	python scripts/setup.py --doctor

repair: ## Force a clean rebuild + recreate, re-seed and re-verify
	python scripts/setup.py --repair

env: ## Create .env from .env.example if it does not exist
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example — edit secrets before production use.")

up: env ## Build and start the full stack
	$(COMPOSE) up --build -d
	@echo "Stack starting. First run? Now run: make bootstrap"

down: ## Stop the stack (named volumes are preserved)
	$(COMPOSE) down

restart: ## Restart the stack
	$(COMPOSE) down
	$(COMPOSE) up --build -d

bootstrap: env ## First-run: pull models, build warehouse, index documents
	$(COMPOSE) run --rm worker python scripts/bootstrap.py

logs: ## Tail logs for all services
	$(COMPOSE) logs -f --tail=100

ps: ## Show service status
	$(COMPOSE) ps

seed: ## (Re)build the warehouse + republish the document corpus
	$(COMPOSE) run --rm worker python scripts/seed.py --require-postgres

reindex: ## Re-embed changed documents into Qdrant (tracked as a pipeline run)
	$(COMPOSE) run --rm worker python -m worker run reindex_docs

clean: ## Stop the stack and DELETE all data volumes (destructive)
	$(COMPOSE) down -v

# Offline test suites. Each Python service is self-contained and runs with no
# external services (fixture/duckdb backends). Uses uv so no global installs.
test: ## Run each service's offline tests
	@echo "== services/api ==";        cd services/api        && uv run pytest -q
	@echo "== services/retrieval ==";  cd services/retrieval  && uv run pytest -q
	@echo "== services/ingestion ==";  cd services/ingestion  && uv run pytest -q
	@echo "== services/worker ==";     cd services/worker     && uv run pytest -q
	@echo "== data/generator ==";      uv run pytest -q data/generator/tests
	@echo "== tests (semantic-layer drift) =="; uv run --with pytest --with pyyaml pytest -q tests

lint: ## Ruff over every Python package (line-length 100; E,F,I,UP,B,SIM)
	uv run --with ruff ruff check services data scripts tests

# Offline evaluation harnesses. Both run on the deterministic fixture stack (fake
# provider + DuckDB warehouse) via the api project's environment — no models, DB,
# or network. The floored pytest fails on a regression; the scripts then print
# the human-readable scoreboards.
eval: ## Run the text-to-SQL + faithfulness eval harnesses (floors + scoreboards)
	uv run --project services/api pytest -q tests/eval/text2sql.py tests/eval/faithfulness.py
	@echo "== text-to-SQL scoreboard =="; uv run --project services/api python tests/eval/text2sql.py
	@echo "== faithfulness scoreboard =="; uv run --project services/api python tests/eval/faithfulness.py

ci-local: lint test eval ## Everything CI runs that needs no Docker: lint + tests + eval
	@echo "ci-local complete: lint, all offline test suites, and eval harnesses passed."
