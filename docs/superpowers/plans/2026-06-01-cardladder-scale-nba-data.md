# Card Ladder Scale NBA Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a repeatable Card Ladder first, eBay second NBA data pipeline that lets an agent operate Chrome, collect sales history, import it into localhost, and turn those comps into player/card scouting signals.

**Architecture:** Treat Card Ladder Sales History as the trusted paid comp source and import it through the existing `cardladder_manual` path into `tx_raw` and `tx_clean`. Add an operator queue that defines exactly which NBA player/card searches to run, a browser-capture workflow that records every batch, and reporting pages that show ingest coverage, parser gaps, and next recommended searches.

**Tech Stack:** Python 3.14, SQLAlchemy, PostgreSQL, Streamlit, pytest, existing `sportscards` CLI, Chrome/Card Ladder Pro via Computer Use, optional eBay Browse or Marketplace Insights API once permissions are resolved.

---

## Data We Need

### Card Ladder Sales History Fields

Every imported sale must preserve these fields:

- `source`: always `cardladder_manual` for Card Ladder rows.
- `platform`: `EBAY`, `FANATICS WEEKLY`, `FANATICS BUY NOW`, `GOLDIN`, `ALT`, `MY SLABS`, `CARD HOBBY`, `HERITAGE`, `PRISTINE AUCTION`.
- `seller_or_channel`: eBay seller name or auction channel where visible.
- `raw_title`: exact listing title after platform/seller prefix cleanup.
- `price_usd`: numeric sold price.
- `sold_at`: sale date.
- `listing_type`: `Auction`, `Best Offer`, `Fixed Price`, `Buy Now`.
- `verified`: boolean from Card Ladder verified marker.
- `external_sale_id`: saleId from the Card Ladder link when accessible in the page tree.
- `search_query`: the exact Card Ladder query used.
- `captured_at`: local timestamp.
- `capture_window`: visible page slice number for that query.

### Card Identity Fields

These fields should be parsed or mapped into `card_master`:

- `player_name`
- `season_or_year`: examples `2013`, `2018-19`, `2023-24`.
- `manufacturer`: `Panini`, `Topps`, `Bowman`, `Upper Deck`.
- `product`: `Prizm`, `Select`, `Optic`, `Mosaic`, `National Treasures`, `Topps Chrome`.
- `parallel`: `Base`, `Silver`, `Green`, `Mosaic`, `Blue Disco`, `Gold`, `Black`, `Refractor`.
- `card_number`
- `serial_number`: `/10`, `/25`, `/99`, etc.
- `grade_company`: `PSA`, `BGS`, `SGC`, `CGC`, or raw.
- `grade_value`: `10`, `9.5`, `9`, etc.
- `is_rookie`
- `rookie_flag_confidence`: `title`, `known_flagship_map`, or `unknown`.
- `spec_key`: normalized key such as `giannis-antetokounmpo|2013|panini|prizm|base|290|psa|10`.

### Player Scouting Fields

For deciding who to buy, not just which card to buy, we need:

- `player_name`, `br_slug`, `nba_id` if available.
- `draft_year`, `draft_pick`, `team`, `position`, `age`.
- Current and historical per-game stats.
- Advanced stats: BPM, VORP, PER, WS/48, TS%, USG%, AST%, REB%, STL%, BLK%.
- Availability: games played, minutes, injuries, role stability.
- Prospect signals for rookies: draft consensus, college/international stats, combine, age curve.
- Market attention signals: Card Ladder sale count, median price, record sale, 30/90 day momentum, liquidity.
- Catalyst signals: awards, playoffs, trades, contract extension, all-star selections, rookie milestones.

## Search Universe

Use these Card Ladder query tiers. The agent should run one visible page per query first, then revisit high-liquidity queries for deeper pages.

### Tier A: Market Anchors

Run these searches daily or whenever Card Ladder is open:

