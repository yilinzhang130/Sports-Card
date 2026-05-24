"""Read-only DB queries for the reporting layer.

Every query checks that its source table exists and raises
`TableMissing` if not, so the dashboard/letter can render a
placeholder instead of crashing.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from sportscards.db.session import get_engine


class TableMissing(RuntimeError):
    """Raised when a query's source table does not exist yet."""

    def __init__(self, table: str, phase: str):
        super().__init__(f"Table {table!r} not found (expected from {phase})")
        self.table = table
        self.phase = phase


def _require(engine: Engine, table: str, phase: str) -> None:
    if not inspect(engine).has_table(table):
        raise TableMissing(table, phase)


def _engine(engine: Engine | None) -> Engine:
    return engine if engine is not None else get_engine()


# --- Market overview (Phase 2A) ------------------------------------------------


def repeat_sales_index(engine: Engine | None = None) -> pd.DataFrame:
    """Time series of the repeat-sales index. Columns: as_of, sleeve, index_value."""
    eng = _engine(engine)
    _require(eng, "repeat_sales_index", "Phase 2A")
    sql = text("SELECT as_of, sleeve, index_value FROM repeat_sales_index ORDER BY as_of")
    return pd.read_sql(sql, eng)


# --- Mispricing leaderboard (Phase 2B) -----------------------------------------


def mispricing_leaderboard(engine: Engine | None = None, n: int = 20) -> dict[str, pd.DataFrame]:
    """Top-N undervalued (positive residual) and overvalued (negative)."""
    eng = _engine(engine)
    _require(eng, "tx_mispricing", "Phase 2B")
    base = (
        "SELECT m.tx_id, m.residual, m.fair_value, c.year, c.set_name, "
        "       c.parallel, p.name AS player "
        "FROM tx_mispricing m "
        "JOIN tx_clean t ON t.tx_id = m.tx_id "
        "JOIN card_master c ON c.card_id = t.card_id "
        "JOIN player_master p ON p.player_id = c.player_id "
    )
    under = pd.read_sql(text(base + "ORDER BY m.residual DESC LIMIT :n"), eng, params={"n": n})
    over = pd.read_sql(text(base + "ORDER BY m.residual ASC LIMIT :n"), eng, params={"n": n})
    return {"undervalued": under, "overvalued": over}


# --- Prospect board (Phase 3 — already shipped) --------------------------------


def stardom_scores(engine: Engine | None = None, draft_year: int | None = None) -> pd.DataFrame:
    eng = _engine(engine)
    _require(eng, "player_stardom_score", "Phase 3")
    sql = (
        "SELECT s.player_id, p.name, s.draft_year, s.premium, s.percentile_rank "
        "FROM player_stardom_score s "
        "JOIN player_master p ON p.player_id = s.player_id "
    )
    params: dict[str, object] = {}
    if draft_year is not None:
        sql += "WHERE s.draft_year = :dy "
        params["dy"] = draft_year
    sql += "ORDER BY s.premium DESC"
    return pd.read_sql(text(sql), eng, params=params)


def player_price_history(player_id: int, engine: Engine | None = None) -> pd.DataFrame:
    eng = _engine(engine)
    _require(eng, "tx_clean", "Phase 1")
    sql = text(
        "SELECT t.sold_at, t.price_usd "
        "FROM tx_clean t JOIN card_master c ON c.card_id = t.card_id "
        "WHERE c.player_id = :pid AND t.sold_at IS NOT NULL "
        "ORDER BY t.sold_at"
    )
    return pd.read_sql(sql, eng, params={"pid": player_id})


# --- Portfolio (Phase 4) -------------------------------------------------------


def backtest_nav(engine: Engine | None = None) -> pd.DataFrame:
    eng = _engine(engine)
    _require(eng, "backtest_runs", "Phase 4")
    sql = text(
        "SELECT as_of, nav FROM backtest_runs "
        "WHERE run_id = (SELECT MAX(run_id) FROM backtest_runs) "
        "ORDER BY as_of"
    )
    return pd.read_sql(sql, eng)


# --- Data health (Phase 1 — always available) ----------------------------------


