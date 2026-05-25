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

import numpy as np
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
    tactical_tilt: bool = False
    tactical_tilt_pct: float = 0.20  # ±20% reshuffle within sleeve
    tactical_top_quintile: int = 5  # top 1/N gets boost, bottom 1/N gets cut

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


def _apply_liquidity_hype_filters(factor_df: pd.DataFrame, *, drop_hyped: bool) -> pd.DataFrame:
    """Drop tier-D rows always; drop hyped rows when ``drop_hyped`` (long side)."""
    if factor_df.empty:
        return factor_df
    out = factor_df
    if "liquidity_tier" in out.columns:
        out = out[out["liquidity_tier"] != "D"]
    if drop_hyped and "is_hyped" in out.columns:
        out = out[~out["is_hyped"].fillna(False).astype(bool)]
    return out


def _select_factor_long(factor_df: pd.DataFrame, decile: int) -> list[int]:
    """Top-decile mispricing residuals within each (sport, parallel_tier).

    Excludes tier-D (illiquid) and is_hyped (bubble-top) names.
    """
    factor_df = _apply_liquidity_hype_filters(factor_df, drop_hyped=True)
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
    # Shorts can include hyped names (those are the bubble-tops we want to fade);
    # still exclude tier-D since we can't trade them.
    factor_df = _apply_liquidity_hype_filters(factor_df, drop_hyped=False)
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


def _momentum_tilt(
    card_ids: list[int],
    factor_df: pd.DataFrame,
    sleeve_weight: float,
    per_name_cap: float,
    sleeve_label: Sleeve,
    aum: float,
    signal_source: str,
) -> tuple[list[TargetPosition], float]:
    """Distribute ``sleeve_weight`` proportional to ``cs_momentum_pct``.

    Falls back to equal-weight when momentum data is absent. Per-name cap
    is enforced via the same iterative spillover loop as equal-weighting.
    """
    if not card_ids:
        return [], sleeve_weight
    if "cs_momentum_pct" not in factor_df.columns:
        return _equal_weight_with_cap(
            card_ids, sleeve_weight, per_name_cap, sleeve_label, aum, signal_source
        )
    weights_map: dict[int, float] = {}
    sub = factor_df[factor_df["card_id"].isin(card_ids)]
    raw = sub.set_index("card_id")["cs_momentum_pct"].astype(float).reindex(card_ids).fillna(0.5)
    if raw.sum() <= 0:
        return _equal_weight_with_cap(
            card_ids, sleeve_weight, per_name_cap, sleeve_label, aum, signal_source
        )

    remaining = sleeve_weight
    raw_arr = raw.to_numpy()
    base_alloc = (raw_arr / raw_arr.sum()) * sleeve_weight
    for cid, w in zip(card_ids, base_alloc, strict=True):
        weights_map[cid] = min(float(w), per_name_cap)
        remaining -= weights_map[cid]

    # Spill any cap-cut weight to the under-cap names, proportional to their
    # raw momentum score (not yet at cap).
    while remaining > 1e-12:
        eligible = [cid for cid in card_ids if weights_map[cid] < per_name_cap - 1e-12]
        if not eligible:
            break
        elig_raw = np.array([float(raw[cid]) if raw[cid] > 0 else 1e-9 for cid in eligible])
        total = elig_raw.sum()
        progressed = False
        for cid, r in zip(eligible, elig_raw, strict=True):
            room = per_name_cap - weights_map[cid]
            add = min(room, remaining * (r / total))
            if add <= 0:
                continue
            weights_map[cid] += add
            remaining -= add
            progressed = True
        if not progressed:
            break

    positions = [
        TargetPosition(
            card_id=cid,
            sleeve=sleeve_label,
            target_weight_pct=w,
            target_usd_value=w * aum,
            signal_source=signal_source,
        )
        for cid, w in weights_map.items()
        if w > 1e-12
    ]
    return positions, max(remaining, 0.0)


_DEFAULT_ALLOC = AllocationConfig()


def _sleeve_cap(sleeve: Sleeve, cfg: AllocationConfig) -> float:
    if sleeve == "anchor":
        return cfg.anchor_position_cap_pct
    if sleeve == "prospect":
        return cfg.prospect_per_name_cap_pct
    return cfg.other_position_cap_pct


