# Grading EV Optionality (raw → PSA 10) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Grading-EV (Collector's-Edge–style) model that, for each card, estimates the expected value of buying raw and submitting to PSA vs. buying an already-graded PSA 10. Persist daily EVs, expose CLI + dashboard + opt-in portfolio sleeve.

**Architecture:** New module `sportscards.factors.grading_ev` computes `EV = gem_rate × P10 + (1-gem_rate) × P9 − grade_cost − raw_price`. `gem_rate` is estimated from `pop_snapshots` with Beta(α,β) shrinkage toward a universe prior, plus an optional 90d-vs-365d trend adjustment. Hedonic prices (`factors.hedonic.predict`) supply P10/P9 fair value net of eBay fees. Raw prices come from `tx_clean` rows where `slab_grader IS NULL` — which requires re-opening the parser path so raw cards survive into `tx_clean` as `parser_method='regex_raw'`. Results land in a new `grading_ev` table (TimescaleDB hypertable on `as_of_date`), a Prefect daily flow, a `sportscards ev` CLI subgroup, a Streamlit tab, and an opt-in `--grading-arbitrage-pct` sleeve in portfolio construction.

**Tech Stack:** Python, SQLAlchemy, Alembic, pandas, NumPy, XGBoost (existing hedonic), Click, Prefect 3, Streamlit, pytest, SQLite (tests) / Postgres+TimescaleDB (prod).

**Notes / spec deltas:**
- Spec says migration `0010_grading_ev.py`; correct next number is **`0006`** (current head is `0005_tx_mispricing`).
- `cost_to_grade('value_bulk')` already returns `$24.99` (post-Feb-2026), so the tier table needs no update; verify in Task 1.
- Raw cards already partially survive (regex max-confidence 0.80 → routed to LLM). We tighten this to a deterministic `regex_raw` method so they land in `tx_clean` without spending LLM credits.

---

## File Map

| Path | Action | Responsibility |
|---|---|---|
| `sql/migrations/versions/0006_grading_ev.py` | create | Alembic migration: `grading_ev` table + hypertable |
| `src/sportscards/db/models.py` | modify | Add `GradingEv` ORM class |
| `src/sportscards/parse/regex_parser.py` | modify | Emit `method="regex_raw"` + confidence ≥ 0.85 for raw cards |
| `src/sportscards/parse/router.py` | modify | Skip LLM fallback when `method == "regex_raw"` |
| `src/sportscards/factors/grading_ev.py` | create | Core model — gem_rate, EV, ranking |
| `src/sportscards/flows/daily_grading_ev.py` | create | Daily Prefect flow that persists EVs |
| `prefect.yaml` | modify | Add `daily-grading-ev` deployment at 09:00 PT |
| `src/sportscards/portfolio/construction.py` | modify | Add `grading_arbitrage_weight` field + sleeve allocation |
| `src/sportscards/cli/__main__.py` | modify | Add `ev` subgroup (`compute`, `top`) + `--grading-arbitrage-pct` |
| `src/sportscards/reports/queries.py` | modify | Add `grading_ev_leaderboard()` |
| `reports/dashboard.py` | modify | Add "Grading EV" tab |
| `tests/test_grading_ev.py` | create | Unit + integration tests |
| `tests/test_regex_parser.py` | modify | Add raw-card test cases |

---

## Task 1: Verify PSA fee schedule

**Files:**
- Read: `src/sportscards/portfolio/transaction_costs.py:17-25`

- [ ] **Step 1: Inspect tier table**

  Read `_default_grading_tiers()` and confirm:
  ```
  value_bulk = 24.99
  value = 49.99
  ```
  Both match the Feb-2026 PSA price update. No edit needed.

- [ ] **Step 2: Run sanity test**

  ```bash
  python -c "from sportscards.portfolio.transaction_costs import cost_to_grade, net_proceeds; print(cost_to_grade('value_bulk'), net_proceeds(1000, 'ebay'))"
  ```
  Expected: `24.99 867.2`  (1000 × (1 − 0.1325) − 0.30 = 867.20)

  If either differs, stop and update `_default_grading_tiers` accordingly. Otherwise skip Step 3.

- [ ] **Step 3 (only if Step 2 failed): commit any fix**

  ```bash
  git add src/sportscards/portfolio/transaction_costs.py
  git commit -m "fix(costs): correct PSA tier prices to Feb-2026 schedule"
  ```

---

## Task 2: Alembic migration — `grading_ev` table

**Files:**
- Create: `sql/migrations/versions/0006_grading_ev.py`

- [ ] **Step 1: Write the migration**

  ```python
  """grading_ev table for raw → PSA 10 optionality model.

  Revision ID: 0006
  Revises: 0005
  Create Date: 2026-05-25
  """

  from collections.abc import Sequence

  import sqlalchemy as sa
  from alembic import op

  revision: str = "0006"
  down_revision: str | None = "0005"
  branch_labels: str | Sequence[str] | None = None
  depends_on: str | Sequence[str] | None = None


  def upgrade() -> None:
      op.create_table(
          "grading_ev",
          sa.Column("card_id", sa.Integer, sa.ForeignKey("card_master.card_id"), primary_key=True),
          sa.Column("as_of_date", sa.DateTime(timezone=True), primary_key=True),
          sa.Column("grade_tier", sa.String(16), primary_key=True),
          sa.Column("gem_rate", sa.Numeric(6, 4), nullable=False),
          sa.Column("p10_price", sa.Numeric(14, 2), nullable=False),
          sa.Column("p9_price", sa.Numeric(14, 2), nullable=False),
          sa.Column("cost_to_grade", sa.Numeric(8, 2), nullable=False),
          sa.Column("raw_price", sa.Numeric(14, 2), nullable=True),
          sa.Column("ev", sa.Numeric(14, 2), nullable=False),
          sa.Column("ev_per_dollar", sa.Numeric(8, 4), nullable=True),
          sa.Column("sample_size", sa.Integer, nullable=False),
          sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
      )
      op.create_index("ix_grading_ev_ev_per_dollar", "grading_ev", ["ev_per_dollar"])
      op.execute(
          "SELECT create_hypertable('grading_ev', 'as_of_date', if_not_exists => TRUE)"
      )


  def downgrade() -> None:
      op.drop_index("ix_grading_ev_ev_per_dollar", table_name="grading_ev")
      op.drop_table("grading_ev")
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add sql/migrations/versions/0006_grading_ev.py
  git commit -m "feat(migrations): 0006 grading_ev table"
  ```

