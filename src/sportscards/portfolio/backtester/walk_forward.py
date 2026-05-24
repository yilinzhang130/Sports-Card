"""Walk-forward backtester with liquidity-constrained execution.

Design choices (per Phase 4 spec):
- Factor models refit every ``rebalance_freq_days`` (default 90) using only
  data ``< rebalance_date`` — look-ahead is enforced by the adapter layer.
- Weekly turnover per card capped at
  ``min(target_delta, weekly_turnover_pct_cap × 90d sales count)`` measured
  in card units. Partial fills carry to the next week.
- Fees deducted via :mod:`sportscards.portfolio.transaction_costs` on every
  buy and sell.
- NAV marked daily using last-observation-carried-forward VWAP (cards are
  illiquid; many days have no sales).

The backtester accepts callables (``get_universe``, ``price_panel``) so
tests can inject synthetic data without a database.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from sportscards.portfolio.construction import (
    AllocationConfig,
    TargetPosition,
    UniverseSnapshot,
    build_portfolio,
)
from sportscards.portfolio.transaction_costs import (
    BuyChannel,
    FeeSchedule,
    SellChannel,
    landed_cost,
    net_proceeds,
)

UniverseProvider = Callable[[pd.Timestamp], UniverseSnapshot]


@dataclass(frozen=True)
class BacktestConfig:
    start: date
    end: date
    rebalance_freq_days: int = 90
    initial_aum_usd: float = 1_000_000.0
    weekly_turnover_pct_cap: float = 0.50
    fee_schedule: FeeSchedule = field(default_factory=FeeSchedule)
    allocation: AllocationConfig = field(default_factory=AllocationConfig)
    trading_channel_buy: BuyChannel = "ebay"
    trading_channel_sell: SellChannel = "ebay"


@dataclass
class BacktestResult:
    nav: pd.Series
    weights_history: pd.DataFrame
    trades: pd.DataFrame
    fees_paid: pd.Series
    drawdown: pd.Series
    summary: dict[str, Any]


def _price_lookup(panel: pd.DataFrame) -> pd.DataFrame:
    """Return wide-format daily LOCF price frame indexed by date, cards as cols."""
    if panel.empty:
        return pd.DataFrame()
    panel = panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    wide = panel.pivot_table(index="date", columns="card_id", values="vwap", aggfunc="mean")
    full_idx = pd.date_range(wide.index.min(), wide.index.max(), freq="D")
    return wide.reindex(full_idx).ffill()


def _sales_count_90d(panel: pd.DataFrame, card_id: int, as_of: pd.Timestamp) -> int:
    if panel.empty:
        return 0
    cutoff = as_of - pd.Timedelta(days=90)
    sub = panel[(panel["card_id"] == card_id) & (panel["date"] >= cutoff) & (panel["date"] < as_of)]
    if "volume" in sub.columns:
        return int(sub["volume"].sum())
    return int(len(sub))


def _target_units(
    targets: list[TargetPosition],
    prices: pd.Series,
    aum: float,
) -> dict[int, float]:
    out: dict[int, float] = {}
    for t in targets:
        p = prices.get(t.card_id)
        if p is None or pd.isna(p) or p <= 0:
            continue
        out[t.card_id] = (t.target_weight_pct * aum) / float(p)
    return out


def run_backtest(
    cfg: BacktestConfig,
    get_universe: UniverseProvider,
    price_panel: pd.DataFrame,
) -> BacktestResult:
    """Run the walk-forward backtest.

    ``price_panel`` is long-format ``(date, card_id, vwap, [volume])`` covering
    ``cfg.start`` through ``cfg.end``.
    ``get_universe(as_of)`` returns the candidate universe known at ``as_of``.
    """
    start_ts = pd.Timestamp(cfg.start)
    end_ts = pd.Timestamp(cfg.end)
    daily_dates = pd.date_range(start_ts, end_ts, freq="D")
    prices_wide = _price_lookup(price_panel)

    panel_idx = price_panel.copy()
    if not panel_idx.empty:
        panel_idx["date"] = pd.to_datetime(panel_idx["date"])

    holdings: dict[int, float] = {}  # card_id -> units
    cash = cfg.initial_aum_usd
    target_units: dict[int, float] = {}
    nav_series: list[float] = []
    fees_series: list[float] = []
    weights_records: list[dict[str, Any]] = []
    trade_records: list[dict[str, Any]] = []

    next_rebalance = start_ts
    next_trade_window = start_ts

    for d in daily_dates:
        # Mark NAV
        prices_today = (
            pd.Series(prices_wide.loc[d]) if d in prices_wide.index else pd.Series(dtype=float)
        )
        portfolio_value = cash + sum(
            units * float(prices_today.get(cid, 0.0) or 0.0) for cid, units in holdings.items()
        )

        # Rebalance: compute new targets using info < d
        if d >= next_rebalance:
            universe = get_universe(d)
            positions = build_portfolio(universe, cfg.allocation)
            for p in positions:
                weights_records.append(
                    {
                        "date": d,
                        "card_id": p.card_id,
                        "sleeve": p.sleeve,
                        "weight_pct": p.target_weight_pct,
                    }
                )
            aum_for_targets = portfolio_value
            target_units = _target_units(positions, prices_today, aum_for_targets)
            next_rebalance = d + pd.Timedelta(days=cfg.rebalance_freq_days)

        # Weekly execution toward target
        fees_today = 0.0
        if d >= next_trade_window and target_units:
            all_cids = set(target_units) | set(holdings)
            for cid in all_cids:
                tgt = target_units.get(cid, 0.0)
                cur = holdings.get(cid, 0.0)
                delta = tgt - cur
                if abs(delta) < 1e-9:
                    continue
                px = float(prices_today.get(cid, np.nan))
                if not np.isfinite(px) or px <= 0:
                    continue
                liq_cap = cfg.weekly_turnover_pct_cap * _sales_count_90d(panel_idx, cid, d)
                if liq_cap <= 0:
                    continue
                trade_units = float(np.clip(delta, -liq_cap, liq_cap))
                if trade_units > 0:
                    cost = landed_cost(trade_units * px, cfg.trading_channel_buy, cfg.fee_schedule)
                    if cost > cash:
                        scale = cash / cost if cost > 0 else 0
                        trade_units *= scale
                        cost *= scale
                    cash -= cost
                    holdings[cid] = cur + trade_units
                    fees_today += cost - trade_units * px
                    trade_records.append(
                        {
                            "date": d,
                            "card_id": cid,
                            "units": trade_units,
                            "price": px,
                            "fees": cost - trade_units * px,
                        }
                    )
                elif trade_units < 0:
                    sell_units = -trade_units
                    proceeds = net_proceeds(
                        sell_units * px, cfg.trading_channel_sell, cfg.fee_schedule
                    )
                    cash += proceeds
                    holdings[cid] = cur + trade_units
                    fees_today += sell_units * px - proceeds
                    trade_records.append(
                        {
                            "date": d,
                            "card_id": cid,
                            "units": trade_units,
                            "price": px,
                            "fees": sell_units * px - proceeds,
                        }
                    )
            next_trade_window = d + pd.Timedelta(days=7)

        # Re-mark after trades
        prices_today = (
            pd.Series(prices_wide.loc[d]) if d in prices_wide.index else pd.Series(dtype=float)
        )
        portfolio_value = cash + sum(
            units * float(prices_today.get(cid, 0.0) or 0.0) for cid, units in holdings.items()
        )
        nav_series.append(portfolio_value)
        fees_series.append(fees_today)

    nav = pd.Series(nav_series, index=daily_dates, name="nav")
    fees = pd.Series(fees_series, index=daily_dates, name="fees")
    running_max = nav.cummax()
    drawdown = (nav / running_max - 1.0).rename("drawdown")

    returns = nav.pct_change().fillna(0.0)
    ann = float(np.sqrt(252))
    mean_ret = float(returns.mean())
    std_ret = float(returns.std(ddof=0)) or 0.0
    ir = (mean_ret * 252 / (std_ret * ann)) if std_ret > 0 else 0.0
    total_return = float(nav.iloc[-1] / cfg.initial_aum_usd - 1.0)
    total_fees = float(fees.sum())
    fee_drag_pct = total_fees / cfg.initial_aum_usd if cfg.initial_aum_usd > 0 else 0.0

    summary = {
        "start": str(cfg.start),
        "end": str(cfg.end),
        "initial_aum": cfg.initial_aum_usd,
        "final_nav": float(nav.iloc[-1]),
        "total_return": total_return,
        "ir": float(ir),
        "max_drawdown": float(drawdown.min()),
        "fee_drag_pct": fee_drag_pct,
        "total_fees_usd": total_fees,
        "trade_count": len(trade_records),
    }

    return BacktestResult(
        nav=nav,
        weights_history=pd.DataFrame(weights_records),
        trades=pd.DataFrame(trade_records),
        fees_paid=fees,
        drawdown=drawdown,
        summary=summary,
    )


def synthetic_flat_panel(
    card_ids: list[int], start: date, end: date, price: float = 100.0, daily_volume: int = 10
) -> pd.DataFrame:
    """Helper for tests: flat-price daily panel with constant volume per card."""
    dates = pd.date_range(start, end, freq="D")
    rows = [
        {"date": d.date(), "card_id": cid, "vwap": price, "volume": daily_volume}
        for d in dates
        for cid in card_ids
    ]
    return pd.DataFrame(rows)
