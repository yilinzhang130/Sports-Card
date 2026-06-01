# Trader Console

The Trader Console is a localhost-only multi-page Streamlit app that exposes
every `sportscards` CLI workflow behind a browser UI, plus a few operator
flows the CLI doesn't serve well (file upload, manual parse triage, manual
portfolio overrides, holdings ledger).

Launch:

```bash
sportscards dashboard       # opens http://localhost:8501
```

The CLI is and remains the canonical operator surface. The UI is a
convenience.

## Safety model

- **Localhost-only.** `reports/app/_components/auth.py` inspects the inbound
  `Host` header. Non-localhost requests render a red block page and call
  `st.stop()`. There is no auth layer, no TLS — the console is meant to run
  on the operator's laptop only.
- **Confirmation toggles.** Every write form requires the "I understand
  this writes to the DB" checkbox before the submit button enables.
- **Audit log.** Every destructive op writes a row to `audit_log`
  (`action`, `payload_json`, `actor`, `at`). Inspect it with
  `select * from audit_log order by at desc`.
- **Single-worker job runner.** Long-running operations run in a
  `ThreadPoolExecutor(max_workers=1)` so the operator can never accidentally
  parallelize a heavy fit on their laptop. Each job persists to
  `model_run_log` so progress survives page reloads.

## Pages

### Home

Data-health snapshot (raw vs. clean tx, parse-failure count, etc.).
No writes.

### 📊 Market

Read-only views ported from the legacy single-file dashboard:
repeat-sales index, mispricing leaderboard, prospects, forward prospects,
factor panel. Each tab degrades gracefully ("coming with phase X") when its
backing table isn't populated yet.

### 💼 Portfolio

- **Target weights**: `build_portfolio(...)` then `apply_overrides(...)`.
  Per-row inputs let you set a `portfolio_overrides` row. Audit-logged.
- **NAV chart**: latest `backtest_runs` NAV series.
- **Holdings ledger**: `portfolio_holdings` browser + record-buy and
  mark-sold forms. Audit-logged.
- **Trade list**: diff target weights against current holdings (cost basis);
  downloadable CSV. Audit-logged.

Overrides set here are honored by `sportscards portfolio plan` from the
terminal — that command warns on stderr for every override applied.

### 📥 Ingest

- Auction-house CSV upload (Goldin / Heritage / Fanatics Collect) →
  `import_auction_csv(path, house)`
- Card Ladder paste import → paste visible Sales History rows, preview parsed
  fields, then import confirmed rows into `tx_raw` with
  `source='cardladder_manual'`; best-effort parsed rows also enter `tx_clean`.
- Quick sale entry → manually enter one Card Ladder comp when copying a full
  Sales History row is not convenient.
- eBay sold-listings ingest → `ingest_sold(...)` (button auto-disables when
  `EBAY_CLIENT_ID` is missing from `.env`)
- PSA pop snapshot → `daily_psa_pop_flow()`

Card Ladder import is manual-only. It does not scrape Card Ladder, use browser
cookies, or call private APIs. The operator copies visible Sales History rows,
previews parsed fields locally, then imports confirmed rows.

#### Agent-operated Card Ladder loop

1. Open Card Ladder Sales History in Chrome.
2. Run the next query from localhost coverage or the Card Ladder search queue.
3. Read visible sale link descriptions from the browser accessibility tree.
4. Convert those link descriptions with `cardladder_capture.capture_links_to_sales(...)`.
5. Import the resulting rows through `cardladder_manual_import(...)`, preserving `search_query` and `saleId`.
6. Refresh Home and confirm Card Ladder row count and coverage increased.
7. Move to the next queue item.

All write flows spawn background jobs (see `model_run_log` for status).

### 🔧 Parse Triage

Table of the most recent 200 `parse_failures` joined to `tx_raw`. For each,
fill in card_id / grader / grade / price / sold_at and submit → creates a
`tx_clean` row with `parser_method='manual'` and deletes the
`parse_failures` row. Atomic per row. Audit-logged.

