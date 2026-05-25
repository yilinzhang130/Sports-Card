"""Feature engineering for the Phase 2B hedonic fair-value model.

Builds a transaction-level feature matrix from ``tx_clean`` joined with
``card_master``, ``player_master``, and ``pop_snapshots``. Pop columns use a
leakage-safe asof-join (backward only) so each transaction sees the latest
snapshot whose ``snapshot_date <= sold_at``.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from sportscards.db.models import Card, FactorPanel, Player, PopSnapshot, TxClean

# ---------------------------------------------------------------------------
# Per-card categorical helpers
# ---------------------------------------------------------------------------


def parallel_tier(card: Card) -> int:
    """Map a card's parallel/print-run to a 0..5 scarcity tier.

    Base=0, color/refractor=1, /99=2, /25=3, /10=4, 1of1=5.
    """
    if card.is_one_of_one:
        return 5
    pr = card.print_run
    if pr is not None:
        if pr <= 10:
            return 4
        if pr <= 25:
            return 3
        if pr <= 99:
            return 2
        if pr <= 499:
            return 1
        return 0
    p = (card.parallel or "Base").lower()
    if p == "base":
        return 0
    # color / refractor / prizm and other non-base colors → tier 1
    return 1


_PREMIUM_SETS = ("national treasures", "flawless", "immaculate")
_FLAGSHIP_SETS = ("prizm", "select", "topps chrome")
_MID_SETS = ("mosaic", "optic", "donruss optic")
_LOW_SETS = ("hoops", "donruss")


def set_tier(set_name: str) -> str:
    """Map a set name to premium / flagship / mid / low."""
    s = (set_name or "").lower()
    for needle in _PREMIUM_SETS:
        if needle in s:
            return "premium"
    # Check mid before low so "Donruss Optic" doesn't get caught by "donruss".
    for needle in _MID_SETS:
        if needle in s:
            return "mid"
    for needle in _FLAGSHIP_SETS:
        if needle in s:
            return "flagship"
    for needle in _LOW_SETS:
        if needle in s:
            return "low"
    return "flagship"


_PREMIUM_TEAMS = {"LAL", "NYK", "BOS", "CHI", "TOR"}


def team_market(team: str | None) -> str:
    """Map team code to premium / standard market."""
    if team and team.upper() in _PREMIUM_TEAMS:
        return "premium"
    return "standard"


# ---------------------------------------------------------------------------
# Feature matrix builder
# ---------------------------------------------------------------------------


def _to_naive(ts: pd.Series) -> pd.Series:
    """Strip tzinfo from a datetime series to keep merge_asof happy."""
    ts = pd.to_datetime(ts, errors="coerce")
    try:
        return ts.dt.tz_localize(None)
    except (TypeError, AttributeError):
        # already naive
        return ts


def build_features(session: Session) -> pd.DataFrame:
    """Build the hedonic feature matrix.

    For each ``(card_id, sold_at)`` row the pop columns reflect the latest
    snapshot with ``snapshot_date <= sold_at`` (asof-join, no leakage).
    Only PSA 9 / PSA 10 transactions are kept (current scope).
    """
    # --- 1. Load tx + card + player joined into a DataFrame -----------------
    tx_stmt = (
        select(
            TxClean.tx_id,
            TxClean.card_id,
            TxClean.sold_at,
            TxClean.price_usd,
            TxClean.slab_grader,
            TxClean.slab_grade,
            Card.year.label("card_year"),
            Card.parallel,
            Card.print_run,
            Card.is_rookie,
            Card.has_auto,
            Card.has_patch,
            Card.is_one_of_one,
            Card.set_name,
            Player.dob,
            Player.draft_year,
            Player.draft_pick,
            Player.team,
        )
        .join(Card, TxClean.card_id == Card.card_id)
        .join(Player, Card.player_id == Player.player_id)
    )
    rows = session.execute(tx_stmt).all()
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=list(rows[0]._fields))

    # --- 2. Filter to PSA 9 / 10 --------------------------------------------
    df["slab_grade"] = pd.to_numeric(df["slab_grade"], errors="coerce")
    df = df[(df["slab_grader"] == "PSA") & (df["slab_grade"].isin([9, 10]))].copy()
    if df.empty:
        return df

    # --- 3. Target ----------------------------------------------------------
    df["price_usd"] = pd.to_numeric(df["price_usd"], errors="coerce")
    df["log_price"] = np.log(df["price_usd"].astype(float))

    # --- 4. Load pop snapshots ---------------------------------------------
    snap_rows = session.execute(
        select(
            PopSnapshot.card_id,
            PopSnapshot.grader,
            PopSnapshot.grade,
            PopSnapshot.snapshot_date,
            PopSnapshot.pop_count,
        )
    ).all()
    _snap_cols = ["card_id", "grader", "grade", "snapshot_date", "pop_count"]
    snaps = (
        pd.DataFrame(snap_rows, columns=_snap_cols)
        if snap_rows
        else pd.DataFrame(columns=_snap_cols)
    )
    snaps["grade"] = pd.to_numeric(snaps["grade"], errors="coerce")
    snaps["snapshot_date"] = _to_naive(snaps["snapshot_date"])
    snaps = snaps.sort_values("snapshot_date").reset_index(drop=True)

    # --- 5/6. Asof-join PSA 10 then PSA 9 pop ------------------------------
    df["sold_at"] = _to_naive(df["sold_at"])
    df = df.sort_values("sold_at").reset_index(drop=True)

    def _asof_pop(grade_val: int, out_col: str) -> None:
        sub = (
            snaps[(snaps["grader"] == "PSA") & (snaps["grade"] == grade_val)][
                ["card_id", "snapshot_date", "pop_count"]
            ]
            .sort_values("snapshot_date")
            .reset_index(drop=True)
            .rename(columns={"pop_count": out_col})
        )
        if sub.empty:
            df[out_col] = np.nan
            return
        merged = pd.merge_asof(
            df.sort_values("sold_at"),
            sub,
            left_on="sold_at",
            right_on="snapshot_date",
            by="card_id",
            direction="backward",
        )
        # merge_asof preserves left-row order when input is sorted by `on`.
        df[out_col] = merged[out_col].to_numpy()
        if "snapshot_date" in merged.columns:
            merged.drop(columns=["snapshot_date"], inplace=True)

    _asof_pop(10, "pop_psa10")
    _asof_pop(9, "pop_psa9")

    # --- 5b. Asof-join factor_panel (momentum + liquidity) ----------------
    fp_rows = session.execute(
        select(
            FactorPanel.card_id,
            FactorPanel.as_of_date,
            FactorPanel.cs_momentum_pct,
            FactorPanel.is_hyped,
            FactorPanel.sales_count_90d,
            FactorPanel.bid_ask_proxy,
            FactorPanel.liquidity_tier,
        )
    ).all()
    fp_cols = [
        "card_id",
        "as_of_date",
        "cs_momentum_pct",
        "is_hyped",
        "sales_count_90d",
        "bid_ask_proxy",
        "liquidity_tier",
    ]
    fp = pd.DataFrame(fp_rows, columns=fp_cols) if fp_rows else pd.DataFrame(columns=fp_cols)
    if not fp.empty:
        fp["as_of_date"] = _to_naive(fp["as_of_date"])
        fp["cs_momentum_pct"] = pd.to_numeric(fp["cs_momentum_pct"], errors="coerce")
        fp["sales_count_90d"] = pd.to_numeric(fp["sales_count_90d"], errors="coerce").fillna(0)
        fp["bid_ask_proxy"] = pd.to_numeric(fp["bid_ask_proxy"], errors="coerce")
        fp["is_hyped"] = fp["is_hyped"].astype(bool)
        fp = fp.sort_values("as_of_date").reset_index(drop=True)
        merged_fp = pd.merge_asof(
            df.sort_values("sold_at"),
            fp,
            left_on="sold_at",
            right_on="as_of_date",
            by="card_id",
            direction="backward",
        )
        for col in (
            "cs_momentum_pct",
            "is_hyped",
            "sales_count_90d",
            "bid_ask_proxy",
            "liquidity_tier",
        ):
            df[col] = merged_fp[col].to_numpy()
    else:
        df["cs_momentum_pct"] = np.nan
        df["is_hyped"] = False
        df["sales_count_90d"] = 0
        df["bid_ask_proxy"] = np.nan
        df["liquidity_tier"] = "D"

    df["pop_psa9_or_better"] = df["pop_psa10"].astype(float) + df["pop_psa9"].astype(float)

    # --- 7. log1p pop transforms (NaN-preserving) --------------------------
    df["log_pop_psa10"] = np.log1p(df["pop_psa10"].astype(float))
    df["log_pop_psa9_or_better"] = np.log1p(df["pop_psa9_or_better"].astype(float))

    # --- 8. print_run_log (fill missing with pop_psa9_or_better) -----------
    pr = pd.to_numeric(df["print_run"], errors="coerce")
    pr = pr.fillna(df["pop_psa9_or_better"])
    df["print_run_log"] = np.log1p(pr.astype(float))

    # --- 9-12. Categorical / derived features ------------------------------
    # parallel_tier needs a Card-like object; emulate via a simple namespace.
    def _row_tier(row: pd.Series) -> int:
        stub = SimpleNamespace(
            is_one_of_one=bool(row["is_one_of_one"]),
            print_run=None if pd.isna(row["print_run"]) else int(row["print_run"]),
            parallel=row["parallel"],
        )
        return parallel_tier(stub)  # type: ignore[arg-type]

    df["parallel_tier"] = df.apply(_row_tier, axis=1).astype(int)

    # player_age_at_sale (years)
    dob = pd.to_datetime(df["dob"], errors="coerce")
    with contextlib.suppress(TypeError, AttributeError):
        dob = dob.dt.tz_localize(None)
    df["player_age_at_sale"] = (df["sold_at"] - dob).dt.days / 365.25

    # years_since_draft
    draft_year = pd.to_numeric(df["draft_year"], errors="coerce")
    df["years_since_draft"] = df["sold_at"].dt.year - draft_year

    df["draft_pick"] = pd.to_numeric(df["draft_pick"], errors="coerce")

    # era_modern must match synthetic_data.py threshold (year >= 2010).
    df["era_modern"] = (df["card_year"] >= 2010).astype(int)

    # Boolean → int
    for col in ("is_rookie", "has_auto", "has_patch", "is_one_of_one"):
        df[col] = df[col].astype(bool).astype(int)

    # Categoricals as str (one-hot done downstream in hedonic module)
    df["set_tier"] = df["set_name"].apply(set_tier).astype(str)
    df["team_market"] = df["team"].apply(team_market).astype(str)
    df["slab_grader"] = df["slab_grader"].astype(str)

    # --- Momentum + liquidity feature post-processing ---------------------
    df["cs_momentum_pct"] = pd.to_numeric(df["cs_momentum_pct"], errors="coerce").fillna(0.5)
    df["bid_ask_proxy"] = pd.to_numeric(df["bid_ask_proxy"], errors="coerce").fillna(0.0)
    df["sales_count_90d"] = pd.to_numeric(df["sales_count_90d"], errors="coerce").fillna(0.0)
    df["log_sales_count_90d"] = np.log1p(df["sales_count_90d"].astype(float))
    # is_hyped → bool → int
    df["is_hyped"] = df["is_hyped"].fillna(False).astype(bool).astype(int)
    # liquidity_tier as canonical str — default D when missing
    df["liquidity_tier"] = df["liquidity_tier"].fillna("D").astype(str)

    out_cols = [
        # identifiers
        "tx_id",
        "sold_at",
        # target
        "log_price",
        # numerical
        "log_pop_psa10",
        "log_pop_psa9_or_better",
        "parallel_tier",
        "print_run_log",
        "slab_grade",
        "player_age_at_sale",
        "years_since_draft",
        "draft_pick",
        # boolean (int 0/1)
        "is_rookie",
        "has_auto",
        "has_patch",
        "is_one_of_one",
        "era_modern",
        # momentum + liquidity
        "cs_momentum_pct",
        "is_hyped",
        "log_sales_count_90d",
        "bid_ask_proxy",
        # categorical (str)
        "set_tier",
        "team_market",
        "slab_grader",
        "liquidity_tier",
    ]
    return df[out_cols].reset_index(drop=True)
