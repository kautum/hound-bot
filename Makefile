.PHONY: db-up db-down test lint run migrate

db-up:
	docker compose up -d postgres
	@echo "Waiting for Postgres..."
	@until docker compose exec -T postgres pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
	@echo "Postgres is up."

db-down:
	docker compose down

migrate:
	alembic upgrade head

test:
	pytest

lint:
	ruff check app tests

run:
	uvicorn app.main:app --reload --port 8000