def data_health(engine: Engine | None = None) -> dict[str, pd.DataFrame]:
    eng = _engine(engine)
    _require(eng, "tx_raw", "Phase 1")
    raw_vs_clean = pd.read_sql(
        text(
            "SELECT DATE(r.ingested_at) AS day, r.source, "
            "       COUNT(*) AS raw_count, "
            "       COUNT(c.tx_id) AS clean_count "
            "FROM tx_raw r LEFT JOIN tx_clean c ON c.raw_id = r.raw_id "
            "GROUP BY day, r.source ORDER BY day DESC LIMIT 30"
        ),
        eng,
    )
    failures = pd.read_sql(text("SELECT COUNT(*) AS n FROM parse_failures"), eng)
    return {"raw_vs_clean": raw_vs_clean, "failures": failures}


# --- Letter metrics bundle -----------------------------------------------------


@dataclass
class LetterMetrics:
    month: str
    index_returns: dict[str, float] | None
    top_mispricings: pd.DataFrame | None
    rebalance_trades: pd.DataFrame | None
    fee_drag_ytd: float | None
    sleeve_allocation: pd.DataFrame | None


def collect_letter_metrics(month: str, engine: Engine | None = None) -> LetterMetrics:
    """Best-effort metric collection — any missing table becomes a None field."""
    eng = _engine(engine)

    def _try(fn):
        try:
            return fn()
        except TableMissing:
            return None
        except Exception:
            # Schema drift on an in-progress Phase 2+ table — degrade gracefully.
            return None

    index_returns = _try(lambda: _compute_index_returns(eng, month))
    top_mis = _try(lambda: mispricing_leaderboard(eng, n=5)["undervalued"])
    trades = _try(lambda: _recent_trades(eng, month))
    fees = _try(lambda: _fee_drag_ytd(eng, month))
    sleeves = _try(lambda: _sleeve_allocation(eng))

    return LetterMetrics(
        month=month,
        index_returns=index_returns,
        top_mispricings=top_mis,
        rebalance_trades=trades,
        fee_drag_ytd=fees,
        sleeve_allocation=sleeves,
    )


def _compute_index_returns(engine: Engine, month: str) -> dict[str, float]:
    _require(engine, "repeat_sales_index", "Phase 2A")
    df = pd.read_sql(
        text("SELECT as_of, index_value FROM repeat_sales_index WHERE sleeve='ALL' ORDER BY as_of"),
        engine,
    )
    if df.empty:
        return {"1m": 0.0, "3m": 0.0, "12m": 0.0}
    end = pd.Timestamp(month + "-01") + pd.offsets.MonthEnd(0)
    df = df[df["as_of"] <= end]
    last = df["index_value"].iloc[-1]

    def ret(months: int) -> float:
        cutoff = end - pd.DateOffset(months=months)
        prior = df[df["as_of"] <= cutoff]
        if prior.empty:
            return 0.0
        return float(last / prior["index_value"].iloc[-1] - 1.0)

    return {"1m": ret(1), "3m": ret(3), "12m": ret(12)}


def _recent_trades(engine: Engine, month: str) -> pd.DataFrame:
    _require(engine, "backtest_runs", "Phase 4")
    return pd.read_sql(
        text(
            "SELECT trade_date, side, card_id, qty, price "
            "FROM backtest_runs WHERE DATE_TRUNC('month', trade_date) = :m "
            "ORDER BY trade_date"
        ),
        engine,
        params={"m": month + "-01"},
    )


def _fee_drag_ytd(engine: Engine, month: str) -> float:
    _require(engine, "tx_clean", "Phase 1")
    year = month.split("-")[0]
    df = pd.read_sql(
        text(
            "SELECT COALESCE(SUM(fee_estimate),0) AS fees, "
            "       COALESCE(SUM(price_usd),0) AS gross "
            "FROM tx_clean WHERE EXTRACT(YEAR FROM sold_at) = :y"
        ),
        engine,
        params={"y": int(year)},
    )
    gross = float(df["gross"].iloc[0])
    fees = float(df["fees"].iloc[0])
    return fees / gross if gross > 0 else 0.0


def _sleeve_allocation(engine: Engine) -> pd.DataFrame:
    _require(engine, "backtest_runs", "Phase 4")
    return pd.read_sql(
        text("SELECT sleeve, target_weight, current_weight FROM backtest_runs_latest_alloc"),
        engine,
    )