---

## Task 3: ORM model

**Files:**
- Modify: `src/sportscards/db/models.py` (append after `TxMispricing`)

- [ ] **Step 1: Add `GradingEv` class**

  ```python
  class GradingEv(Base):
      """TimescaleDB hypertable on as_of_date — daily grading optionality EVs."""

      __tablename__ = "grading_ev"

      card_id: Mapped[int] = mapped_column(ForeignKey("card_master.card_id"), primary_key=True)
      as_of_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
      grade_tier: Mapped[str] = mapped_column(String(16), primary_key=True)
      gem_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
      p10_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
      p9_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
      cost_to_grade: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
      raw_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
      ev: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
      ev_per_dollar: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), index=True)
      sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
      computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
  ```

- [ ] **Step 2: Smoke test (the in-memory fixture pattern used in tests)**

  ```bash
  python -c "from sqlalchemy import create_engine; from sportscards.db.models import Base, GradingEv; e = create_engine('sqlite:///:memory:'); Base.metadata.create_all(e); print('ok', GradingEv.__tablename__)"
  ```
  Expected: `ok grading_ev`

- [ ] **Step 3: Commit**

  ```bash
  git add src/sportscards/db/models.py
  git commit -m "feat(db): GradingEv ORM model"
  ```

---

## Task 4: Raw-card parser path

**Files:**
- Modify: `src/sportscards/parse/regex_parser.py:122-172`
- Modify: `src/sportscards/parse/router.py:17-29`
- Modify: `tests/test_regex_parser.py`

- [ ] **Step 1: Write failing test cases (raw cards)**

  Append to `tests/test_regex_parser.py`:
  ```python
  def test_raw_card_well_formed_marked_as_regex_raw():
      from sportscards.parse.regex_parser import parse_title
      r = parse_title("2018-19 Panini Prizm Luka Doncic #280 Silver Rookie RC")
      assert r.slab_grader is None
      assert r.slab_grade is None
      assert r.method == "regex_raw"
      assert r.confidence >= Decimal("0.85")  # bypass LLM fallback


  def test_raw_card_missing_card_number_stays_regex():
      from sportscards.parse.regex_parser import parse_title
      r = parse_title("2018 Panini Prizm Luka Doncic Silver")
      # no card_number → not well-formed-raw → don't bump method
      assert r.method == "regex"
      assert r.confidence < Decimal("0.85")


  def test_raw_router_does_not_call_llm(monkeypatch):
      from sportscards.parse import router as r
      called = []
      monkeypatch.setattr(r, "parse_title_llm", lambda t: called.append(t) or None)
      out = r.parse_title("2018-19 Panini Prizm Luka Doncic #280 Silver Rookie")
      assert out.method == "regex_raw"
      assert called == []
  ```

- [ ] **Step 2: Run them to verify failure**

  ```bash
  pytest tests/test_regex_parser.py -k "raw" -v
  ```
  Expected: 3 failures (method is `regex`, not `regex_raw`).

- [ ] **Step 3: Update `regex_parser.parse_title`**

  Replace the confidence block (lines 158–171) with:
  ```python
      # Confidence scoring: each strong field present buys you points.
      score = Decimal("0")
      if out.year:
          score += Decimal("0.25")
      if out.manufacturer:
          score += Decimal("0.15")
      if out.set_name:
          score += Decimal("0.25")
      if out.card_number:
          score += Decimal("0.15")
      if out.slab_grader and out.slab_grade is not None:
          score += Decimal("0.20")
      else:
          # Well-formed raw card — promote so router doesn't waste LLM credits,
          # and tag method so the persistence layer treats it as raw inventory.
          if out.year and out.manufacturer and out.set_name and out.card_number:
              out.method = "regex_raw"
              score = max(score, Decimal("0.85"))
      out.confidence = min(score, Decimal("1.000"))

      return out
  ```

- [ ] **Step 4: Update `router.parse_title`**

  Replace `router.py` body with:
  ```python
  def parse_title(title: str, *, allow_llm: bool = True) -> ParsedTitle:
      """Try regex first; fall back to DeepSeek if confidence is below floor.

      ``regex_raw`` results are deterministic and skip the LLM fallback even
      when confidence is exactly at the floor.
      """
      regex_result = parse_title_regex(title)
      if regex_result.method == "regex_raw":
          return regex_result
      if regex_result.confidence >= REGEX_CONFIDENCE_FLOOR or not allow_llm:
          return regex_result
      try:
          llm_result = parse_title_llm(title)
      except Exception as e:
          log.warning("LLM fallback failed for %r: %s", title[:80], e)
          return regex_result
      if llm_result.confidence > regex_result.confidence:
          return llm_result
      return regex_result
  ```

- [ ] **Step 5: Run tests**

  ```bash
  pytest tests/test_regex_parser.py -v
  ```
  Expected: all green (existing tests + 3 new).

- [ ] **Step 6: Verify existing-graded-card behavior unchanged**

  ```bash
  pytest tests/test_regex_parser.py tests/test_features.py -v
  ```
  Expected: all green. `parse_pending_flow` will now write raw rows with `parser_method='regex_raw'` automatically — no change needed in `parse_pending.py` because it already reads `parsed.method` and `parsed.slab_grader` may be `None`.

- [ ] **Step 7: Commit**

  ```bash
  git add src/sportscards/parse/regex_parser.py src/sportscards/parse/router.py tests/test_regex_parser.py
  git commit -m "feat(parse): keep well-formed raw cards as parser_method=regex_raw"
  ```

---

## Task 5: Core `factors/grading_ev.py` — gem-rate estimator

**Files:**
- Create: `src/sportscards/factors/grading_ev.py` (gem-rate piece only)
- Create: `tests/test_grading_ev.py` (gem-rate tests only)

