"""Barbell portfolio allocator (70% anchors / 20% factor / 10% prospects).

The only documented-edge strategy from the Phase 4 research doc (SCAA
Substack section): equal-weighted PWCC-100-tier blue-chip anchors form the
core; a mispricing factor sleeve harvests Phase 2B residuals; a small
prospect sleeve harvests Phase 3 stardom premia.

If Phase 2B (``factor_df``) or Phase 3 (``prospect_df``) inputs are missing,
the missing sleeve's target weight is redistributed pro-rata to the
remaining sleeves and a ``UserWarning`` is emitted.

An optional ``grading_arbitrage_weight`` (0–0.10) funds a raw-card sleeve
sourced from ``rank_grading_candidates``.  It is an *overlay* on top of the
normal barbell — the three core weights must still sum to 1.0.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass
from datetime import UTC
from typing import Literal

import pandas as pd

logger = logging.getLogger(__name__)

Sleeve = Literal["anchor", "factor_long", "factor_short", "prospect", "grading_arbitrage"]


@dataclass(frozen=True)
class AllocationConfig:
    total_aum_usd: float = 1_000_000.0
    anchor_weight: float = 0.70
    factor_weight: float = 0.20
    prospect_weight: float = 0.10
    anchor_position_cap_pct: float = 0.10
    other_position_cap_pct: float = 0.01
    prospect_per_name_cap_pct: float = 0.05
    factor_decile_long: int = 10
    factor_decile_short: int | None = None
    prospect_top_n: int = 15
    # Opt-in grading-arbitrage overlay (0.0 = disabled, max effective value = 0.10).
    # This is an *additional* sleeve funded from AUM on top of the core barbell;
    # anchor_weight + factor_weight + prospect_weight must still sum to 1.0.
    grading_arbitrage_weight: float = 0.0

    def __post_init__(self) -> None:
        total = self.anchor_weight + self.factor_weight + self.prospect_weight
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"sleeve weights must sum to 1.0, got {total}")
        if not (0.0 <= self.grading_arbitrage_weight <= 1.0):
            raise ValueError(
                f"grading_arbitrage_weight must be in [0, 1], got {self.grading_arbitrage_weight}"
            )


@dataclass(frozen=True)
class UniverseSnapshot:
    anchors_df: pd.DataFrame
    factor_df: pd.DataFrame | None = None
    prospect_df: pd.DataFrame | None = None


@dataclass(frozen=True)
class TargetPosition:
    card_id: int
    sleeve: Sleeve
    target_weight_pct: float
    target_usd_value: float
    signal_source: str  # "anchor" | "factor" | "prospect"


def _equal_weight_with_cap(
    card_ids: list[int],
    sleeve_weight: float,
    per_name_cap: float,
    sleeve_label: Sleeve,
    aum: float,
    signal_source: str,
) -> tuple[list[TargetPosition], float]:
    """Distribute ``sleeve_weight`` equally subject to ``per_name_cap``.

    Returns ``(positions, unallocated_weight)``. ``unallocated_weight`` is
    >0 when caps bind before the full sleeve is deployed.
    """
    if not card_ids:
        return [], sleeve_weight
    remaining = sleeve_weight
    out: dict[int, float] = {}
    pool = list(card_ids)
    while pool and remaining > 1e-12:
        share = remaining / len(pool)
        next_pool: list[int] = []
        progressed = False
        for cid in pool:
            current = out.get(cid, 0.0)
            room = per_name_cap - current
            if room <= 1e-12:
                continue
            take = min(share, room)
            out[cid] = current + take
            remaining -= take
            if per_name_cap - out[cid] > 1e-12:
                next_pool.append(cid)
            progressed = True
        if not progressed:
            break
        pool = next_pool
    positions = [
        TargetPosition(
            card_id=cid,
            sleeve=sleeve_label,
            target_weight_pct=w,
            target_usd_value=w * aum,
            signal_source=signal_source,
        )
        for cid, w in out.items()
    ]
    return positions, max(remaining, 0.0)


def _select_factor_long(factor_df: pd.DataFrame, decile: int) -> list[int]:
    """Top-decile mispricing residuals within each (sport, parallel_tier)."""
    if factor_df.empty:
        return []
    group_cols = [c for c in ("sport", "parallel_tier") if c in factor_df.columns]
    if not group_cols:
        factor_df = factor_df.assign(_grp="all")
        group_cols = ["_grp"]
    selected: list[int] = []
    for _, grp in factor_df.groupby(group_cols, dropna=False):
        n = max(1, len(grp) // decile)
        top = grp.nlargest(n, "mispricing_residual")
        selected.extend(top["card_id"].tolist())
    return selected


def _select_factor_short(factor_df: pd.DataFrame, decile: int) -> list[int]:
    if factor_df.empty:
        return []
    group_cols = [c for c in ("sport", "parallel_tier") if c in factor_df.columns]
    if not group_cols:
        factor_df = factor_df.assign(_grp="all")
        group_cols = ["_grp"]
    selected: list[int] = []
    for _, grp in factor_df.groupby(group_cols, dropna=False):
        n = max(1, len(grp) // decile)
        bot = grp.nsmallest(n, "mispricing_residual")
        selected.extend(bot["card_id"].tolist())
    return selected


_DEFAULT_ALLOC = AllocationConfig()


def build_portfolio(
    universe: UniverseSnapshot,
    cfg: AllocationConfig = _DEFAULT_ALLOC,
) -> list[TargetPosition]:
    """Build a barbell portfolio. Returns target positions summing to ≤ 1.0."""
    anchor_w = cfg.anchor_weight
    factor_w = cfg.factor_weight if universe.factor_df is not None else 0.0
    prospect_w = cfg.prospect_weight if universe.prospect_df is not None else 0.0

    missing = []
    if universe.factor_df is None:
        missing.append("factor")
    if universe.prospect_df is None:
        missing.append("prospect")
    if missing:
        warnings.warn(
            f"missing sleeves ({', '.join(missing)}) — redistributing to anchors. "
            "Phase 2B / Phase 3 data not available yet.",
            stacklevel=2,
        )
        # redistribute missing weight pro-rata to anchor (only present sleeve guaranteed)
        unallocated = (
            cfg.anchor_weight
            + cfg.factor_weight
            + cfg.prospect_weight
            - anchor_w
            - factor_w
            - prospect_w
        )
        anchor_w += unallocated

    aum = cfg.total_aum_usd
    positions: list[TargetPosition] = []

    anchor_ids = universe.anchors_df["card_id"].tolist() if not universe.anchors_df.empty else []
    anchor_positions, anchor_unalloc = _equal_weight_with_cap(
        anchor_ids, anchor_w, cfg.anchor_position_cap_pct, "anchor", aum, "anchor"
    )
    positions.extend(anchor_positions)

    if universe.factor_df is not None and factor_w > 0:
        long_ids = _select_factor_long(universe.factor_df, cfg.factor_decile_long)
        if cfg.factor_decile_short is not None:
            short_ids = _select_factor_short(universe.factor_df, cfg.factor_decile_short)
            long_w = factor_w / 2
            short_w = factor_w / 2
        else:
            short_ids = []
            long_w = factor_w
            short_w = 0.0
        lp, _ = _equal_weight_with_cap(
            long_ids, long_w, cfg.other_position_cap_pct, "factor_long", aum, "factor"
        )
        positions.extend(lp)
        if short_ids:
            sp, _ = _equal_weight_with_cap(
                short_ids, short_w, cfg.other_position_cap_pct, "factor_short", aum, "factor"
            )
            # encode shorts as negative
            positions.extend(
                TargetPosition(
                    card_id=p.card_id,
                    sleeve="factor_short",
                    target_weight_pct=-p.target_weight_pct,
                    target_usd_value=-p.target_usd_value,
                    signal_source="factor",
                )
                for p in sp
            )

    if universe.prospect_df is not None and prospect_w > 0:
        df = universe.prospect_df.nlargest(cfg.prospect_top_n, "stardom_score")
        prospect_ids = df["card_id"].tolist()
        pp, _ = _equal_weight_with_cap(
            prospect_ids, prospect_w, cfg.prospect_per_name_cap_pct, "prospect", aum, "prospect"
        )
        positions.extend(pp)

    # --- optional grading-arbitrage overlay ---
    if cfg.grading_arbitrage_weight > 0:
        from datetime import datetime

        from sportscards.db.session import session_scope
        from sportscards.factors.grading_ev import rank_grading_candidates

        _MAX_GA_WEIGHT = 0.10
        ga_weight = min(cfg.grading_arbitrage_weight, _MAX_GA_WEIGHT)
        sleeve_budget = ga_weight * aum
        candidates: pd.DataFrame | None = None
        try:
            with session_scope() as _ga_session:
                candidates = rank_grading_candidates(_ga_session, datetime.now(tz=UTC))
        except Exception:
            logger.warning(
                "grading_ev sleeve: rank_grading_candidates failed; skipping sleeve",
                exc_info=True,
            )
        if candidates is not None and not candidates.empty:
            for _, row in candidates.iterrows():
                if sleeve_budget <= 0:
                    break
                cost = float(row["raw_price"])
                if cost <= sleeve_budget:
                    w_pct = cost / aum
                    positions.append(
                        TargetPosition(
                            card_id=int(row["card_id"]),
                            sleeve="grading_arbitrage",
                            target_weight_pct=w_pct,
                            target_usd_value=cost,
                            signal_source="grading_arbitrage",
                        )
                    )
                    sleeve_budget -= cost

    return positions


def total_long_weight(positions: list[TargetPosition]) -> float:
    return sum(p.target_weight_pct for p in positions if p.target_weight_pct > 0)
