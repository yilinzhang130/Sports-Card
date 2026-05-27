"""Read-only DB queries for the reporting layer.

Every query checks that its source table exists and raises
`TableMissing` if not, so the dashboard/letter can render a
placeholder instead of crashing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar

import pandas as pd
from sqlalchemy import bindparam, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

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
    params: dict[str, int] = {}
    if draft_year is not None:
        sql += "WHERE s.draft_year = :dy "
        params["dy"] = draft_year
    sql += "ORDER BY s.premium DESC"
    return pd.read_sql(text(sql), eng, params=params)


def forward_prospects(
    engine: Engine | None = None,
    draft_year: int | None = None,
) -> pd.DataFrame:
    """Latest forward-looking prospect snapshot.

    Returns one row per prospect from the most recent ``as_of_date``
    (per draft_year), ranked by premium DESC. If ``draft_year`` is None,
    the table's max draft_year is used (typically the upcoming class).
    """
    eng = _engine(engine)
    _require(eng, "prospect_forecast", "Phase 3")
    if draft_year is None:
        max_year = pd.read_sql(text("SELECT MAX(draft_year) AS y FROM prospect_forecast"), eng)[
            "y"
        ].iloc[0]
        if max_year is None:
            return pd.DataFrame()
        draft_year = int(max_year)
    sql = text(
        "SELECT player_slug, name, draft_year, premium, pairwise_score, "
        "       consensus_rank, sources_count, is_underclassman, "
        "       years_until_draft, prior_league, n_games_played, as_of_date "
        "FROM prospect_forecast "
        "WHERE draft_year = :dy "
        "  AND as_of_date = ("
        "      SELECT MAX(as_of_date) FROM prospect_forecast WHERE draft_year = :dy"
        "  )"
    )
    df = pd.read_sql(sql, eng, params={"dy": draft_year})
    return df.sort_values("premium", ascending=False, na_position="last").reset_index(drop=True)


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


# --- Factor panel (momentum + liquidity) ---------------------------------------


def factor_panel_latest(engine: Engine | None = None) -> pd.DataFrame:
    """Latest factor_panel snapshot joined to card_master / player_master."""
    eng = _engine(engine)
    _require(eng, "factor_panel", "Phase 5 (factors)")
    sql = text(
        "SELECT f.card_id, p.name AS player, c.year, c.set AS set_name, c.parallel, "
        "       f.as_of_date, f.r30, f.r90, f.r365, f.cs_momentum_pct, f.is_hyped, "
        "       f.sales_count_90d, f.dollar_volume_90d, f.bid_ask_proxy, "
        "       f.last_sale_recency_days, f.liquidity_tier "
        "FROM factor_panel f "
        "JOIN card_master c ON c.card_id = f.card_id "
        "JOIN player_master p ON p.player_id = c.player_id "
        "WHERE f.as_of_date = (SELECT MAX(as_of_date) FROM factor_panel) "
        "ORDER BY f.cs_momentum_pct DESC NULLS LAST"
    )
    return pd.read_sql(sql, eng)


# --- Catalysts (Phase 5) -------------------------------------------------------


def recent_events(engine: Engine | None = None, days: int = 30, limit: int = 100) -> pd.DataFrame:
    """Latest events in the past `days`.

    Columns: event_date, player_name, event_type, event_payload.
    """
    eng = _engine(engine)
    _require(eng, "player_events", "Phase 5 catalyst")
    cutoff = datetime.now(UTC) - timedelta(days=days)
    sql = text(
        "SELECT e.event_date, p.name AS player_name, e.event_type, e.event_payload "
        "FROM player_events e "
        "JOIN player_master p ON p.player_id = e.player_id "
        "WHERE e.event_date >= :cutoff "
        "ORDER BY e.event_date DESC "
        "LIMIT :lim"
    )
    return pd.read_sql(sql, eng, params={"cutoff": cutoff, "lim": limit})  # type: ignore[arg-type]


def top_catalysts(engine: Engine | None = None, days: int = 30, limit: int = 10) -> pd.DataFrame:
    """Top N players by absolute catalyst score over the past `days`.

    Columns: player_id, player_name, catalyst_score.
    """
    from sportscards.factors.catalyst import compute_catalyst_scores_bulk

    eng = _engine(engine)
    _require(eng, "player_events", "Phase 5 catalyst")
    cutoff = datetime.now(UTC) - timedelta(days=days)
    as_of = datetime.now(UTC)

    with Session(eng) as session:
        pid_rows = session.execute(
            text("SELECT DISTINCT player_id FROM player_events WHERE event_date >= :cutoff"),
            {"cutoff": cutoff},
        ).all()
        player_ids = [int(r[0]) for r in pid_rows]
        if not player_ids:
            return pd.DataFrame(columns=["player_id", "player_name", "catalyst_score"])
        scores = compute_catalyst_scores_bulk(session, player_ids, as_of)
        name_rows = session.execute(
            text("SELECT player_id, name FROM player_master WHERE player_id IN :pids").bindparams(
                bindparam("pids", expanding=True)
            ),
            {"pids": player_ids},
        ).all()
        names = {int(pid): name for pid, name in name_rows}

    rows = [
        {
            "player_id": pid,
            "player_name": names.get(pid, ""),
            "catalyst_score": float(score),
        }
        for pid, score in scores.items()
    ]
    df = pd.DataFrame(rows, columns=["player_id", "player_name", "catalyst_score"])
    if df.empty:
        return df
    df = df.reindex(df["catalyst_score"].abs().sort_values(ascending=False).index)
    return df.head(limit).reset_index(drop=True)


def player_catalyst_sparkline(
    player_id: int,
    engine: Engine | None = None,
    days: int = 180,
    step_days: int = 7,
) -> pd.DataFrame:
    """Time series of catalyst_score for one player.

    Sampled every `step_days` over the past `days`. Columns: as_of, catalyst_score.
    """
    from sportscards.factors.catalyst import compute_catalyst_score

    eng = _engine(engine)
    _require(eng, "player_events", "Phase 5 catalyst")

    today = datetime.now(UTC)
    start = today - timedelta(days=days)
    rows: list[dict[str, object]] = []
    with Session(eng) as session:
        cur = start
        while cur <= today:
            score = compute_catalyst_score(session, player_id, cur)
            rows.append({"as_of": cur, "catalyst_score": float(score)})
            cur = cur + timedelta(days=step_days)
    return pd.DataFrame(rows, columns=["as_of", "catalyst_score"])


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


# --- Grading EV leaderboard (grading-ev) --------------------------------------


def grading_ev_leaderboard(engine: Engine | None = None, n: int = 50) -> pd.DataFrame:
    """Top-N cards by EV-per-dollar for raw→PSA 10 grading, most recent snapshot."""
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
    return pd.read_sql(text(sql), eng, params={"n": n})


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

    T = TypeVar("T")

    def _try(fn: Callable[[], T]) -> T | None:
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


# --- Trader Console (Phase 6) --------------------------------------------------


from collections.abc import Iterable  # noqa: E402
from datetime import date  # noqa: E402

from sqlalchemy import select  # noqa: E402

from sportscards.db.models import ExitSignal, TradeTargets  # noqa: E402
from sportscards.db.session import session_scope  # noqa: E402


def get_trade_targets(*, card_ids: Iterable[int], as_of: date) -> pd.DataFrame:
    """Return trade-target rows for the given card IDs on ``as_of``."""
    ids = list(card_ids)
    if not ids:
        return pd.DataFrame(
            columns=[
                "card_id", "as_of_date", "fair_value", "bid_max",
                "sell_target", "stop_loss", "confidence",
                "half_spread_pct", "liquidity_margin_pct",
            ]
        )
    with session_scope() as s:
        rows = s.execute(
            select(TradeTargets)
            .where(TradeTargets.card_id.in_(ids))
            .where(TradeTargets.as_of_date == as_of)
        ).scalars().all()
        return pd.DataFrame(
            [
                {
                    "card_id": r.card_id,
                    "as_of_date": r.as_of_date,
                    "fair_value": float(r.fair_value),
                    "bid_max": float(r.bid_max),
                    "sell_target": float(r.sell_target),
                    "stop_loss": float(r.stop_loss),
                    "confidence": float(r.confidence),
                    "half_spread_pct": float(r.half_spread_pct),
                    "liquidity_margin_pct": float(r.liquidity_margin_pct),
                }
                for r in rows
            ]
        )


def get_open_exit_signals() -> pd.DataFrame:
    """Return all unresolved exit signals, newest first."""
    with session_scope() as s:
        rows = s.execute(
            select(ExitSignal)
            .where(ExitSignal.resolved_at.is_(None))
            .order_by(ExitSignal.as_of_date.desc(), ExitSignal.id.desc())
        ).scalars().all()
        return pd.DataFrame(
            [
                {
                    "id": r.id,
                    "holding_id": r.holding_id,
                    "rule_triggered": r.rule_triggered,
                    "recommended_action": r.recommended_action,
                    "as_of_date": r.as_of_date,
                    "notes": r.notes,
                    "resolved_at": r.resolved_at,
                }
                for r in rows
            ]
        )


def resolve_exit_signal(signal_id: int) -> None:
    """Mark an exit signal as resolved (sets resolved_at to now)."""
    with session_scope() as s:
        sig = s.get(ExitSignal, signal_id)
        if sig is None:
            return
        sig.resolved_at = datetime.now(tz=UTC)
