# Contributing

## Local setup

```bash
docker compose up -d db
uv sync
uv run alembic upgrade head
uv run sportscards seed players
uv run sportscards seed cards
```

The dev database listens on host port `5433` (mapped to container 5432). Set
`DATABASE_URL=postgresql+psycopg://sportscards:sportscards@localhost:5433/sportscards`
in your `.env` (see `.env.example`).

## Running tests

```bash
pytest tests/
# or
make test
```

## Pre-commit

Install once per clone:

```bash
uv run pre-commit install
```

On first use, initialize the secrets baseline so `detect-secrets` knows what to
ignore:

```bash
uv run detect-secrets scan > .secrets.baseline
```

Run all hooks against the full tree before pushing:

```bash
uv run pre-commit run --all-files
```

## Branch naming

- `phase-N-<topic>` — work that advances a phase from the master plan
- `chore/<topic>` — tooling, CI, infra
- `fix/<topic>` — bugfixes
- `docs/<topic>` — docs-only changes

## Commit style

- Imperative mood ("add foo", not "added foo" / "adds foo")
- Conventional-Commits prefix: `feat:`, `chore:`, `fix:`, `docs:`, `refactor:`, `test:`
- Reference the plan section or research-doc lines when the rationale is non-obvious

## Plans

The master plan lives at
`/Users/peter/.claude/plans/users-peter-downloads-compass-artifact-cheerful-pearl.md`.
Per-task plans live in the same directory.

## Prefect deployments

Schedules are defined in [`prefect.yaml`](prefect.yaml). To apply them:

```bash
docker compose up -d prefect
uv run prefect work-pool create default-agent-pool --type process  # one-time
uv run sportscards deploy
```
