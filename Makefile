# CourseLLM — developer and CI entry points.
#
# Every workflow has exactly one command. CI calls these targets rather than
# reimplementing them, so a green pipeline means the same thing locally.

SHELL := /bin/bash
.DEFAULT_GOAL := help

VENV        ?= .venv
PY          := $(VENV)/bin/python
PIP         := $(VENV)/bin/pip
API_DIR     := apps/api
WEB_DIR     := apps/web
SRC         := $(API_DIR)/src
PYTHONPATH  := $(SRC)

# Local database used by `make db-reset` and integration tests.
#
# Two roles, mirroring production: <owner> owns the schema and runs migrations,
# coursellm_app runs the application and cannot bypass Row-Level Security. See
# scripts/bootstrap_db.sql for why that split is required rather than optional.
DB_NAME     ?= coursellm_dev
TEST_DB_NAME?= coursellm_test
DB_HOST     ?= localhost
DB_PORT     ?= 5432
DB_USER     ?= $(shell whoami)
APP_ROLE    ?= coursellm_app

DATABASE_URL          ?= postgresql+asyncpg://$(APP_ROLE)@$(DB_HOST):$(DB_PORT)/$(DB_NAME)
ALEMBIC_DATABASE_URL  ?= postgresql+asyncpg://$(DB_USER)@$(DB_HOST):$(DB_PORT)/$(DB_NAME)
TEST_DATABASE_URL     ?= postgresql+asyncpg://$(APP_ROLE)@$(DB_HOST):$(DB_PORT)/$(TEST_DB_NAME)
TEST_ALEMBIC_DATABASE_URL ?= postgresql+asyncpg://$(DB_USER)@$(DB_HOST):$(DB_PORT)/$(TEST_DB_NAME)

export PYTHONPATH
export DATABASE_URL
export ALEMBIC_DATABASE_URL
export ENVIRONMENT ?= local

# ---------------------------------------------------------------------------
.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
.PHONY: venv
venv: ## Create the virtualenv
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip setuptools wheel

.PHONY: install
install: venv ## Install core + dev dependencies (no heavy ML extras)
	$(PIP) install -e "$(API_DIR)[dev]"

.PHONY: install-all
install-all: venv ## Install everything, including embeddings, rerank, MCP, LangSmith
	$(PIP) install -e "$(API_DIR)[dev,embeddings,rerank,mcp,langsmith,docx,checkpoint-postgres]"

.PHONY: install-web
install-web: ## Install frontend dependencies
	cd $(WEB_DIR) && npm install

# ---------------------------------------------------------------------------
# Quality
# ---------------------------------------------------------------------------
.PHONY: fmt
fmt: ## Format Python and frontend sources
	$(VENV)/bin/ruff format $(API_DIR)
	$(VENV)/bin/ruff check --fix $(API_DIR)

.PHONY: lint
lint: ## Lint Python sources
	$(VENV)/bin/ruff check $(API_DIR)
	$(VENV)/bin/ruff format --check $(API_DIR)

.PHONY: lint-web
lint-web: ## Lint the frontend
	cd $(WEB_DIR) && npm run lint

.PHONY: typecheck
typecheck: ## Type-check the backend
	cd $(API_DIR) && ../../$(VENV)/bin/mypy src

.PHONY: typecheck-web
typecheck-web: ## Type-check the frontend
	cd $(WEB_DIR) && npm run typecheck

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
.PHONY: test
test: test-unit ## Run the default (fast) test suite

.PHONY: test-unit
test-unit: ## Unit tests only; no external services
	cd $(API_DIR) && ../../$(VENV)/bin/pytest -m unit -q

.PHONY: test-integration
test-integration: ## Integration tests; requires PostgreSQL (see TEST_DATABASE_URL)
	cd $(API_DIR) && TEST_DATABASE_URL="$(TEST_DATABASE_URL)" \
		../../$(VENV)/bin/pytest -m integration -q

.PHONY: test-security
test-security: ## Adversarial security regression tests
	cd $(API_DIR) && ../../$(VENV)/bin/pytest -m security -q

.PHONY: test-all
test-all: ## Everything, with coverage
	cd $(API_DIR) && TEST_DATABASE_URL="$(TEST_DATABASE_URL)" \
		../../$(VENV)/bin/pytest -q --cov=coursellm --cov-report=term-missing --cov-report=xml

.PHONY: test-web
test-web: ## Frontend tests
	cd $(WEB_DIR) && npm run test -- --run

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
.PHONY: db-create
db-create: ## Create the dev and test databases with the vector extension
	-createdb -h $(DB_HOST) -p $(DB_PORT) $(DB_NAME)
	-createdb -h $(DB_HOST) -p $(DB_PORT) $(TEST_DB_NAME)
	psql -h $(DB_HOST) -p $(DB_PORT) -d $(DB_NAME) -c 'CREATE EXTENSION IF NOT EXISTS vector;'
	psql -h $(DB_HOST) -p $(DB_PORT) -d $(TEST_DB_NAME) -c 'CREATE EXTENSION IF NOT EXISTS vector;'

.PHONY: migrate
migrate: ## Apply all migrations as the schema owner
	cd $(API_DIR) && ../../$(VENV)/bin/alembic upgrade head