- [ ] **Step 1: Write failing tests**

  ```python
  # tests/test_grading_ev.py
  from __future__ import annotations

  from datetime import datetime, timezone
  from decimal import Decimal

  import pytest
  from sqlalchemy import create_engine
  from sqlalchemy.orm import Session

  from sportscards.db.models import Base, Card, Player, PopSnapshot
  from sportscards.factors.grading_ev import (
      UNIVERSE_PRIOR,
      estimate_gem_rate,
  )


  @pytest.fixture()
  def session() -> Session:
      engine = create_engine("sqlite:///:memory:")
      Base.metadata.create_all(engine)
      s = Session(engine)
      s.add(Player(player_id=1, name="X", sport="basketball"))
      s.add(Card(card_id=1, year=2020, manufacturer="Panini", set_name="Prizm",
                 card_number="1", parallel="Base", player_id=1))
      s.commit()
      yield s
      s.close()


  def _add_pop(s: Session, card_id: int, when: datetime, psa8: int, psa9: int, psa10: int) -> None:
      for grade, n in [(Decimal("8"), psa8), (Decimal("9"), psa9), (Decimal("10"), psa10)]:
          s.add(PopSnapshot(snapshot_date=when, card_id=card_id, grader="PSA", grade=grade, pop_count=n))
      s.commit()


  def test_gem_rate_uses_latest_snapshot(session):
      now = datetime(2026, 5, 1, tzinfo=timezone.utc)
      _add_pop(session, 1, now, psa8=10, psa9=40, psa10=50)
      r = estimate_gem_rate(session, card_id=1, as_of=now)
      # 50 / (10 + 40 + 50) = 0.50, large n → minimal shrinkage
      assert Decimal("0.48") <= r.rate <= Decimal("0.52")
      assert r.sample_size == 100


  def test_gem_rate_small_sample_shrinks_toward_prior(session):
      now = datetime(2026, 5, 1, tzinfo=timezone.utc)
      _add_pop(session, 1, now, psa8=0, psa9=0, psa10=2)
      r = estimate_gem_rate(session, card_id=1, as_of=now)
      # raw rate = 1.00; with α=β=10 prior and n=2, posterior = (2+10)/(2+20) ≈ 0.545
      # → strongly pulled toward UNIVERSE_PRIOR (0.50)
      assert abs(r.rate - UNIVERSE_PRIOR) < Decimal("0.10")
      assert r.sample_size == 2


  def test_gem_rate_no_snapshot_returns_prior(session):
      now = datetime(2026, 5, 1, tzinfo=timezone.utc)
      r = estimate_gem_rate(session, card_id=1, as_of=now)
      assert r.rate == UNIVERSE_PRIOR
      assert r.sample_size == 0
  ```

- [ ] **Step 2: Verify they fail**

  ```bash
  pytest tests/test_grading_ev.py -v
  ```
  Expected: import error (module not yet created).

- [ ] **Step 3: Write the module — gem-rate section only**

  ```python
  # src/sportscards/factors/grading_ev.py
  """Grading-EV optionality model (raw → PSA 10).

  EV = gem_rate × P10_net − cost_to_grade − raw_price + (1 − gem_rate) × P9_net
  """

  from __future__ import annotations

  from dataclasses import dataclass
  from datetime import datetime, timedelta
  from decimal import Decimal

  from sqlalchemy import select
  from sqlalchemy.orm import Session

  from sportscards.db.models import PopSnapshot

  # Universe-wide PSA-10 rate among modern-era cards (set as a sensible prior;
  # revisit with empirical universe estimate when available).
  UNIVERSE_PRIOR: Decimal = Decimal("0.50")
  # Beta prior strength (α + β). Larger ⇒ stronger pull toward UNIVERSE_PRIOR
  # for small-sample cards. With α+β = 20, a card with 200 graded copies barely
  # shrinks; one with 2 graded copies is pulled almost all the way to the prior.
  PRIOR_STRENGTH: Decimal = Decimal("20")


  @dataclass(frozen=True)
  class GemRateEstimate:
      rate: Decimal
      sample_size: int  # total graded copies in latest pop snapshot
      raw_rate: Decimal | None  # un-shrunk rate (None if sample_size == 0)


  def estimate_gem_rate(
      session: Session,
      card_id: int,
      as_of: datetime,
      *,
      universe_prior: Decimal = UNIVERSE_PRIOR,
      prior_strength: Decimal = PRIOR_STRENGTH,
  ) -> GemRateEstimate:
      """Latest-snapshot PSA-10 rate with Beta(α,β) shrinkage to the prior.

      Treats every graded copy in the latest `pop_snapshots` row for the card
      as a 'submission outcome'. Known limitation: a single bulk submission
      can spike pop; switching to event-level grading outcomes is a follow-up
      once PSA exposes them.
      """
      latest_date = session.execute(
          select(PopSnapshot.snapshot_date)
          .where(PopSnapshot.card_id == card_id)
          .where(PopSnapshot.grader == "PSA")
          .where(PopSnapshot.snapshot_date <= as_of)
          .order_by(PopSnapshot.snapshot_date.desc())
          .limit(1)
      ).scalar_one_or_none()
      if latest_date is None:
          return GemRateEstimate(rate=universe_prior, sample_size=0, raw_rate=None)

      rows = session.execute(
          select(PopSnapshot.grade, PopSnapshot.pop_count)
          .where(PopSnapshot.card_id == card_id)
          .where(PopSnapshot.grader == "PSA")
          .where(PopSnapshot.snapshot_date == latest_date)
      ).all()
      pops = {Decimal(str(g)): int(n) for g, n in rows}
      psa10 = pops.get(Decimal("10"), 0)
      n = psa10 + pops.get(Decimal("9"), 0) + pops.get(Decimal("8"), 0)
      if n == 0:
          return GemRateEstimate(rate=universe_prior, sample_size=0, raw_rate=None)
      raw_rate = Decimal(psa10) / Decimal(n)
      # Beta posterior mean with prior pseudo-counts α = prior*strength, β = (1-prior)*strength.
      alpha = universe_prior * prior_strength + Decimal(psa10)
      beta = (Decimal(1) - universe_prior) * prior_strength + Decimal(n - psa10)
      shrunk = alpha / (alpha + beta)
      return GemRateEstimate(rate=shrunk.quantize(Decimal("0.0001")),
                             sample_size=n, raw_rate=raw_rate)


  def trend_adjustment(
      session: Session,
      card_id: int,
      as_of: datetime,
      *,
      universe_prior: Decimal = UNIVERSE_PRIOR,
  ) -> Decimal:
      """Multiplicative factor on gem_rate when recent 90d pop growth is
      materially gem-light relative to trailing 365d.

      Returns 1.0 when no trend signal is detectable. Returns <1.0 when the
      90d-incremental PSA-10 share is at least 5 pts below the 365d share.
      """
      def _share(start: datetime, end: datetime) -> Decimal | None:
          rows = session.execute(
              select(PopSnapshot.snapshot_date, PopSnapshot.grade, PopSnapshot.pop_count)
              .where(PopSnapshot.card_id == card_id)
              .where(PopSnapshot.grader == "PSA")
              .where(PopSnapshot.snapshot_date >= start)
              .where(PopSnapshot.snapshot_date <= end)
              .order_by(PopSnapshot.snapshot_date)
          ).all()
          if not rows:
              return None
          # Net change between first & last snapshot in the window
          first = {Decimal(str(g)): int(n) for d, g, n in rows if d == rows[0][0]}
          last = {Decimal(str(g)): int(n) for d, g, n in rows if d == rows[-1][0]}
          def total(d):
              return d.get(Decimal("10"), 0) + d.get(Decimal("9"), 0) + d.get(Decimal("8"), 0)
          delta10 = last.get(Decimal("10"), 0) - first.get(Decimal("10"), 0)
          delta_n = total(last) - total(first)
          if delta_n <= 0:
              return None
          return Decimal(delta10) / Decimal(delta_n)

      s90 = _share(as_of - timedelta(days=90), as_of)
      s365 = _share(as_of - timedelta(days=365), as_of)
      if s90 is None or s365 is None or s365 == 0:
          return Decimal("1")
      if s365 - s90 >= Decimal("0.05"):
          return (s90 / s365).quantize(Decimal("0.0001"))
      return Decimal("1")
  ```

