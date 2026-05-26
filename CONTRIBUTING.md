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
`DATABASE_URL=postgresql+psycopg://sportscards:sportscards@localhost:5433/sportscards`  <!-- pragma: allowlist secret -->
in your `.env` (see `.env.example`).

## Running tests

```bash
pytest tests/
# or
make test
```

The default suite runs against per-test SQLite databases (see
`tests/conftest.py::migrated_db`) and does **not** exercise
TimescaleDB-specific behavior (hypertable inserts, `ON CONFLICT ...
RETURNING`, etc.).

## Integration testing

Tests marked `@pytest.mark.integration` run the full pipeline against a
real Postgres + TimescaleDB instance. They cover schema-drift bugs,
hypertable upsert semantics, and the end-to-end CLI flow.

Prerequisite: the docker compose `db` service is running and migrations
are applied:

```bash
docker compose up -d db
uv run alembic upgrade head
```

Then opt in via `RUN_INTEGRATION=1`:

```bash
RUN_INTEGRATION=1 \
  DATABASE_URL=postgresql+psycopg://sportscards:sportscards@localhost:5433/sportscards \
  uv run pytest tests/test_integration_e2e.py tests/test_hypertable_upserts.py tests/test_seed_rowcount.py -v
```

The e2e test wipes transactional + master tables before running, so do
not point it at a database whose data you want to keep. CI is expected
to spin up a fresh compose stack per integration job.

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
docker compose up -d prefect                                         # starts Prefect server on :4200
export PREFECT_API_URL=http://localhost:4200/api                     # point CLI at it
uv run prefect work-pool create default-agent-pool --type process    # one-time
uv run sportscards deploy
```

`sportscards deploy` resolves `prefect.yaml` relative to the repo root, so it
can be invoked from any subdirectory of a source checkout.
