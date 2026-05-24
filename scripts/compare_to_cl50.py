"""Compare our cert-based repeat-sales index with Card Ladder CL50.

CL50 is a price-averaged basket (selection-biased, not a true index), so we
expect material divergence — that's the point. This script quantifies it.

Usage:
    python scripts/compare_to_cl50.py \
        --cl50 path/to/cl50.csv \
        --grade-tier PSA10 \
        --bucket weekly \
        --era modern \
        --out docs/index_vs_cl50.md

The CL50 CSV is expected to have at least two columns:
    date  — anything pd.to_datetime can parse
    value — CL50 level
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select

from sportscards.db.models import RepeatSalesIndex
from sportscards.db.session import session_scope


def _weekly_monday(s: pd.Series) -> pd.Series:
    s = pd.to_datetime(s, utc=True)
    return (s - pd.to_timedelta(s.dt.weekday, unit="D")).dt.floor("D")


def _load_cl50(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if not {"date", "value"}.issubset(df.columns):
        raise ValueError("CL50 CSV must have 'date' and 'value' columns")
    df["period_start"] = _weekly_monday(df["date"])
    return (
        df.groupby("period_start", as_index=False)["value"].mean().rename(columns={"value": "cl50"})
    )


def _load_ours(sport: str, bucket: str, grade_tier: str, era: str) -> pd.DataFrame:
    with session_scope() as s:
        rows = s.execute(
            select(
                RepeatSalesIndex.period_start,
                RepeatSalesIndex.index_value,
                RepeatSalesIndex.n_pairs,
            )
            .where(
                RepeatSalesIndex.sport == sport,
                RepeatSalesIndex.bucket == bucket,
                RepeatSalesIndex.grade_tier == grade_tier,
                RepeatSalesIndex.era == era,
            )
            .order_by(RepeatSalesIndex.period_start)
        ).all()
    return pd.DataFrame(
        [
            {
                "period_start": r.period_start,
                "ours": float(r.index_value) if r.index_value is not None else np.nan,
                "n_pairs": r.n_pairs,
            }
            for r in rows
        ]
    )


def _ascii_plot(series: pd.Series, width: int = 60, height: int = 8) -> str:
    s = series.dropna().to_numpy()
    if len(s) < 2:
        return "(insufficient data)"
    lo, hi = float(s.min()), float(s.max())
    if hi == lo:
        return "(flat)"
    cols = min(width, len(s))
    step = len(s) / cols
    sampled = np.array([s[int(i * step)] for i in range(cols)])
    rows: list[str] = []
    for r in range(height, 0, -1):
        thresh = lo + (hi - lo) * (r - 0.5) / height
        rows.append("".join("█" if v >= thresh else " " for v in sampled))
    return "\n".join(rows) + f"\n  min={lo:.2f}  max={hi:.2f}  n={len(s)}"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cl50", required=True, type=Path)
    p.add_argument("--sport", default="NBA")
    p.add_argument("--bucket", default="weekly")
    p.add_argument("--grade-tier", default="PSA10")
    p.add_argument("--era", default="modern")
    p.add_argument("--out", type=Path, default=Path("docs/index_vs_cl50.md"))
    args = p.parse_args()

    cl50 = _load_cl50(args.cl50)
    ours = _load_ours(args.sport, args.bucket, args.grade_tier, args.era)

    merged = ours.merge(cl50, on="period_start", how="inner").dropna(subset=["ours", "cl50"])
    if len(merged) < 4:
        raise SystemExit(f"only {len(merged)} overlapping periods; nothing to compare")

    log_ours = np.log(merged["ours"].to_numpy())
    log_cl50 = np.log(merged["cl50"].to_numpy())
    ret_ours = np.diff(log_ours)
    ret_cl50 = np.diff(log_cl50)
    level_corr = float(np.corrcoef(log_ours - log_ours.mean(), log_cl50 - log_cl50.mean())[0, 1])
    return_corr = float(np.corrcoef(ret_ours, ret_cl50)[0, 1])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    spec_line = (
        f"- Sport: **{args.sport}**, era: **{args.era}**, "
        f"grade: **{args.grade_tier}**, bucket: **{args.bucket}**"
    )
    window_line = (
        f"- Overlap window: {merged['period_start'].min().date()} → "
        f"{merged['period_start'].max().date()} ({len(merged)} buckets)"
    )
    args.out.write_text(
        f"""# Repeat-sales index vs Card Ladder CL50

{spec_line}
{window_line}

## Correlations

| Metric                            | Value   |
|-----------------------------------|---------|
| Level correlation (de-meaned log) | {level_corr:.3f} |
| Period-return correlation         | {return_corr:.3f} |

## Index level — ours

```
{_ascii_plot(merged["ours"])}
```

## Index level — CL50

```
{_ascii_plot(merged["cl50"])}
```

> CL50 is a price-averaged basket with rotating constituents (selection bias).
> Our index is a cert-based repeat-sales regression on the same asset over time.
> Material divergence is expected and informative.
"""
    )
    print(f"wrote {args.out}  (level_corr={level_corr:.3f}, return_corr={return_corr:.3f})")


if __name__ == "__main__":
    main()