```text
Michael Jordan Fleer PSA 10
LeBron James Topps Chrome PSA 10
Kobe Bryant Topps Chrome PSA 10
Stephen Curry Topps Chrome PSA 10
Stephen Curry Prizm PSA 10
Kevin Durant Topps Chrome PSA 10
Giannis Antetokounmpo Prizm PSA 10
Nikola Jokic Prizm PSA 10
Luka Doncic Prizm PSA 10
Victor Wembanyama Prizm PSA 10
```

### Tier B: Modern Blue Chips

Run these after Tier A has at least 100 rows per player:

```text
Anthony Edwards Prizm PSA 10
Shai Gilgeous-Alexander Prizm PSA 10
Jayson Tatum Prizm PSA 10
Ja Morant Prizm PSA 10
Zion Williamson Prizm PSA 10
Paolo Banchero Prizm PSA 10
Chet Holmgren Prizm PSA 10
Tyrese Haliburton Prizm PSA 10
Jalen Brunson Prizm PSA 10
Devin Booker Prizm PSA 10
```

### Tier C: Rookie/Prospect Watch

Run these weekly during draft season and early NBA season:

```text
Cooper Flagg Bowman Chrome PSA 10
Cooper Flagg Topps Chrome PSA 10
Dylan Harper Bowman Chrome PSA 10
Ace Bailey Bowman Chrome PSA 10
VJ Edgecombe Topps Now PSA 10
Kon Knueppel Bowman Chrome PSA 10
Tre Johnson Bowman Chrome PSA 10
Jeremiah Fears Bowman Chrome PSA 10
```

### Tier D: Card-Type Deep Dives

Run these when a player shows positive player signal:

```text
{player} Prizm Silver PSA 10
{player} Prizm Base PSA 10
{player} Prizm Color PSA 10
{player} Select Courtside PSA 10
{player} Optic Holo PSA 10
{player} National Treasures RPA
{player} Topps Chrome Refractor PSA 10
```

## File Structure

- Modify `src/sportscards/ingest/cardladder_manual.py`
  - Add platform support for `PRISTINE AUCTION` and `FANATICS BUY NOW`.
  - Strip `ALT (CONFIRMED PAID)` from `raw_title` while preserving `verified=True`.
  - Add optional `search_query` and `external_sale_id` metadata into raw payload.
- Create `src/sportscards/ingest/cardladder_queue.py`
  - Defines query tiers, active queue, batch status, and coverage targets.
- Create `src/sportscards/ingest/cardladder_capture.py`
  - Converts Card Ladder accessibility-tree link descriptions into rows without scraping private APIs.
- Modify `reports/app/pages/3_📥_Ingest.py`
  - Add “Next Card Ladder Searches” and “Paste/Import Current Capture” sections.
- Modify `src/sportscards/reports/queries.py`
  - Add Card Ladder coverage by query/player/platform and latest capture timestamp.
- Create `tests/test_cardladder_parser_platforms.py`
  - Parser coverage for platform variants observed in live Card Ladder.
- Create `tests/test_cardladder_queue.py`
  - Queue ordering and coverage target tests.
- Create `tests/test_cardladder_capture.py`
  - Accessibility description to import payload tests.
- Modify `docs/trader_console.md`
  - Document the agent-operated Card Ladder ingest workflow.

## Task 1: Patch Parser For Live Card Ladder Variants

**Files:**
- Modify: `src/sportscards/ingest/cardladder_manual.py`
- Create: `tests/test_cardladder_parser_platforms.py`

- [ ] **Step 1: Write failing parser tests**

