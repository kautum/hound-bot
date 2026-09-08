.PHONY: db-up db-down test lint run migrate

db-up:
	@pg_isready -h localhost -p 5432 >/dev/null 2>&1 && echo "Postgres already up." || (echo "Start your local Postgres server (e.g. 'brew services start postgresql@14') then re-run make db-up." && exit 1)
	@createdb -h localhost -p 5432 -O $$(whoami) slack_workplace_assistant 2>/dev/null || true
	@createdb -h localhost -p 5432 -O $$(whoami) swa_test 2>/dev/null || true

db-down:
	@echo "No managed process to stop — Postgres runs natively on this machine, not via docker compose."

migrate:
	alembic upgrade head

test:
	DATABASE_URL=postgresql+asyncpg://$$(whoami)@localhost:5432/swa_test pytest

lint:
	ruff check app tests

run:
	uvicorn app.main:app --reload --port 8000
