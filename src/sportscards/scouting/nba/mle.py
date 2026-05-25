"""Major League Equivalency (MLE) multipliers for cross-league prospect stats.

Loaded from ``seed_data/league_mle.yaml``. Multipliers scale a non-NCAA
league's per-40 production into an NCAA-equivalent so the pairwise model can
compare prospects across pipelines without absorbing league-strength noise.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TypedDict

import yaml

SEED_PATH = Path(__file__).parent / "seed_data" / "league_mle.yaml"

DEFAULT_ORIGIN = "OTHER_INTL"
DEFAULT_MLE = 0.85
DEFAULT_STRENGTH_RANK = 2

# Stats that should be scaled by the MLE multiplier (volume / percent-based
# production). Pure rate fields like TS% are left untouched — shooting
# efficiency does not benefit from a league-strength bump.
SCALED_STATS = ("trb_pct", "ast_pct", "stl_pct", "blk_pct", "usg_pct")


class LeagueMLE(TypedDict):
    mle: float
    strength_rank: int


@lru_cache(maxsize=1)
def load_mle_table(path: Path = SEED_PATH) -> dict[str, LeagueMLE]:
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    leagues = data.get("leagues", {})
    out: dict[str, LeagueMLE] = {}
    for name, payload in leagues.items():
        out[str(name).upper()] = {
            "mle": float(payload.get("mle", DEFAULT_MLE)),
            "strength_rank": int(payload.get("strength_rank", DEFAULT_STRENGTH_RANK)),
        }
    return out


def mle_for(origin: str) -> LeagueMLE:
    table = load_mle_table()
    key = (origin or DEFAULT_ORIGIN).upper()
    return table.get(key, table.get(DEFAULT_ORIGIN, {"mle": DEFAULT_MLE, "strength_rank": DEFAULT_STRENGTH_RANK}))
