.PHONY: up migrate seed test lint fmt dashboard

up:
	docker compose up -d db

migrate:
	uv run alembic upgrade head

seed:
	uv run sportscards seed players && uv run sportscards seed cards

test:
	uv run pytest tests/

lint:
	uv run ruff check . && uv run ruff format --check . && uv run mypy src

fmt:
	uv run ruff check --fix . && uv run ruff format .

dashboard:
	uv run sportscards dashboard