A "Re-run LLM" button submits `parse_pending_flow(allow_llm=True)` via the
job runner for batch re-parsing of the remaining failures.

### 🃏 Master Data

Three tabs:
- **Cards**: browse `card_master` with manufacturer/set/year filters; add
  new cards. Inserts go straight into the table — no migration needed.
- **Players**: browse `player_master`; add new players.
- **PSA spec mapping**: cards in `psa_priority.yaml` whose `psa_spec_id` is
  `"TBD"`. Optional "Lookup by cert#" calls `PsaClient.get_cert(cert)` to
  pre-fill the SpecID. "Save" writes back to `psa_priority.yaml` (atomic
  write via tempfile + `os.replace`) and inserts a `cardladder_spec_map`
  row if a Card Ladder spec_id is also provided.

### 🧪 Models

Five forms — each submits a background job and renders its `summary_json`:
- Fit hedonic (train_end + synthetic checkbox)
- Build repeat-sales index (bucket + grade tiers + eras + replace)
- Compute factor panel (as_of)
- Fit scouting (PRISM) model (start/end years)
- Score scouting class (draft_year + season + as_of)

Recent-runs table at the bottom from `model_run_log`.

### 📅 Catalysts

Placeholder. Will be filled in when the catalyst-events chip merges.

### 📈 Backtest

Form: start, end, AUM, rebalance frequency days. Submit spawns a
`run_backtest` job. On success the page renders the NAV chart + metrics
(total return, drawdown, IR, turnover, fee drag) from `summary_json`.
History table of `backtest_runs` with a selector to re-render any past
run's chart.

## Tables introduced by the Trader Console (migration 0008)

| Table | Purpose |
|---|---|
| `portfolio_overrides` | Per-card weight overrides applied to portfolio plan output. |
| `portfolio_holdings` | Operator-recorded cost-basis ledger. |
| `model_run_log` | One row per background job; status/summary/error. |
| `audit_log` | One row per destructive operator action. |

## Architecture

```
reports/app/
├── Home.py                       # landing + data health
├── pages/
│   ├── 1_📊_Market.py
│   ├── 2_💼_Portfolio.py
│   ├── 3_📥_Ingest.py
│   ├── 4_🔧_Parse_Triage.py
│   ├── 5_🃏_Master_Data.py
│   ├── 6_🧪_Models.py
│   ├── 7_📅_Catalysts.py
│   ├── 8_📈_Backtest.py
│   └── 9_💰_Pricing.py
└── _components/
    ├── auth.py        # localhost guard
    ├── ui.py          # confirm_toggle, job_badge
    ├── jobs.py        # ThreadPoolExecutor + model_run_log persistence
    ├── audit.py       # write_audit
    ├── actions.py     # job-runnable wrappers around CLI-backing functions
    ├── overrides.py   # portfolio_overrides CRUD
    ├── holdings.py    # portfolio_holdings CRUD
    └── psa_yaml.py    # safe read/write of psa_priority.yaml
```

Action wrappers in `_components/actions.py` are thin shims around the same
functions the CLI subcommands call — no business logic is duplicated.

## Pricing & Exit Signals

The pricing module turns factor recommendations into actionable trade
targets. After each factor-panel refresh, the `pricing-refresh` flow
writes one row per recommended card to `trade_targets` and emits
`exit_signal` rows for any open holding that trips an exit rule.

- **Trade targets** per card (`bid_max | fair_value | sell_target |
  stop_loss`) plus a confidence score (recency of last comp).
- **Exit signals** appear in the "💰 Pricing" page of the dashboard.
  Each unresolved signal has a one-click Resolve action.

Card Ladder is no longer ingested via CSV (Card Ladder Pro doesn't
expose a market-data CSV export). The browser remains the trader's
manual sanity check; programmatic queries go through the cert lookup
client (`pricing/cert_lookup.py`) used by the grading-EV sleeve.