- [ ] **Step 4: Run tests**

  ```bash
  pytest tests/test_grading_ev.py -v
  ```
  Expected: 3 pass.

- [ ] **Step 5: Commit**

  ```bash
  git add src/sportscards/factors/grading_ev.py tests/test_grading_ev.py
  git commit -m "feat(factors): gem_rate estimator with Beta shrinkage"
  ```

---

## Task 6: `compute_grading_ev` + `rank_grading_candidates`

**Files:**
- Modify: `src/sportscards/factors/grading_ev.py`
- Modify: `tests/test_grading_ev.py`

- [ ] **Step 1: Add failing tests for EV computation**

  Append to `tests/test_grading_ev.py`:
  ```python
  from sportscards.factors.grading_ev import (
      GradingEV,
      compute_grading_ev,
      rank_grading_candidates,
  )
  from sportscards.db.models import TxRaw, TxClean


  def _planted_card(s, card_id, hedonic_p10, hedonic_p9, gem_rate):
      """Patch the hedonic-prediction hook so tests don't need a fitted model."""
      from sportscards.factors import grading_ev as ge
      ge._HEDONIC_OVERRIDES[card_id] = (Decimal(hedonic_p10), Decimal(hedonic_p9))
      # Implant pop snapshot that yields ~gem_rate at a sample-size large enough
      # to avoid shrinkage.
      total = 1000
      psa10 = int(gem_rate * total)
      psa9 = total - psa10
      _add_pop(s, card_id, datetime(2026, 5, 1, tzinfo=timezone.utc),
               psa8=0, psa9=psa9, psa10=psa10)


  def _add_raw_comp(s, card_id, price):
      raw = TxRaw(source="ebay", external_id=f"x-{card_id}-{price}",
                  raw_title=f"raw-{card_id}", raw_price=Decimal(price),
                  sold_at=datetime(2026, 5, 1, tzinfo=timezone.utc))
      s.add(raw); s.flush()
      s.add(TxClean(raw_id=raw.raw_id, card_id=card_id, slab_grader=None,
                    slab_grade=None, price_usd=Decimal(price),
                    sold_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
                    parser_confidence=Decimal("0.90"),
                    parser_method="regex_raw"))
      s.commit()


  def test_compute_grading_ev_matches_formula(session):
      # gem=0.20, P10=1000, P9=100, cost=24.99 (value_bulk), raw=80
      _planted_card(session, 1, hedonic_p10=1000, hedonic_p9=100, gem_rate=0.20)
      _add_raw_comp(session, 1, "80")
      ev = compute_grading_ev(session, card_id=1,
                              as_of=datetime(2026, 5, 1, tzinfo=timezone.utc))
      # net P10 = 1000*(1-0.1325)-0.30 = 867.20 ; net P9 = 100*(1-0.1325)-0.30 = 86.45
      # EV = 0.20*867.20 + 0.80*86.45 - 24.99 - 80 = 173.44 + 69.16 - 104.99 = 137.61
      assert abs(ev.ev - Decimal("137.61")) < Decimal("0.50")
      assert ev.raw_price == Decimal("80")
      assert ev.sample_size == 1000


  def test_rank_excludes_negative_ev(session):
      # Card 2: positive EV
      session.add(Card(card_id=2, year=2020, manufacturer="Panini", set_name="Prizm",
                       card_number="2", parallel="Base", player_id=1))
      session.add(Card(card_id=3, year=2020, manufacturer="Panini", set_name="Prizm",
                       card_number="3", parallel="Base", player_id=1))
      session.commit()
      _planted_card(session, 2, hedonic_p10=1000, hedonic_p9=100, gem_rate=0.20)
      _add_raw_comp(session, 2, "80")
      # Card 3: negative EV (raw too expensive relative to expected value)
      _planted_card(session, 3, hedonic_p10=1000, hedonic_p9=100, gem_rate=0.20)
      _add_raw_comp(session, 3, "500")
      df = rank_grading_candidates(session,
                                   as_of=datetime(2026, 5, 1, tzinfo=timezone.utc),
                                   min_ev_per_dollar=Decimal("0.15"))
      assert 2 in df["card_id"].tolist()
      assert 3 not in df["card_id"].tolist()
  ```

- [ ] **Step 2: Verify failure**

  ```bash
  pytest tests/test_grading_ev.py -v
  ```
  Expected: import errors / undefined symbols.