```python
from decimal import Decimal

from sportscards.ingest.cardladder_manual import parse_cardladder_text


def test_pristine_auction_parses_as_platform():
    rows = parse_cardladder_text(
        "PRISTINE AUCTION 2014 Panini Prizm Nikola Jokic #253 PSA 10 Price $1,763.99 verified Auction Jun 1, 2026"
    )
    assert len(rows) == 1
    assert rows[0].platform == "PRISTINE AUCTION"
    assert rows[0].price_usd == Decimal("1763.99")
    assert rows[0].verified is True
    assert rows[0].listing_type == "Auction"


def test_alt_confirmed_paid_prefix_is_not_title():
    rows = parse_cardladder_text(
        "ALT (CONFIRMED PAID) 2023-24 Panini Prizm Victor Wembanyama Silver PSA 10 Price $1,100.00 Fixed Price May 31, 2026"
    )
    assert len(rows) == 1
    assert rows[0].platform == "ALT"
    assert rows[0].verified is True
    assert rows[0].raw_title == "2023-24 Panini Prizm Victor Wembanyama Silver PSA 10"
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
uv run pytest tests/test_cardladder_parser_platforms.py -q
```

Expected: first test parses zero rows or wrong platform; second leaves `(CONFIRMED PAID)` inside `raw_title`.

- [ ] **Step 3: Implement the parser patch**

Change `PLATFORMS` to include longer variants before shorter variants:

```python
PLATFORMS = (
    "PRISTINE AUCTION",
    "FANATICS WEEKLY",
    "FANATICS BUY NOW",
    "FANATICS",
    "CARD HOBBY",
    "MY SLABS",
    "HERITAGE",
    "GOLDIN",
    "EBAY",
    "ALT",
)
```

Add a cleanup helper:

```python
CONFIRMED_PAID_RE = re.compile(r"^\(?CONFIRMED PAID\)?\s+", re.I)


def _strip_status_prefix(title: str) -> str:
    return CONFIRMED_PAID_RE.sub("", title.strip()).strip()
```

Call `_strip_status_prefix` inside `_extract_title` after platform removal and before `_strip_seller_prefix`.

- [ ] **Step 4: Run parser tests**

Run:

```bash
uv run pytest tests/test_cardladder_parser_platforms.py tests/test_cardladder_manual_import.py -q
```

Expected: all parser/import tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/sportscards/ingest/cardladder_manual.py tests/test_cardladder_parser_platforms.py
git commit -m "fix: parse live card ladder platform variants"
```

## Task 2: Add Card Ladder Search Queue

**Files:**
- Create: `src/sportscards/ingest/cardladder_queue.py`
- Create: `tests/test_cardladder_queue.py`

- [ ] **Step 1: Write queue tests**

```python
from sportscards.ingest.cardladder_queue import next_searches, query_tiers


def test_query_tiers_include_required_anchor_players():
    tiers = query_tiers()
    tier_a = [q.query for q in tiers if q.tier == "A"]
    assert "LeBron James Topps Chrome PSA 10" in tier_a
    assert "Stephen Curry Prizm PSA 10" in tier_a
    assert "Victor Wembanyama Prizm PSA 10" in tier_a


def test_next_searches_prioritizes_undercovered_tier_a():
    rows = next_searches(
        coverage={
            "LeBron James Topps Chrome PSA 10": 120,
            "Stephen Curry Prizm PSA 10": 20,
            "Victor Wembanyama Prizm PSA 10": 0,
        },
        limit=3,
    )
    assert rows[0].query == "Victor Wembanyama Prizm PSA 10"
    assert rows[1].query == "Stephen Curry Prizm PSA 10"
