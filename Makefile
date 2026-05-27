.PHONY: up migrate seed test lint fmt dashboard ci ci-fast fix-ci install-hooks

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

# Fast pre-push check: lint + format + types only (no DB needed). Catches ~80%
# of CI failures in a few seconds.
ci-fast:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src

# Full local replica of .github/workflows/ci.yml. Requires Postgres
# (run `make up` first) for the alembic step.
ci:
	uv sync --all-extras --dev
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src
	uv run alembic upgrade head
	uv run pytest tests/

# Auto-fix the things ruff can fix, then re-run ci-fast.
fix-ci:
	uv run ruff check --fix .
	uv run ruff format .
	$(MAKE) ci-fast

# Wire .githooks/ as this repo's hooks dir (one-time per clone).
install-hooks:
	git config core.hooksPath .githooks
	@echo "pre-push hook installed -> .githooks/pre-push"