- [ ] **Step 3: Extend `grading_ev.py`**

  Append:
  ```python
  import pandas as pd
  from sqlalchemy import func

  from sportscards.db.models import TxClean
  from sportscards.portfolio.transaction_costs import (
      DEFAULT_SCHEDULE,
      FeeSchedule,
      cost_to_grade,
      net_proceeds,
  )

  # Test seam: tests patch this to avoid loading a fitted hedonic model.
  _HEDONIC_OVERRIDES: dict[int, tuple[Decimal, Decimal]] = {}


  @dataclass(frozen=True)
  class GradingEV:
      card_id: int
      gem_rate: Decimal
      p10_price: Decimal
      p9_price: Decimal
      cost_to_grade: Decimal
      raw_price: Decimal | None
      ev: Decimal
      ev_per_dollar: Decimal | None
      p10_pop: int
      sample_size: int
      computed_at: datetime


  def _hedonic_prices(session: Session, card_id: int) -> tuple[Decimal, Decimal] | None:
      """Return (P10_gross, P9_gross) fair value or None when unavailable.

      Tests inject via ``_HEDONIC_OVERRIDES``. Production path loads the
      persisted model and runs ``hedonic.predict`` on a synthesized row at
      grade=10 and grade=9.
      """
      if card_id in _HEDONIC_OVERRIDES:
          return _HEDONIC_OVERRIDES[card_id]
      try:
          from sportscards.factors import hedonic
          model, encoder, _ = hedonic.load_model()
      except FileNotFoundError:
          return None
      # Build a 2-row design frame (grade=10, grade=9) using the card's features.
      # Implementation note: synthesize the minimal feature row from card_master
      # + latest pop snapshot (log_pop_psa10, log_pop_psa9_or_better). See
      # `factors.features.build_features` for the column contract — replicate
      # those columns here for a single (card_id, grade) pair.
      import numpy as np
      from sportscards.db.models import Card
      card = session.get(Card, card_id)
      if card is None:
          return None
      # Pull latest pop counts for log features.
      latest = session.execute(
          select(PopSnapshot.grade, PopSnapshot.pop_count)
          .where(PopSnapshot.card_id == card_id)
          .where(PopSnapshot.grader == "PSA")
          .order_by(PopSnapshot.snapshot_date.desc())
          .limit(10)
      ).all()
      pops = {Decimal(str(g)): int(n) for g, n in latest}
      pop10 = pops.get(Decimal("10"), 0)
      pop9 = pops.get(Decimal("9"), 0)
      pop9plus = pop9 + pop10
      def _row(grade: float) -> dict:
          return {
              "log_pop_psa10": float(np.log1p(pop10)),
              "log_pop_psa9_or_better": float(np.log1p(pop9plus)),
              "parallel_tier": 0,  # neutral; refine in follow-up
              "print_run_log": float(np.log1p(card.print_run or 0)),
              "slab_grade": grade,
              "player_age_at_sale": 25,
              "years_since_draft": 5,
              "draft_pick": 15,
              "is_rookie": bool(card.is_rookie),
              "has_auto": bool(card.has_auto),
              "has_patch": bool(card.has_patch),
              "is_one_of_one": bool(card.is_one_of_one),
              "era_modern": (card.year or 0) >= 2010,
              "set_tier": "mid",
              "team_market": "mid",
              "slab_grader": "PSA",
          }
      df = pd.DataFrame([_row(10.0), _row(9.0)])
      log_prices = hedonic.predict(model, encoder, df)
      p10 = Decimal(str(float(np.expm1(log_prices[0])))).quantize(Decimal("0.01"))
      p9 = Decimal(str(float(np.expm1(log_prices[1])))).quantize(Decimal("0.01"))
      return p10, p9


  def _raw_clearing_price(
      session: Session, card_id: int, as_of: datetime, window_days: int = 30
  ) -> Decimal | None:
      since = as_of - timedelta(days=window_days)
      rows = session.execute(
          select(TxClean.price_usd)
          .where(TxClean.card_id == card_id)
          .where(TxClean.slab_grader.is_(None))
          .where(TxClean.sold_at >= since)
          .where(TxClean.sold_at <= as_of)
      ).scalars().all()
      if not rows:
          return None
      prices = sorted(Decimal(str(p)) for p in rows)
      mid = len(prices) // 2
      return prices[mid] if len(prices) % 2 == 1 else (prices[mid - 1] + prices[mid]) / 2


  def compute_grading_ev(
      session: Session,
      card_id: int,
      as_of: datetime,
      *,
      grade_tier: str = "value_bulk",
      schedule: FeeSchedule = DEFAULT_SCHEDULE,
      apply_trend_adjustment: bool = False,
  ) -> GradingEV:
      gem = estimate_gem_rate(session, card_id, as_of)
      gem_rate = gem.rate
      if apply_trend_adjustment:
          gem_rate = (gem_rate * trend_adjustment(session, card_id, as_of)).quantize(Decimal("0.0001"))

      hedonic = _hedonic_prices(session, card_id)
      if hedonic is None:
          raise ValueError(f"no hedonic price available for card {card_id}")
      p10_gross, p9_gross = hedonic
      p10_net = Decimal(str(net_proceeds(float(p10_gross), "ebay", schedule))).quantize(Decimal("0.01"))
      p9_net = Decimal(str(net_proceeds(float(p9_gross), "ebay", schedule))).quantize(Decimal("0.01"))
      grade_cost = Decimal(str(cost_to_grade(grade_tier, schedule))).quantize(Decimal("0.01"))

      raw = _raw_clearing_price(session, card_id, as_of)
      ev = (gem_rate * p10_net + (Decimal(1) - gem_rate) * p9_net - grade_cost - (raw or Decimal(0)))
      ev = ev.quantize(Decimal("0.01"))
      evpd = (ev / raw).quantize(Decimal("0.0001")) if raw and raw > 0 else None

      # PSA-10 pop on latest snapshot
      p10_pop = session.execute(
          select(PopSnapshot.pop_count)
          .where(PopSnapshot.card_id == card_id)
          .where(PopSnapshot.grader == "PSA")
          .where(PopSnapshot.grade == Decimal("10"))
          .order_by(PopSnapshot.snapshot_date.desc())
          .limit(1)
      ).scalar_one_or_none() or 0

      return GradingEV(
          card_id=card_id,
          gem_rate=gem_rate,
          p10_price=p10_gross,
          p9_price=p9_gross,
          cost_to_grade=grade_cost,
          raw_price=raw,
          ev=ev,
          ev_per_dollar=evpd,
          p10_pop=int(p10_pop),
          sample_size=gem.sample_size,
          computed_at=datetime.now(tz=as_of.tzinfo) if as_of.tzinfo else datetime.utcnow(),
      )


  def rank_grading_candidates(
      session: Session,
      as_of: datetime,
      *,
      min_ev_per_dollar: Decimal = Decimal("0.15"),
      grade_tier: str = "value_bulk",
      schedule: FeeSchedule = DEFAULT_SCHEDULE,
      min_sample_size: int = 10,
      apply_trend_adjustment: bool = False,
  ) -> pd.DataFrame:
      """Return sorted DataFrame of positive-EV grading candidates.

      Only considers cards with ≥1 recent raw comp and ≥``min_sample_size`` graded copies.
      """
      since = as_of - timedelta(days=30)
      cand_ids = session.execute(
          select(TxClean.card_id)
          .where(TxClean.slab_grader.is_(None))
          .where(TxClean.sold_at >= since)
          .where(TxClean.card_id.is_not(None))
          .group_by(TxClean.card_id)
      ).scalars().all()

      rows = []
      for cid in cand_ids:
          try:
              ev = compute_grading_ev(session, cid, as_of, grade_tier=grade_tier,
                                       schedule=schedule,
                                       apply_trend_adjustment=apply_trend_adjustment)
          except ValueError:
              continue
          if ev.sample_size < min_sample_size:
              continue
          if ev.raw_price is None or ev.ev_per_dollar is None:
              continue
          if ev.ev_per_dollar < min_ev_per_dollar:
              continue
          rows.append(dict(
              card_id=ev.card_id, gem_rate=float(ev.gem_rate),
              p10_price=float(ev.p10_price), p9_price=float(ev.p9_price),
              cost_to_grade=float(ev.cost_to_grade),
              raw_price=float(ev.raw_price), ev=float(ev.ev),
              ev_per_dollar=float(ev.ev_per_dollar),
              p10_pop=ev.p10_pop, sample_size=ev.sample_size,
          ))
      df = pd.DataFrame(rows)
      if df.empty:
          return df
      return df.sort_values("ev_per_dollar", ascending=False).reset_index(drop=True)
  ```

