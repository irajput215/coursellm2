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
	$(VENV)/bin/ruff format $(API_DIR) evals
	$(VENV)/bin/ruff check --fix $(API_DIR) evals

.PHONY: lint
lint: ## Lint Python sources
	$(VENV)/bin/ruff check $(API_DIR) evals

.PHONY: format-check
format-check: ## Fail if Python sources are not formatted
	$(VENV)/bin/ruff format --check $(API_DIR) evals

.PHONY: lint-web
lint-web: ## Lint the frontend
	cd $(WEB_DIR) && npm run lint

.PHONY: typecheck
typecheck: ## Type-check the backend and the evaluation harness
	cd $(API_DIR) && ../../$(VENV)/bin/mypy src
	$(VENV)/bin/mypy evals

.PHONY: typecheck-web
typecheck-web: ## Type-check the frontend
	cd $(WEB_DIR) && npm run typecheck

# ---------------------------------------------------------------------------
# Tests
#
# Order matters in `verify`: the cheap, deterministic checks run first so a
# formatting or lint error fails in seconds rather than after the four-minute
# integration tier. Coverage is one command (`test-coverage`) that runs every
# non-eval tier in a single process, because the threshold is a property of the
# whole suite and a per-tier figure would double-count core modules.
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

.PHONY: test-coverage
test-coverage: ## Unit + security + integration with coverage; fails below fail_under
	cd $(API_DIR) && TEST_DATABASE_URL="$(TEST_DATABASE_URL)" \
		../../$(VENV)/bin/pytest -m "unit or security or integration" -q \
		--cov=coursellm --cov-report=term-missing --cov-report=xml

.PHONY: test-all
test-all: test-coverage ## Alias kept for older runbooks; runs the coverage suite

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

.PHONY: seed-catalogue
seed-catalogue: ## Seed the curated resource catalogue (idempotent on URL)
	cd $(API_DIR) && ../../$(VENV)/bin/python -m coursellm.cli seed-catalogue

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
.PHONY: api
api: ## Run the API with reload
	cd $(API_DIR) && ../../$(VENV)/bin/uvicorn coursellm.main:app --reload --port 8000

# There is deliberately no `worker` target. Ingestion runs inline in
# services/ingestion.py: there is no durable queue, so a background callback would
# be lost on restart and strand documents at `pending` with nothing to recover
# them. Adding a worker is a deployment change, not a one-line Makefile change.

.PHONY: web
web: ## Run the frontend dev server
	cd $(WEB_DIR) && npm run dev

.PHONY: build-web
build-web: ## Build the frontend into $(WEB_DIR)/dist
	cd $(WEB_DIR) && npm run build

.PHONY: up
up: ## [docker] Start the full stack (build, migrate, api, web)
	docker compose up --build -d
	@echo "API  -> http://localhost:8000/docs"
	@echo "Web  -> http://localhost:5173"

.PHONY: down
down: ## [docker] Stop the stack (keeps volumes; use docker compose down -v to drop data)
	docker compose down

.PHONY: logs
logs: ## [docker] Tail logs from every service
	docker compose logs -f --tail=100

.PHONY: ps
ps: ## [docker] Show container status (migrate should read "exited (0)")
	docker compose ps

.PHONY: migrate-docker
migrate-docker: ## [docker] Re-run migrations and the app-role bootstrap as the schema owner
	docker compose run --rm --build migrate

# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
.PHONY: eval
eval: ## Run the RAG evaluation suite and write a report artefact
	$(PY) -m evals.runners.run_rag_eval --output evals/reports/latest.json

.PHONY: eval-retrieval
eval-retrieval: ## Retrieval-only metrics (no LLM required)
	$(PY) -m evals.runners.run_retrieval_eval --output evals/reports/retrieval.json

# Which report `eval-gate` compares. The default is the retrieval report, which
# is committed and needs no provider key, so `make verify` can run the gate on a
# clean clone. CI overrides it explicitly for the same reason: the committed
# baseline is a retrieval run, and comparing it against a full-RAG `latest.json`
# (which is gitignored and may not exist) would grade a stale artefact.
EVAL_CURRENT ?= evals/reports/retrieval.json

.PHONY: eval-gate
eval-gate: ## Compare an eval report against the committed baseline; non-zero on regression
	$(PY) -m evals.runners.check_regression \
		--baseline evals/reports/baseline.json \
		--current $(EVAL_CURRENT)

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

.PHONY: audit-deps
audit-deps: ## Audit the installed environment against known dependency advisories
	$(PIP) install --quiet pip-audit
	$(VENV)/bin/pip-audit

# ---------------------------------------------------------------------------
# Composite gates
# ---------------------------------------------------------------------------
.PHONY: verify
verify: ## The full local quality gate, cheapest checks first
	@echo "==> 1/8 format check"
	@$(MAKE) --no-print-directory format-check
	@echo "==> 2/8 lint"
	@$(MAKE) --no-print-directory lint
	@echo "==> 3/8 typecheck"
	@$(MAKE) --no-print-directory typecheck
	@echo "==> 4/8 secret scan"
	@$(MAKE) --no-print-directory secrets-scan
	@echo "==> 5/8 unit"
	@$(MAKE) --no-print-directory test-unit
	@echo "==> 6/8 security"
	@$(MAKE) --no-print-directory test-security
	@echo "==> 7/8 integration"
	@$(MAKE) --no-print-directory test-integration
	@echo "==> 8/8 eval gate"
	@$(MAKE) --no-print-directory eval-gate

.PHONY: verify-web
verify-web: lint-web typecheck-web test-web build-web ## The frontend quality gate

.PHONY: clean
clean: ## Remove caches and build artefacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov coverage.xml
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf $(WEB_DIR)/dist $(WEB_DIR)/coverage