.PHONY: downgrade
downgrade: ## Roll back one migration
	cd $(API_DIR) && ../../$(VENV)/bin/alembic downgrade -1

.PHONY: migration
migration: ## Autogenerate a migration: make migration m="add table"
	@test -n "$(m)" || (echo 'usage: make migration m="description"'; exit 1)
	cd $(API_DIR) && ../../$(VENV)/bin/alembic revision --autogenerate -m "$(m)"

.PHONY: migrate-test
migrate-test: ## Apply all migrations to the test database as the schema owner
	cd $(API_DIR) && ALEMBIC_DATABASE_URL="$(TEST_ALEMBIC_DATABASE_URL)" \
		../../$(VENV)/bin/alembic upgrade head

.PHONY: db-bootstrap
db-bootstrap: ## Create the restricted app role and grant it privileges (after migrate)
	psql -h $(DB_HOST) -p $(DB_PORT) -d $(DB_NAME) -f scripts/bootstrap_db.sql
	psql -h $(DB_HOST) -p $(DB_PORT) -d $(TEST_DB_NAME) -f scripts/bootstrap_db.sql

.PHONY: db-setup
db-setup: db-create migrate migrate-test db-bootstrap ## Full local database setup, in dependency order
	@echo
	@echo "Application role: $(APP_ROLE) (NOSUPERUSER NOBYPASSRLS, so RLS applies)"
	@echo "Migrations run as: $(DB_USER)"
	@echo "Ready. Run 'make api' and 'make test-integration'."

.PHONY: db-reset
db-reset: ## Drop and rebuild the dev database from migrations
	-dropdb -h $(DB_HOST) -p $(DB_PORT) --if-exists $(DB_NAME)
	make db-create
	make migrate
	make db-bootstrap

.PHONY: db-reset-test
db-reset-test: ## Drop and rebuild the test database from migrations
	-dropdb -h $(DB_HOST) -p $(DB_PORT) --if-exists $(TEST_DB_NAME)
	make db-create
	make migrate-test
	make db-bootstrap

.PHONY: db-heads
db-heads: ## Show the current migration head(s)
	cd $(API_DIR) && ../../$(VENV)/bin/alembic heads

.PHONY: db-inspect
db-inspect: ## Report RLS enforcement and connection identity for the app role
	cd $(API_DIR) && ../../$(VENV)/bin/python -m coursellm.cli db-inspect

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
.PHONY: api
api: ## Run the API with reload
	cd $(API_DIR) && ../../$(VENV)/bin/uvicorn coursellm.main:app --reload --port 8000

.PHONY: worker
worker: ## Run the ingestion worker
	cd $(API_DIR) && ../../$(VENV)/bin/python -m coursellm.workers.runner

.PHONY: web
web: ## Run the frontend dev server
	cd $(WEB_DIR) && npm run dev

.PHONY: up
up: ## Start the full stack in Docker
	docker compose up --build -d
	@echo "API  -> http://localhost:8000/docs"
	@echo "Web  -> http://localhost:5173"

.PHONY: down
down: ## Stop the Docker stack
	docker compose down

.PHONY: logs
logs: ## Tail Docker logs
	docker compose logs -f --tail=100

# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
.PHONY: eval
eval: ## Run the RAG evaluation suite and write a report artefact
	$(PY) -m evals.runners.run_rag_eval --output evals/reports/latest.json

.PHONY: eval-retrieval
eval-retrieval: ## Retrieval-only metrics (no LLM required)
	$(PY) -m evals.runners.run_retrieval_eval --output evals/reports/retrieval.json

.PHONY: eval-gate
eval-gate: ## Compare the latest report against the committed baseline; non-zero on regression
	$(PY) -m evals.runners.check_regression \
		--baseline $(API_DIR)/../../evals/reports/baseline.json \
		--current evals/reports/latest.json

# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------
.PHONY: tf-fmt
tf-fmt: ## Format Terraform
	terraform -chdir=infra/terraform fmt -recursive

.PHONY: tf-validate
tf-validate: ## Validate every Terraform environment
	@for env in dev prod; do \
		echo "==> $$env"; \
		terraform -chdir=infra/terraform/environments/$$env init -backend=false -input=false >/dev/null; \
		terraform -chdir=infra/terraform/environments/$$env validate; \
	done

.PHONY: tf-plan
tf-plan: ## Plan the dev environment (never applies)
	terraform -chdir=infra/terraform/environments/dev init -input=false
	terraform -chdir=infra/terraform/environments/dev plan -var-file=terraform.tfvars.example

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------
.PHONY: secrets-scan
secrets-scan: ## Fail if a secret-shaped value is present in tracked files
	bash scripts/scan_secrets.sh

# ---------------------------------------------------------------------------
# Composite gates
# ---------------------------------------------------------------------------
.PHONY: verify
verify: lint typecheck secrets-scan test-all ## The full local quality gate

.PHONY: verify-web
verify-web: lint-web typecheck-web test-web ## The frontend quality gate

.PHONY: clean
clean: ## Remove caches and build artefacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov coverage.xml
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf $(WEB_DIR)/dist $(WEB_DIR)/coverage