- [ ] **Step 4: Run tests**

  ```bash
  pytest tests/test_grading_ev.py -v
  ```
  Expected: all green.

- [ ] **Step 5: Run full test suite**

  ```bash
  pytest -x
  ```
  Expected: all green (no regressions).

- [ ] **Step 6: Commit**

  ```bash
  git add src/sportscards/factors/grading_ev.py tests/test_grading_ev.py
  git commit -m "feat(factors): compute_grading_ev + rank_grading_candidates"
  ```

---

## Task 7: Trend-adjustment test + flag

**Files:**
- Modify: `tests/test_grading_ev.py`

- [ ] **Step 1: Add trend test**

  Append:
  ```python
  def test_trend_adjustment_dampens_gem_rate_when_recent_share_drops(session):
      from sportscards.factors.grading_ev import trend_adjustment
      old = datetime(2025, 5, 1, tzinfo=timezone.utc)   # 1 year ago window start
      mid = datetime(2026, 2, 1, tzinfo=timezone.utc)   # ~90d ago
      now = datetime(2026, 5, 1, tzinfo=timezone.utc)
      # 365d-window: starts at 50/100 = 0.50 share among PSA8/9/10
      _add_pop(session, 1, old, psa8=0, psa9=50, psa10=50)
      # 90d-window: only 5 new PSA10 of 100 new graded — recent share 0.05
      _add_pop(session, 1, mid, psa8=0, psa9=95, psa10=55)  # cumulative as of mid
      _add_pop(session, 1, now, psa8=0, psa9=190, psa10=60)  # +5 P10, +95 P9 in last 90d
      adj = trend_adjustment(session, 1, now)
      assert adj < Decimal("1")  # dampened
  ```

- [ ] **Step 2: Run and confirm green**

  ```bash
  pytest tests/test_grading_ev.py::test_trend_adjustment_dampens_gem_rate_when_recent_share_drops -v
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add tests/test_grading_ev.py
  git commit -m "test(grading_ev): cover trend adjustment dampening"
  ```

---

## Task 8: Daily flow

**Files:**
- Create: `src/sportscards/flows/daily_grading_ev.py`
- Modify: `prefect.yaml`

- [ ] **Step 1: Write the flow**

  ```python
  """Daily grading-EV flow — recomputes EV for all eligible cards."""

  from __future__ import annotations

  from datetime import datetime, timezone
  from decimal import Decimal

  from prefect import flow, get_run_logger
  from sqlalchemy import select
  from sqlalchemy.dialects.postgresql import insert as pg_insert

  from sportscards.db.models import GradingEv, TxClean
  from sportscards.db.session import session_scope
  from sportscards.factors.grading_ev import compute_grading_ev


  @flow(name="daily-grading-ev")
  def daily_grading_ev_flow(grade_tier: str = "value_bulk",
                            apply_trend_adjustment: bool = False) -> int:
      log = get_run_logger()
      as_of = datetime.now(tz=timezone.utc)
      written = 0
      with session_scope() as s:
          cand_ids = s.execute(
              select(TxClean.card_id)
              .where(TxClean.slab_grader.is_(None))
              .where(TxClean.card_id.is_not(None))
              .group_by(TxClean.card_id)
          ).scalars().all()
          for cid in cand_ids:
              try:
                  ev = compute_grading_ev(s, cid, as_of,
                                          grade_tier=grade_tier,
                                          apply_trend_adjustment=apply_trend_adjustment)
              except ValueError:
                  continue
              s.execute(
                  pg_insert(GradingEv)
                  .values(card_id=ev.card_id, as_of_date=as_of, grade_tier=grade_tier,
                          gem_rate=ev.gem_rate, p10_price=ev.p10_price, p9_price=ev.p9_price,
                          cost_to_grade=ev.cost_to_grade, raw_price=ev.raw_price,
                          ev=ev.ev, ev_per_dollar=ev.ev_per_dollar,
                          sample_size=ev.sample_size)
                  .on_conflict_do_update(
                      index_elements=["card_id", "as_of_date", "grade_tier"],
                      set_=dict(gem_rate=ev.gem_rate, p10_price=ev.p10_price,
                                p9_price=ev.p9_price, cost_to_grade=ev.cost_to_grade,
                                raw_price=ev.raw_price, ev=ev.ev,
                                ev_per_dollar=ev.ev_per_dollar,
                                sample_size=ev.sample_size),
                  )
              )
              written += 1
      log.info("daily-grading-ev: wrote %d rows", written)
      return written


  if __name__ == "__main__":
      daily_grading_ev_flow()
  ```

