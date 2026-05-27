# Pricing & Exit Rules — Design

**Date:** 2026-05-27
**Status:** Draft, awaiting review

## Problem

The existing factor pipeline (`factors/factor_panel.py`) and portfolio
allocator (`portfolio/construction.py`) produce `TargetPosition` rows with
`target_usd_value` per card. There is no module that converts that
"how much to allocate" into actionable trade prices: **what to bid, what to
sell at, and when to exit**. The Card Ladder CSV importer
(`ingest/cardladder.py`) is also effectively dead — Card Ladder Pro does
not expose a market-data CSV export, only a collection-upload CSV.

This spec covers the new pricing and exit-rules layer that closes the loop:

```
factor signal → target USD → bid/ask/sell targets → trade
                                                 ↓
                                          exit rules monitor
```

## Scope

In scope:
- New `src/sportscards/pricing/` package with five modules
- A `trade_targets` table persisted per (card_id, as_of_date)
- Exit-rule evaluator that runs against open positions
- Lightweight Card Ladder cert-lookup client (for the grading-EV sleeve)
- Trader Console display of bid/fair/sell per recommended card
- Deprecation of `ingest/cardladder.py` CSV-import path

Out of scope (deferred):
- Active-listing scanner (eBay Browse API arbitrage screen) — v2
- Multi-marketplace comp aggregation (PWCC, Goldin, Heritage) — v2
- Automated order placement — v2

## Data sources

- **Primary**: eBay sold transactions, already ingested into the existing
  `tx_clean` / `repeat_sales` tables. No new ingestion required.
- **Secondary**: Card Ladder cert API/page, queried on-demand for a single
  cert number. Used only by the grading-EV sleeve to confirm grade-ratio
  premia. Not persisted in a market-data table.
- **Manual**: The trader opens Card Ladder in a browser for visual sanity
  check on individual cards. Not a code path.

## Pricing model

Adopted from a synthesis of Card Ladder's "CL Value" methodology
(index-anchoring), Corwin-Schultz (2012) implicit spread estimator, and
Pederson's liquidity premium framework.

### Step 1 — Index-anchored fair value

For each card, take the most recent confirmed sold price and reproject it
forward via the relevant player/parallel index:

```
fair_index = last_sold_price(card)
           × index(player|parallel, t_now)
           / index(player|parallel, t_last_sold)
```

The player/parallel indexes already exist in `factors/index_build.py`.

### Step 2 — Recency-blended fair value

Single-card sales are sparse. Blend the index-projected price with the
hedonic cross-sectional prediction, weighted by recency confidence:

```
confidence = exp(-days_since_last_sold / τ),   τ = 30 days
fair_value = confidence × fair_index
           + (1 - confidence) × hedonic_predict(card)
```

`hedonic_predict` is the existing model in `factors/hedonic.py`.

The 30-day τ is an initial constant; a follow-up calibration task will fit
τ on holdout data.

### Step 3 — Implicit half-spread (Corwin-Schultz)

Sports cards have no live bid/ask, only transaction prints. Apply the
Corwin-Schultz (2012) two-day high-low estimator using rolling 60-day
discrete-trade max/min as a proxy for daily H/L:

```
β = E[ (ln(H_t / L_t))² + (ln(H_{t-1} / L_{t-1}))² ]
γ = (ln(H_{2-day} / L_{2-day}))²
α = (√(2β) - √β) / (3 - 2√2)
  − √(γ / (3 - 2√2))
spread_pct = 2(e^α - 1) / (1 + e^α)
half_spread = spread_pct / 2
```

The discrete-trade H/L proxy is an accepted approximation when trades are
sparse. When fewer than 6 trades exist in the 60-day window, fall back to
a tier-based default: A=0.03, B=0.05, C=0.10, D=0.20.

### Step 4 — Liquidity premium margin

Hold-period illiquidity compensation, layered on top of the half-spread:

```
liquidity_margin = {A: 0.03, B: 0.05, C: 0.10, D: 0.20}[liquidity_tier]
```

`liquidity_tier` is already produced by `factors/liquidity.py`.

### Step 5 — Final price targets

```
bid_max     = fair_value × (1 - half_spread - liquidity_margin)
sell_target = fair_value × (1 + half_spread + factor_zscore × k)
stop_loss   = fair_value × (1 - 2 × liquidity_margin)
```

