# FinAgent Enterprise developer commands. Requires: Python 3.12, Docker (for compose), Node 22 (web).
SHELL := /bin/bash
PY ?= python3
export FINAGENT_DATABASE_URL ?= postgresql+psycopg://finagent:finagent@127.0.0.1:5432/finagent
export FINAGENT_TEST_DATABASE_URL ?= postgresql+psycopg://finagent:finagent@127.0.0.1:5432/finagent_test

.PHONY: setup deps-up dev migrate db-app-role seed test test-unit test-integration test-security \
        test-recovery test-release-blockers mutation-check lint format typecheck security trace openapi \
        web-check token down clean
# Hosts without GNU make (e.g. Windows): `python scripts/tasks.py <target>` runs the same targets.

setup:            ## install backend (editable) + dev tools
	$(PY) -m pip install -e ".[dev]"
	@test -f .env || cp .env.example .env

deps-up:          ## start PostgreSQL + Redis only
	docker compose up -d postgres redis

dev: deps-up migrate seed  ## run API locally with reload
	$(PY) -m uvicorn finagent.api.main:create_app --factory --reload --port 8000

migrate:          ## runs as FINAGENT_MIGRATION_DATABASE_URL (schema owner) when set
	$(PY) -m alembic upgrade head

db-app-role:      ## LOCAL ONLY: enable the finagent_app login (production-like privileges)
	$(PY) scripts/db_app_role.py

seed:
	$(PY) -m finagent.database.seeds

test:             ## full suite with junit evidence, then regenerate traceability
	@mkdir -p reports
	$(PY) -m pytest --junitxml=reports/junit.xml
	$(PY) scripts/gen_traceability.py

test-unit:
	$(PY) -m pytest tests/unit

test-integration:
	$(PY) -m pytest tests/integration

test-security:
	$(PY) -m pytest tests/security

test-recovery:
	$(PY) -m pytest tests/recovery

test-release-blockers:  ## prompt §31 - any failure blocks release
	$(PY) -m pytest -m release_blocker

mutation-check:   ## break each security control, confirm a test fails (tests/mutations.yaml)
	$(PY) scripts/mutation_check.py

lint:
	ruff check src tests scripts apps/worker apps/mock-bank
	ruff format --check src tests scripts apps/worker apps/mock-bank

format:
	ruff format src tests scripts apps/worker apps/mock-bank
	ruff check --fix src tests scripts apps/worker apps/mock-bank

typecheck:
	mypy

security:         ## SAST + dependency audit (secret scanning runs in CI via gitleaks)
	$(PY) -m bandit -q -r src
	$(PY) -m pip_audit --progress-spinner off --skip-editable

web-check:
	cd apps/web && npm run typecheck && npm run build

trace: test

openapi:          ## commit the generated contract (prompt §22)
	$(PY) scripts/export_openapi.py > docs/api/openapi.json

token:            ## make token SUB=a.analyst1@synthetic
	@$(PY) scripts/dev_token.py $(SUB)

down:
	docker compose down

clean:
	rm -rf reports .pytest_cache .mypy_cache .ruff_cache