```

- [ ] **Step 2: Run failing queue tests**

Run:

```bash
uv run pytest tests/test_cardladder_queue.py -q
```

Expected: import error for missing `cardladder_queue`.

- [ ] **Step 3: Implement queue module**

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class CardLadderQuery:
    tier: str
    query: str
    target_rows: int
    cadence: str


def query_tiers() -> list[CardLadderQuery]:
    return [
        CardLadderQuery("A", "Michael Jordan Fleer PSA 10", 100, "daily"),
        CardLadderQuery("A", "LeBron James Topps Chrome PSA 10", 100, "daily"),
        CardLadderQuery("A", "Kobe Bryant Topps Chrome PSA 10", 100, "daily"),
        CardLadderQuery("A", "Stephen Curry Topps Chrome PSA 10", 100, "daily"),
        CardLadderQuery("A", "Stephen Curry Prizm PSA 10", 100, "daily"),
        CardLadderQuery("A", "Kevin Durant Topps Chrome PSA 10", 100, "daily"),
        CardLadderQuery("A", "Giannis Antetokounmpo Prizm PSA 10", 100, "daily"),
        CardLadderQuery("A", "Nikola Jokic Prizm PSA 10", 100, "daily"),
        CardLadderQuery("A", "Luka Doncic Prizm PSA 10", 100, "daily"),
        CardLadderQuery("A", "Victor Wembanyama Prizm PSA 10", 100, "daily"),
        CardLadderQuery("B", "Anthony Edwards Prizm PSA 10", 80, "weekly"),
        CardLadderQuery("B", "Shai Gilgeous-Alexander Prizm PSA 10", 80, "weekly"),
        CardLadderQuery("B", "Jayson Tatum Prizm PSA 10", 80, "weekly"),
        CardLadderQuery("B", "Paolo Banchero Prizm PSA 10", 80, "weekly"),
        CardLadderQuery("B", "Chet Holmgren Prizm PSA 10", 80, "weekly"),
        CardLadderQuery("C", "Cooper Flagg Bowman Chrome PSA 10", 50, "weekly"),
        CardLadderQuery("C", "Dylan Harper Bowman Chrome PSA 10", 50, "weekly"),
        CardLadderQuery("C", "Ace Bailey Bowman Chrome PSA 10", 50, "weekly"),
    ]


def next_searches(coverage: dict[str, int], limit: int = 10) -> list[CardLadderQuery]:
    tier_rank = {"A": 0, "B": 1, "C": 2, "D": 3}
    rows = sorted(
        query_tiers(),
        key=lambda q: (
            tier_rank[q.tier],
            coverage.get(q.query, 0) / q.target_rows,
            coverage.get(q.query, 0),
            q.query,
        ),
    )
    return rows[:limit]
```

- [ ] **Step 4: Run queue tests**

Run:

```bash
uv run pytest tests/test_cardladder_queue.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/sportscards/ingest/cardladder_queue.py tests/test_cardladder_queue.py
git commit -m "feat: add card ladder search queue"
```

## Task 3: Add Coverage Queries For Localhost

**Files:**
- Modify: `src/sportscards/reports/queries.py`
- Modify: `reports/app/Home.py`
- Test: `tests/test_reports.py`

- [ ] **Step 1: Write coverage query test**

```python
def test_cardladder_coverage_summary_counts_rows_by_query(migrated_db):
    from sqlalchemy import text
    from sportscards.db.session import engine
    from sportscards.reports import queries

    with engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tx_raw (source, external_id, payload) VALUES "
                "('cardladder_manual', 'q1', '{\"search_query\":\"Stephen Curry Prizm PSA 10\"}'::jsonb), "
                "('cardladder_manual', 'q2', '{\"search_query\":\"Stephen Curry Prizm PSA 10\"}'::jsonb), "
                "('cardladder_manual', 'q3', '{\"search_query\":\"Giannis Antetokounmpo Prizm PSA 10\"}'::jsonb)"
            )
        )
    rows = queries.cardladder_coverage_summary(engine=engine())
    assert rows.loc[rows.search_query == "Stephen Curry Prizm PSA 10", "rows"].iloc[0] == 2
```

- [ ] **Step 2: Add query implementation**

```python
def cardladder_coverage_summary(engine: Engine | None = None) -> pd.DataFrame:
    eng = _engine(engine)
    _require(eng, "tx_raw", "Phase 1")
    sql = text(
        """
        SELECT
          COALESCE(payload->>'search_query', 'unknown') AS search_query,
          COUNT(*)::int AS rows,
          MAX(ingested_at) AS latest_ingested_at
        FROM tx_raw
        WHERE source = 'cardladder_manual'
        GROUP BY 1
        ORDER BY rows DESC, search_query ASC
        """
    )
    return pd.read_sql(sql, eng)
```