- [ ] **Step 2: Add to `prefect.yaml`**

  Append a fourth deployment block:
  ```yaml
    - name: daily-grading-ev
      entrypoint: src/sportscards/flows/daily_grading_ev.py:daily_grading_ev_flow
      work_pool:
        name: default-agent-pool
      schedules:
        - cron: "0 9 * * *"
          timezone: America/Los_Angeles
          active: true
  ```

- [ ] **Step 3: Smoke import**

  ```bash
  python -c "from sportscards.flows.daily_grading_ev import daily_grading_ev_flow; print('ok')"
  ```
  Expected: `ok`

- [ ] **Step 4: Commit**

  ```bash
  git add src/sportscards/flows/daily_grading_ev.py prefect.yaml
  git commit -m "feat(flows): daily-grading-ev at 09:00 PT"
  ```

---

## Task 9: CLI — `sportscards ev compute` and `sportscards ev top`

**Files:**
- Modify: `src/sportscards/cli/__main__.py`

- [ ] **Step 1: Append CLI subgroup**

  At the bottom of `__main__.py` (or alongside the other `@cli.group()` blocks):
  ```python
  @cli.group()
  def ev() -> None:
      """Grading-EV optionality model."""


  @ev.command("compute")
  @click.option("--as-of", default=None, help="ISO date; default today")
  @click.option("--grade-tier", default="value_bulk")
  @click.option("--apply-trend-adjustment", is_flag=True, default=False)
  def ev_compute_cmd(as_of: str | None, grade_tier: str, apply_trend_adjustment: bool) -> None:
      from datetime import datetime, timezone

      from sportscards.flows.daily_grading_ev import daily_grading_ev_flow

      n = daily_grading_ev_flow(grade_tier=grade_tier,
                                apply_trend_adjustment=apply_trend_adjustment)
      click.echo(f"wrote {n} grading_ev rows")


  @ev.command("top")
  @click.option("--limit", default=20, type=int)
  @click.option("--grade-tier", default="value_bulk")
  @click.option("--min-ev-per-dollar", default=0.15, type=float)
  def ev_top_cmd(limit: int, grade_tier: str, min_ev_per_dollar: float) -> None:
      from datetime import datetime, timezone
      from decimal import Decimal

      from sportscards.db.session import session_scope
      from sportscards.factors.grading_ev import rank_grading_candidates

      with session_scope() as s:
          df = rank_grading_candidates(
              s, as_of=datetime.now(tz=timezone.utc),
              grade_tier=grade_tier, min_ev_per_dollar=Decimal(str(min_ev_per_dollar)),
          )
      if df.empty:
          click.echo("no positive-EV candidates")
          return
      for _, row in df.head(limit).iterrows():
          click.echo(
              f"card_id={int(row.card_id):>6} ev=${row.ev:>8.2f} "
              f"ev/$={row.ev_per_dollar:>5.2f} gem={row.gem_rate:.2f} "
              f"raw=${row.raw_price:.2f} (n={int(row.sample_size)})"
          )
  ```

- [ ] **Step 2: Verify CLI loads**

  ```bash
  python -m sportscards.cli ev --help
  ```
  Expected: subcommands `compute`, `top`.

- [ ] **Step 3: Commit**

  ```bash
  git add src/sportscards/cli/__main__.py
  git commit -m "feat(cli): sportscards ev compute / top"
  ```

---

## Task 10: Portfolio sleeve — `--grading-arbitrage-pct`

**Files:**
- Modify: `src/sportscards/portfolio/construction.py` (around line 24-40 + line 179+)
- Modify: `src/sportscards/cli/__main__.py` (around line 179)

- [ ] **Step 1: Read current `AllocationConfig`**

  ```bash
  sed -n '20,80p' src/sportscards/portfolio/construction.py
  ```
  Inspect the existing dataclass to confirm field names before editing.

- [ ] **Step 2: Extend `AllocationConfig`**

  Add a new field:
  ```python
  @dataclass
  class AllocationConfig:
      total_aum_usd: Decimal = Decimal("0")
      anchor_weight: Decimal = Decimal("0.70")
      factor_weight: Decimal = Decimal("0.20")
      prospect_weight: Decimal = Decimal("0.10")
      grading_arbitrage_weight: Decimal = Decimal("0.00")  # opt-in raw sleeve, ≤0.10
  ```

  In `build_portfolio()` (~line 139), after the existing three sleeves are sized, append a grading sleeve:
  ```python
  # --- Grading-arbitrage sleeve (opt-in raw inventory) ---
  if cfg.grading_arbitrage_weight > 0:
      from datetime import datetime, timezone

      from sportscards.factors.grading_ev import rank_grading_candidates

      cap = Decimal("0.10")
      w = min(cfg.grading_arbitrage_weight, cap)
      sleeve_budget = cfg.total_aum_usd * w
      candidates = rank_grading_candidates(session, datetime.now(tz=timezone.utc))
      # Equal-weight top-N until budget exhausted. Position type = 'raw'.
      # NOTE: actual submit-to-PSA workflow tracked as a TODO; here we only
      # plan the raw positions.
      positions = []
      for _, row in candidates.iterrows():
          if sleeve_budget <= 0:
              break
          qty = 1
          cost = Decimal(str(row.raw_price))
          if cost <= sleeve_budget:
              positions.append({"card_id": int(row.card_id), "qty": qty,
                                "buy_price": cost, "position_type": "raw"})
              sleeve_budget -= cost
      result["grading_arbitrage"] = positions
  ```
  (Adapt to existing return-shape conventions in `build_portfolio` — if it currently returns a dataclass, add a `grading_arbitrage` field; if it returns a dict, set the key.)

- [ ] **Step 3: Add CLI flag**

  In `portfolio_plan_cmd` (cli/__main__.py:181), add:
  ```python
  @portfolio.command("plan")
  @click.option("--aum", required=True, type=float)
  @click.option("--grading-arbitrage-pct", default=0.0, type=float,
                help="Percent of AUM to allocate to raw grading arbitrage (≤10).")
  def portfolio_plan_cmd(aum: float, grading_arbitrage_pct: float) -> None:
      from decimal import Decimal
      from sportscards.portfolio.construction import AllocationConfig, build_portfolio
      # ... existing body, passing:
      AllocationConfig(total_aum_usd=Decimal(str(aum)),
                       grading_arbitrage_weight=Decimal(str(grading_arbitrage_pct / 100)))
  ```

