"""Risk reporting: position caps, concentration (Herfindahl), beta to index."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from sportscards.portfolio.construction import AllocationConfig, TargetPosition


@dataclass(frozen=True)
class Violation:
    card_id: int
    sleeve: str
    weight_pct: float
    cap_pct: float


def position_size_violations(
    positions: list[TargetPosition],
    cfg: AllocationConfig,
) -> list[Violation]:
    out: list[Violation] = []
    for p in positions:
        cap = (
            cfg.anchor_position_cap_pct
            if p.sleeve == "anchor"
            else (
                cfg.prospect_per_name_cap_pct
                if p.sleeve == "prospect"
                else cfg.other_position_cap_pct
            )
        )
        if abs(p.target_weight_pct) > cap + 1e-9:
            out.append(Violation(p.card_id, p.sleeve, p.target_weight_pct, cap))
    return out


def concentration(
    positions: list[TargetPosition],
    by: Literal["player", "set", "era"],
    card_meta: Mapping[int, Mapping[str, object]],
) -> dict[str, float]:
    """Herfindahl indices keyed by group.

    ``card_meta[card_id]`` must contain the grouping key (``player``, ``set``,
    or ``era``). Era is decade-bucketed by ``year // 10 * 10`` if provided.
    """
    weights_by_group: defaultdict[str, float] = defaultdict(float)
    total = sum(abs(p.target_weight_pct) for p in positions)
    if total <= 0:
        return {}
    for p in positions:
        meta = card_meta.get(p.card_id, {})
        key_raw = meta.get(by) if by != "era" else meta.get("year")
        if by == "era" and isinstance(key_raw, int):
            key = f"{key_raw // 10 * 10}s"
        else:
            key = str(key_raw) if key_raw is not None else "unknown"
        weights_by_group[key] += abs(p.target_weight_pct) / total
    return {g: w * w for g, w in weights_by_group.items()}


def herfindahl_index(positions: list[TargetPosition]) -> float:
    """Single-number HHI across cards."""
    total = sum(abs(p.target_weight_pct) for p in positions)
    if total <= 0:
        return 0.0
    return float(sum((abs(p.target_weight_pct) / total) ** 2 for p in positions))


def beta_to_index(returns: pd.Series, index_returns: pd.Series) -> float:
    """OLS beta of portfolio returns to index returns. Returns 0.0 if degenerate."""
    df = pd.concat([returns.rename("r"), index_returns.rename("i")], axis=1).dropna()
    if len(df) < 2:
        return 0.0
    var_i = float(df["i"].var(ddof=0))
    if var_i <= 0:
        return 0.0
    cov = float(np.cov(df["r"], df["i"], ddof=0)[0, 1])
    return cov / var_i
