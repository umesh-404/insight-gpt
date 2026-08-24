# InsightGPT — one-command developer workflow.
#
#   make up          # build + start the whole stack (docker compose up --build)
#   make bootstrap   # first-run: pull models, build warehouse, index documents
#   make down        # stop the stack (volumes preserved)
#   make logs        # tail all service logs
#   make seed        # (re)build the warehouse from synthetic data (host or worker)
#   make test        # run every service's offline test suite
#
# Compose lives in docker/compose.yml but is always driven from the repo root so
# the root .env and the build contexts resolve correctly.

COMPOSE := docker compose --env-file .env -f docker/compose.yml

.DEFAULT_GOAL := help
.PHONY: help env up down restart bootstrap logs ps seed test clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

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

seed: ## (Re)build the warehouse via the worker container
	$(COMPOSE) run --rm worker python scripts/seed.py

clean: ## Stop the stack and DELETE all data volumes (destructive)
	$(COMPOSE) down -v

# Offline test suites. Each Python service is self-contained and runs with no
# external services (fixture/duckdb backends). Uses uv so no global installs.
test: ## Run each service's offline tests
	@echo "== services/api ==";        cd services/api        && uv run pytest -q
	@echo "== services/retrieval ==";  cd services/retrieval  && uv run pytest -q
	@echo "== services/ingestion ==";  cd services/ingestion  && uv run pytest -q
	@echo "== data/generator ==";      uv run pytest -q data/generator/tests