def _apply_tactical_tilt(
    positions: list[TargetPosition],
    catalyst_scores: dict[int, float],
    cfg: AllocationConfig,
    aum: float,
) -> list[TargetPosition]:
    """Reshuffle ±tactical_tilt_pct within each sleeve based on catalyst score.

    Sleeve total weight (sign-preserving) is preserved within float epsilon.
    Per-name caps are clamped after tilt (surplus is dropped — sleeve total
    may shrink slightly when clamping binds).
    """
    if not positions:
        return positions

    by_sleeve: dict[Sleeve, list[TargetPosition]] = {}
    for p in positions:
        by_sleeve.setdefault(p.sleeve, []).append(p)

    tilt = cfg.tactical_tilt_pct
    quintile = cfg.tactical_top_quintile
    out: list[TargetPosition] = []

    for sleeve, sleeve_positions in by_sleeve.items():
        n = len(sleeve_positions)
        if n == 0:
            out.extend(sleeve_positions)
            continue

        # Preserve sign — shorts are negative; tilt by absolute magnitude
        sign = -1.0 if sleeve == "factor_short" else 1.0

        # Rank by score; missing → 0.0
        scored = [(p, catalyst_scores.get(p.card_id, 0.0)) for p in sleeve_positions]
        scored.sort(key=lambda x: x[1], reverse=True)

        k = max(0, n // quintile)
        top_ids = {scored[i][0].card_id for i in range(k)}
        bot_ids = {scored[n - 1 - i][0].card_id for i in range(k)} if k > 0 else set()
        # Avoid overlap when n is tiny (e.g. n < 2*quintile)
        bot_ids -= top_ids

        cap = _sleeve_cap(sleeve, cfg)

        new_weights: dict[int, float] = {}
        total_boost = 0.0
        total_cut = 0.0
        for p in sleeve_positions:
            abs_w = abs(p.target_weight_pct)
            if p.card_id in top_ids:
                delta = tilt * abs_w
                new_w = abs_w + delta
                total_boost += delta
            elif p.card_id in bot_ids:
                delta = tilt * abs_w
                new_w = abs_w - delta
                total_cut += delta
            else:
                new_w = abs_w
            new_weights[p.card_id] = new_w

        # Renormalize: if boost > cut, trim middle (and overflow into top redistribution
        # if everything is top/bottom); if cut > boost, expand middle.
        net = total_boost - total_cut
        middle_ids = [
            p.card_id
            for p in sleeve_positions
            if p.card_id not in top_ids and p.card_id not in bot_ids
        ]
        if abs(net) > 1e-12:
            if middle_ids:
                middle_total = sum(new_weights[cid] for cid in middle_ids)
                if middle_total > 1e-12:
                    factor = max(0.0, (middle_total - net) / middle_total)
                    for cid in middle_ids:
                        new_weights[cid] *= factor
                else:
                    # middle weights are zero — distribute against top/bottom instead
                    if net > 0 and top_ids:
                        # need to cut boost
                        per = net / len(top_ids)
                        for cid in top_ids:
                            new_weights[cid] = max(0.0, new_weights[cid] - per)
                    elif net < 0 and bot_ids:
                        per = (-net) / len(bot_ids)
                        for cid in bot_ids:
                            new_weights[cid] += per
            else:
                # No middle: adjust top/bottom symmetrically
                if net > 0 and top_ids:
                    per = net / len(top_ids)
                    for cid in top_ids:
                        new_weights[cid] = max(0.0, new_weights[cid] - per)
                elif net < 0 and bot_ids:
                    per = (-net) / len(bot_ids)
                    for cid in bot_ids:
                        new_weights[cid] += per

        # Clamp at per-name cap (sleeve total may shrink slightly when binds)
        for cid in new_weights:
            if new_weights[cid] > cap:
                new_weights[cid] = cap

        # Rebuild positions in original order; drop zero-weight names
        for p in sleeve_positions:
            w = new_weights[p.card_id]
            if w <= 1e-12:
                continue
            signed_w = sign * w
            out.append(
                TargetPosition(
                    card_id=p.card_id,
                    sleeve=sleeve,
                    target_weight_pct=signed_w,
                    target_usd_value=signed_w * aum,
                    signal_source=p.signal_source,
                )
            )

    return out


def build_portfolio(
    universe: UniverseSnapshot,
    cfg: AllocationConfig = _DEFAULT_ALLOC,
    *,
    catalyst_scores: dict[int, float] | None = None,
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
        lp, _ = _momentum_tilt(
            long_ids,
            universe.factor_df,
            long_w,
            cfg.other_position_cap_pct,
            "factor_long",
            aum,
            "factor",
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

    if cfg.tactical_tilt:
        if catalyst_scores is None:
            warnings.warn(
                "tactical_tilt requested but catalyst_scores not provided — skipping tilt",
                stacklevel=2,
            )
        else:
            positions = _apply_tactical_tilt(positions, catalyst_scores, cfg, aum)

    # --- optional grading-arbitrage overlay (runs after tactical tilt; this
    # sleeve holds raw inventory and isn't subject to catalyst tilts) ---
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
