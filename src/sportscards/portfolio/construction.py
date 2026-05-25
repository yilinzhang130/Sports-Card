"""Barbell portfolio allocator (70% anchors / 20% factor / 10% prospects).

The only documented-edge strategy from the Phase 4 research doc (SCAA
Substack section): equal-weighted PWCC-100-tier blue-chip anchors form the
core; a mispricing factor sleeve harvests Phase 2B residuals; a small
prospect sleeve harvests Phase 3 stardom premia.

If Phase 2B (``factor_df``) or Phase 3 (``prospect_df``) inputs are missing,
the missing sleeve's target weight is redistributed pro-rata to the
remaining sleeves and a ``UserWarning`` is emitted.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Literal

import pandas as pd

Sleeve = Literal["anchor", "factor_long", "factor_short", "prospect"]


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
    tactical_tilt: bool = False
    tactical_tilt_pct: float = 0.20  # ±20% reshuffle within sleeve
    tactical_top_quintile: int = 5  # top 1/N gets boost, bottom 1/N gets cut

    def __post_init__(self) -> None:
        total = self.anchor_weight + self.factor_weight + self.prospect_weight
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"sleeve weights must sum to 1.0, got {total}")


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


def _equal_weight_with_cap(
    card_ids: list[int],
    sleeve_weight: float,
    per_name_cap: float,
    sleeve_label: Sleeve,
    aum: float,
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
        original_total = sum(p.target_weight_pct for p in sleeve_positions)

        # Rank by score; missing → 0.0
        scored = [
            (p, catalyst_scores.get(p.card_id, 0.0)) for p in sleeve_positions
        ]
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
                )
            )
        # original_total recorded for potential audit; intentionally not asserted
        _ = original_total

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
        anchor_ids, anchor_w, cfg.anchor_position_cap_pct, "anchor", aum
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
            long_ids, long_w, cfg.other_position_cap_pct, "factor_long", aum
        )
        positions.extend(lp)
        if short_ids:
            sp, _ = _equal_weight_with_cap(
                short_ids, short_w, cfg.other_position_cap_pct, "factor_short", aum
            )
            # encode shorts as negative
            positions.extend(
                TargetPosition(
                    card_id=p.card_id,
                    sleeve="factor_short",
                    target_weight_pct=-p.target_weight_pct,
                    target_usd_value=-p.target_usd_value,
                )
                for p in sp
            )

    if universe.prospect_df is not None and prospect_w > 0:
        df = universe.prospect_df.nlargest(cfg.prospect_top_n, "stardom_score")
        prospect_ids = df["card_id"].tolist()
        pp, _ = _equal_weight_with_cap(
            prospect_ids, prospect_w, cfg.prospect_per_name_cap_pct, "prospect", aum
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

    return positions


def total_long_weight(positions: list[TargetPosition]) -> float:
    return sum(p.target_weight_pct for p in positions if p.target_weight_pct > 0)