Note: `stop_loss` is the tier-anchored level `fair × (1 - 2·margin)`
capped at `bid_max - margin·fair` whenever the implicit half-spread
exceeds the liquidity margin. This guarantees the strict invariant
`stop_loss < bid_max` and gives a tighter stop for cards with wide
spreads (their bid is already low — the stop must be lower still).

- `factor_zscore`: cross-sectional z-score of the card's composite factor
  score (momentum + mispricing_residual) within its sport / parallel_tier.
- `k`: gain-target multiplier, initial value 0.15 (a 1-σ factor signal →
  +15% on the sell target above fair). Calibration deferred.

## Exit rules

Evaluated per open position on every factor_panel refresh (weekly).
Exit if **any** of:

| Rule | Condition | Action |
|---|---|---|
| 1. Target hit | `last_sold >= sell_target` | Sell 50% (partial) |
| 2. Factor reversal | Card drops out of factor long decile | Sell 100% |
| 3. Time stop | `holding_days > 540` AND `last_sold < 1.1 × cost` | Sell 100% |
| 4. Price stop | `last_sold < stop_loss` | Sell 100%, re-evaluate after 30d |
| 5. Liquidity degrade | `liquidity_tier` drops ≥2 grades from entry | Sell 100% |

Each evaluation writes an `exit_signal` row with the triggered rule and
recommended action; the trader confirms in the Trader Console before
execution. No automated order placement in this scope.

## Module layout

```
src/sportscards/pricing/
├── __init__.py
├── fair_value.py        # Steps 1-2: index anchor + recency blend
├── implicit_spread.py   # Step 3: Corwin-Schultz with discrete-trade H/L
├── targets.py           # Steps 4-5: bid_max / sell_target / stop_loss
├── exit_rules.py        # 5-rule evaluator over open positions
└── cert_lookup.py       # Card Ladder cert-API client (grading-EV use only)
```

### Dependencies

- `fair_value.py` reads from: `tx_clean`, `index_build`, `hedonic`
- `implicit_spread.py` reads from: `tx_clean` (sold history)
- `targets.py` reads from: `fair_value`, `implicit_spread`,
  `factors.factor_panel`
- `exit_rules.py` reads from: `targets`, `factor_panel`, open positions
- `cert_lookup.py` is standalone, called by `factors/grading_ev.py`

### Public interfaces

```python
# pricing/targets.py
@dataclass(frozen=True)
class TradeTargets:
    card_id: int
    as_of_date: date
    fair_value: Decimal
    bid_max: Decimal
    sell_target: Decimal
    stop_loss: Decimal
    confidence: float           # 0..1, from recency blend
    half_spread_pct: Decimal
    liquidity_margin_pct: Decimal

def compute_targets(
    session: Session,
    card_id: int,
    as_of: date,
) -> TradeTargets: ...

def persist_targets_for_panel(
    session: Session,
    as_of: date,
) -> int: ...  # writes to trade_targets, returns row count
```

```python
# pricing/exit_rules.py
@dataclass(frozen=True)
class ExitSignal:
    holding_id: int
    rule_triggered: Literal[
        "target_hit", "factor_reversal", "time_stop",
        "price_stop", "liquidity_degrade",
    ]
    recommended_action: Literal["sell_50pct", "sell_100pct"]
    as_of_date: date
    notes: str

def evaluate_open_positions(
    session: Session,
    as_of: date,
) -> list[ExitSignal]: ...
```

## Database schema

New table `trade_targets`:

```sql
CREATE TABLE trade_targets (
    card_id              BIGINT      NOT NULL,
    as_of_date           DATE        NOT NULL,
    fair_value           NUMERIC(12,2) NOT NULL,
    bid_max              NUMERIC(12,2) NOT NULL,
    sell_target          NUMERIC(12,2) NOT NULL,
    stop_loss            NUMERIC(12,2) NOT NULL,
    confidence           NUMERIC(4,3) NOT NULL,
    half_spread_pct      NUMERIC(6,4) NOT NULL,
    liquidity_margin_pct NUMERIC(6,4) NOT NULL,
    PRIMARY KEY (card_id, as_of_date)
);
SELECT create_hypertable('trade_targets', 'as_of_date');
```

New table `exit_signal`:

```sql
CREATE TABLE exit_signal (
    id                 BIGSERIAL PRIMARY KEY,
    holding_id         BIGINT NOT NULL REFERENCES portfolio_holdings(holding_id),
    rule_triggered     TEXT   NOT NULL,
    recommended_action TEXT   NOT NULL,
    as_of_date         DATE   NOT NULL,
    notes              TEXT,
    resolved_at        TIMESTAMPTZ,
    UNIQUE (holding_id, rule_triggered, as_of_date)
);
CREATE INDEX ix_exit_signal_unresolved ON exit_signal(resolved_at) WHERE resolved_at IS NULL;
```

A holdings table already exists from migration 0010:
`portfolio_holdings(holding_id, card_id, cert_number, slab_grader,
slab_grade, acquired_at, acquired_cost_usd, channel, status, sold_at,
sold_proceeds_usd)`. We extend it with two columns needed for the
factor-reversal and liquidity-degrade exit rules:

```sql
ALTER TABLE portfolio_holdings
    ADD COLUMN entry_factor_decile  SMALLINT,
    ADD COLUMN entry_liquidity_tier CHAR(1);
```

`exit_signal.position_id` therefore references
`portfolio_holdings(holding_id)`. Rename accordingly throughout this
spec: read `position` as `portfolio_holdings`, `position_id` as
`holding_id`.

Entries are recorded manually by the trader after a fill (this matches
the existing Trader Console flow); automated wiring from order
execution is deferred.

## Flow integration

A new Prefect flow `pricing_refresh_flow` runs after `factor_panel_flow`:

```
factor_panel_flow  (weekly)
       ↓
pricing_refresh_flow
  ├── pricing.targets.persist_targets_for_panel(as_of)
  └── pricing.exit_rules.evaluate_open_positions(as_of)
            → writes exit_signal rows
```

## Trader Console additions

`reports/queries.py` exposes:
- `get_trade_targets(card_ids, as_of)` → DataFrame for display
- `get_open_exit_signals()` → unresolved signals, with one-click resolve

`reports/render.py` adds:
- Per-card panel showing `bid_max | fair | sell_target | stop_loss` with
  confidence dot indicator
- "Exit Signals" tab listing unresolved signals with action buttons

## Deprecation

`ingest/cardladder.py`:
- Remove `import_sales_csv` and the `sportscards cardladder import` CLI
  subcommand entirely.
- Move the skeleton `CardLadderApiClient` to `pricing/cert_lookup.py` and
  flesh it out for cert-based lookup.
- Delete `ingest/cardladder.py` after migration.
- Update `docs/trader_console.md` to remove references to CSV import.

## Testing

- `tests/pricing/test_fair_value.py` — golden-file tests with synthetic
  index + sold history; covers recency blend at confidence boundaries
- `tests/pricing/test_implicit_spread.py` — Corwin-Schultz against the
  closed-form values from the 2012 paper's worked examples
- `tests/pricing/test_targets.py` — end-to-end on synthetic_data; assert
  `bid_max < fair_value < sell_target` invariant, monotonicity in
  factor_zscore and liquidity_tier
- `tests/pricing/test_exit_rules.py` — one test per rule, plus a
  multi-rule fixture that fires the highest-severity action
- `tests/pricing/test_cert_lookup.py` — recorded VCR cassette for one
  PSA cert; covers 404 and rate-limit handling

## Calibration follow-ups (deferred)

These are explicitly out of scope here; tracked as follow-ups:
1. Fit `τ` (recency time constant) on holdout
2. Fit `k` (sell-target gain multiplier) on holdout
3. Re-fit `liquidity_margin` lookup against realized round-trip costs
4. Backtest the 5 exit rules in isolation; measure marginal Sharpe

## Open risks

- **eBay shill-bid contamination** of `tx_clean`: outliers can pull
  `last_sold` upward. Mitigation: when blending, winsorize `last_sold` at
  the 95th percentile of the 90-day distribution before reprojection.
- **Sparse cards with no recent sold and missing hedonic features**:
  `fair_value` will be undefined. These cards are excluded from
  `trade_targets` and surfaced in a "missing pricing" diagnostic.
- **Index staleness for niche players**: if the player index itself hasn't
  updated in >14 days, drop confidence to 0 and rely entirely on hedonic.