- [ ] **Step 3: Add Home UI coverage table**

On `reports/app/Home.py`, below Data health, render:

```python
coverage = queries.cardladder_coverage_summary()
if not coverage.empty:
    st.subheader("Card Ladder coverage")
    st.dataframe(coverage, use_container_width=True, hide_index=True)
```

- [ ] **Step 4: Run reports tests**

Run:

```bash
uv run pytest tests/test_reports.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/sportscards/reports/queries.py reports/app/Home.py tests/test_reports.py
git commit -m "feat: show card ladder coverage"
```

## Task 4: Agent-Operated Capture Loop

**Files:**
- Create: `src/sportscards/ingest/cardladder_capture.py`
- Create: `tests/test_cardladder_capture.py`
- Modify: `docs/trader_console.md`

- [ ] **Step 1: Write capture parser tests**

```python
from sportscards.ingest.cardladder_capture import capture_links_to_text


def test_capture_links_to_text_extracts_descriptions_and_sale_ids():
    links = [
        {
            "description": "EBAY - SELLER 2018-19 Panini Prizm Luka Doncic #280 PSA 10 Price $4,000.00 Auction Jun 1, 2026",
            "value": "app.cardladder.com/sales-history?q=Luka&saleId=ebay-123",
        },
        {
            "description": "FANATICS WEEKLY 2013 Panini Prizm Giannis Antetokounmpo ROOKIE #290 PSA 10 Price $720.00 verified Auction Jun 1, 2026",
            "value": "app.cardladder.com/sales-history?q=Giannis&saleId=fanatics-weekly-456",
        },
    ]
    text, sale_ids = capture_links_to_text(links)
    assert "Luka Doncic" in text
    assert "Giannis Antetokounmpo" in text
    assert sale_ids == ["ebay-123", "fanatics-weekly-456"]
```

- [ ] **Step 2: Implement capture helper**

```python
from urllib.parse import parse_qs, urlparse


def capture_links_to_text(links: list[dict[str, str]]) -> tuple[str, list[str]]:
    descriptions: list[str] = []
    sale_ids: list[str] = []
    for link in links:
        desc = str(link.get("description", "")).strip()
        value = str(link.get("value", "")).strip()
        if " Price $" not in desc:
            continue
        descriptions.append(desc)
        sale_id = parse_qs(urlparse(value).query).get("saleId", [""])[0]
        if sale_id:
            sale_ids.append(sale_id)
    return "\n".join(descriptions), sale_ids
```

- [ ] **Step 3: Document operator loop**

Add this workflow to `docs/trader_console.md`:

```markdown
### Agent-operated Card Ladder loop

1. Open Card Ladder Sales History in Chrome.
2. Run the next query from localhost coverage.
3. Read visible sale link descriptions from the accessibility tree.
4. Import descriptions through Card Ladder Paste Import.
5. Refresh Home and confirm Card Ladder row count increased.
6. Move to the next queue item.
```

- [ ] **Step 4: Run capture tests**

Run:

```bash
uv run pytest tests/test_cardladder_capture.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/sportscards/ingest/cardladder_capture.py tests/test_cardladder_capture.py docs/trader_console.md
git commit -m "feat: document card ladder capture loop"
```

## Task 5: Player Selection Dashboard

**Files:**
- Modify: `src/sportscards/reports/queries.py`
- Create or modify: `reports/app/pages/12_🏀_NBA_Scouting.py`
- Test: `tests/test_reports.py`

- [ ] **Step 1: Define scoring formula**

Use this first-pass score:

```text
player_opportunity_score =
  0.35 * stardom_percentile
+ 0.20 * catalyst_percentile
+ 0.20 * price_momentum_percentile
+ 0.15 * liquidity_percentile
- 0.10 * injury_or_low_minutes_penalty
```

If a component is missing, normalize over available components and display the missing flags.

- [ ] **Step 2: Add report query**

Add `nba_player_opportunity_board(engine=None) -> pd.DataFrame` returning:

```text
player_id
player_name
draft_year
premium
percentile_rank
catalyst_score
cardladder_rows_90d
median_price_90d
price_change_30d
opportunity_score
missing_components
```

- [ ] **Step 3: Add Streamlit page**

The page should show:

- Top opportunities table.
- Filter for rookies only.
- Filter for minimum Card Ladder rows.
- Drilldown to recent Card Ladder sales for selected player.
- Next Card Ladder searches for selected player.

- [ ] **Step 4: Run UI smoke test**

Run:

```bash
uv run pytest tests/test_reports.py -q
PYTHONPATH=/Users/peter/Sports\\ Card uv run streamlit run reports/app/Home.py --server.port 8501 --server.headless true
```

Expected: tests pass and local UI renders without import errors.

- [ ] **Step 5: Commit**

```bash
git add src/sportscards/reports/queries.py reports/app/pages tests/test_reports.py
git commit -m "feat: add nba player opportunity board"
```

## Task 6: Daily Operating Procedure

**Files:**
- Modify: `docs/trader_console.md`

- [ ] **Step 1: Add daily checklist**

```markdown
## Daily NBA Card Ladder Ingest

1. Start services:
   `docker compose up -d db`
2. Start UI:
   `PYTHONPATH='/Users/peter/Sports Card' uv run streamlit run reports/app/Home.py --server.port 8501 --server.headless true --browser.gatherUsageStats false`
3. Open Card Ladder Sales History in Chrome.
4. Run 5 Tier A searches and import one visible page per search.
5. Refresh localhost and confirm:
   - `parse failures = 0`
   - `Card Ladder rows` increased by imported row count
6. Run one Tier B or Tier C search from the opportunity board.
7. Stop only if the parser hits a new platform/format; patch parser before continuing.
```

- [ ] **Step 2: Run docs grep**

Run:

```bash
rg -n "Daily NBA Card Ladder Ingest|Agent-operated Card Ladder loop" docs/trader_console.md
```

Expected: both headings appear.

- [ ] **Step 3: Commit**

```bash
git add docs/trader_console.md
git commit -m "docs: add card ladder ingest operating procedure"
```

## Execution Notes For Chip Sessions

- Start in Plan mode.
- Create a fresh worktree before code changes:

```bash
git fetch origin
git worktree add .worktrees/cardladder-scale-task origin/main -b codex/cardladder-scale-task
cd .worktrees/cardladder-scale-task
```

- Keep data capture and code edits separate. Data capture can run in `/Users/peter/Sports Card`; code changes should happen in the worktree.
- After each task, run its exact test command and commit before moving to the next task.
- Do not wait for eBay. Keep eBay as a secondary source until Marketplace Insights or approved production Browse access is available.

## Current Baseline As Of 2026-06-01

- Localhost Trader Console is running at `http://localhost:8501/`.
- Card Ladder imports are active and verified.
- Current verified local counts after LeBron/Curry/Jokic/Giannis batches:
  - `raw_transactions`: 361
  - `clean_transactions`: 359
  - `cardladder_rows`: 160
  - `parse_failures`: 0
- Known parser gaps:
  - `PRISTINE AUCTION` currently needs parser support.
  - `ALT (CONFIRMED PAID)` should strip the status prefix from `raw_title`.

## Self-Review

- Spec coverage: the plan covers data needs, Card Ladder operator flow, parser hardening, search queue, coverage UI, player selection, and daily procedure.
- Placeholder scan: no placeholder markers or unspecified testing steps remain.
- Type consistency: queue and capture helper names are introduced before use, and all commands point to existing project tooling.