- [ ] **Step 4: Run existing portfolio tests**

  ```bash
  pytest tests/test_portfolio.py -v
  ```
  Expected: all green (default sleeve weight = 0 → behavior unchanged).

- [ ] **Step 5: Commit**

  ```bash
  git add src/sportscards/portfolio/construction.py src/sportscards/cli/__main__.py
  git commit -m "feat(portfolio): opt-in grading-arbitrage sleeve"
  ```

---

## Task 11: Dashboard tab + queries

**Files:**
- Modify: `src/sportscards/reports/queries.py`
- Modify: `reports/dashboard.py`

- [ ] **Step 1: Add query**

  Append to `queries.py`:
  ```python
  def grading_ev_leaderboard(engine: Engine | None = None, n: int = 50) -> pd.DataFrame:
      eng = _engine(engine)
      _require(eng, "grading_ev", phase="grading-ev")
      sql = """
          SELECT g.*, c.year, c.set_name, c.parallel, p.name AS player
          FROM grading_ev g
          JOIN card_master c ON c.card_id = g.card_id
          LEFT JOIN player_master p ON p.player_id = c.player_id
          WHERE g.as_of_date = (SELECT MAX(as_of_date) FROM grading_ev)
          ORDER BY g.ev_per_dollar DESC NULLS LAST
          LIMIT :n
      """
      return pd.read_sql(sql, eng, params={"n": n})
  ```

- [ ] **Step 2: Add tab**

  In `dashboard.py` add a cached loader + tab function:
  ```python
  @st.cache_data(ttl=300)
  def _cached_grading_ev() -> pd.DataFrame:
      return queries.grading_ev_leaderboard()


  def _grading_ev_tab() -> None:
      st.header("Grading EV — raw → PSA 10 optionality")
      try:
          df = _cached_grading_ev()
      except TableMissing as e:
          _placeholder(e.phase)
          return
      if df.empty:
          st.write("No grading-EV rows yet — run `sportscards ev compute`.")
          return
      def _highlight(v):
          if pd.isna(v):
              return ""
          if v > 0.30:
              return "background-color: #d4edda"
          if v < 0:
              return "background-color: #f8d7da"
          return ""
      st.dataframe(df.style.map(_highlight, subset=["ev_per_dollar"]),
                   use_container_width=True)
      st.caption("Small sample_size = noisier gem_rate estimate; treat <20 as speculative.")
  ```

  In the `tabs = st.tabs([...])` line, add `"Grading EV"`:
  ```python
  tabs = st.tabs(["Market", "Mispricing", "Prospects", "Portfolio", "Grading EV", "Data Health"])
  ...
  with tabs[4]:
      _grading_ev_tab()
  with tabs[5]:
      _data_health_tab()  # or whatever currently sits in slot 4
  ```
  (Adjust the index shift carefully; re-run `streamlit run reports/dashboard.py` and confirm tab order.)

- [ ] **Step 3: Smoke-import**

  ```bash
  python -c "from sportscards.reports import queries; print(queries.grading_ev_leaderboard.__name__)"
  ```
  Expected: `grading_ev_leaderboard`

- [ ] **Step 4: Commit**

  ```bash
  git add src/sportscards/reports/queries.py reports/dashboard.py
  git commit -m "feat(dashboard): Grading EV tab + leaderboard query"
  ```

---

## Task 12: End-to-end verification

- [ ] **Step 1: Full test suite**

  ```bash
  pytest -x
  ```
  Expected: all green.

- [ ] **Step 2: CLI smoke**

  ```bash
  sportscards ev --help
  sportscards ev top --limit 5
  sportscards portfolio plan --aum 100000 --grading-arbitrage-pct 5
  ```
  Expected: no errors. `ev top` may print "no positive-EV candidates" if the dev DB is empty — that's fine.

- [ ] **Step 3: Run migrations against a scratch DB**

  ```bash
  alembic upgrade head
  ```
  Expected: `0006_grading_ev` runs without error.

- [ ] **Step 4: Final commit if anything was tweaked**

  ```bash
  git status
  # commit any stragglers
  ```

---

## Task 13: PR

- [ ] **Step 1: Push branch**

  ```bash
  git push -u origin claude/funny-lehmann-12aa2b
  ```

- [ ] **Step 2: Open PR**

  ```bash
  gh pr create --title "feat(factors): grading EV optionality model (raw → PSA 10)" --body "$(cat <<'EOF'
  ## Summary
  - New module `factors.grading_ev` computes `EV = gem_rate × P10 + (1-gem_rate) × P9 − grade_cost − raw_price`
  - Bayesian shrinkage of gem-rate toward universe prior; optional 90d-vs-365d trend adjustment
  - Raw cards now persist as `parser_method='regex_raw'` (LLM no longer called for well-formed raw titles)
  - New `grading_ev` hypertable + daily Prefect flow at 09:00 PT
  - `sportscards ev compute|top` CLI, opt-in `--grading-arbitrage-pct` portfolio sleeve, Streamlit "Grading EV" tab

  ## Test plan
  - [ ] `pytest -x` green
  - [ ] `alembic upgrade head` runs on scratch DB
  - [ ] `sportscards ev compute --apply-trend-adjustment` writes rows
  - [ ] Dashboard tab renders and highlights ev_per_dollar > 0.30 / < 0
  - [ ] `sportscards portfolio plan --aum 100000 --grading-arbitrage-pct 5` returns a raw sleeve

  🤖 Generated with [Claude Code](https://claude.com/claude-code)
  EOF
  )"
  ```

---

## Known limitations (document in module docstring)

- **Bulk-submission pop spikes**: gem-rate uses snapshot deltas; a 200-card bulk drop can briefly skew rates. Mitigation: switch to event-level grading outcomes once PSA exposes them.
- **Hedonic prediction at PSA 9**: the existing model is trained on PSA 9/10 sales but the `slab_grade` feature is monotone-0 in `factors.hedonic`. P9 prediction is informative but noisy near the boundary; consider a separate PSA-9-specific calibration.
- **Single raw budget cap**: this is the only sleeve holding raw inventory; keep `grading_arbitrage_weight ≤ 0.10` until validated against ≥1 quarter of out-of-sample EVs.
