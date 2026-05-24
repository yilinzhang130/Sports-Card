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
```

## Scope

- **Sport:** NBA only
- **Cards:** Panini (Prizm, Select, Mosaic, National Treasures, Donruss Optic, …)
  + Topps Basketball (2003-04 Topps Chrome vintage + 2025+ new license)
- **Sources:** eBay Browse API (free), PSA Public API (free, 100/day),
  Card Ladder Pro (CSV import day-1, enterprise API hook ready)
- **Out of scope for Phase 1:** models, indices, scouting, other sports

See `plan.md` (or `~/.claude/plans/users-peter-downloads-compass-artifact-cheerful-pearl.md`)
for the full design.
