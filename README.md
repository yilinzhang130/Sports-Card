# sportscards-quant

Quantitative sports card trading — **Phase 1: data layer** (NBA only).

Builds a clean, queryable warehouse of NBA card sales + grading population
data from eBay, PSA, and Card Ladder, as a foundation for later modeling
(repeat-sales index, hedonic fair value, AI scouting).

## Quickstart

```bash
# 1. Install deps (uv recommended)
uv sync

# 2. Bring up Postgres+TimescaleDB and Prefect
docker compose up -d

# 3. Copy and fill in credentials
cp .env.example .env
# edit .env

# 4. Run migrations
uv run alembic upgrade head

# 5. Seed master data
uv run sportscards seed cards
uv run sportscards seed players

# 6. Run a one-shot ingest
uv run sportscards ingest ebay --since 1d

# 7. Deploy daily flows to Prefect
uv run sportscards deploy

# 8. Launch the local Trader Console (multi-page Streamlit UI)
uv run sportscards dashboard      # http://localhost:8501
```

See [`docs/trader_console.md`](docs/trader_console.md) for the page-by-page
reference. The console is localhost-only by design.

## Scope

- **Sport:** NBA only
- **Cards:** Panini (Prizm, Select, Mosaic, National Treasures, Donruss Optic, …)
  + Topps Basketball (2003-04 Topps Chrome vintage + 2025+ new license)
- **Sources:** eBay Browse API (free), PSA Public API (free, 100/day),
  Card Ladder Pro (CSV import day-1, enterprise API hook ready)
- **Out of scope for Phase 1:** models, indices, scouting, other sports

See `plan.md` (or `~/.claude/plans/users-peter-downloads-compass-artifact-cheerful-pearl.md`)
for the full design.
