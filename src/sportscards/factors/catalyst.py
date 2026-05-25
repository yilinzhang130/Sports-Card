"""Catalyst event score with exponential time decay.

Aggregates `player_events` rows into a single scalar per player, as of any
target date. Each event contributes `weight * 0.5 ** (age_days / half_life)`
where weight depends on `event_type` and an optional severity bump (for
`injury_out`). Contributions sum additively across events.

Half-life defaults to 30 days (the "Collector's Edge" everyday-catalyst
window); awards (MVP, ROY, All-NBA, etc.) and deep-playoff outcomes use
longer half-lives to reflect their longer marketing tail.

The default 90-day lookback window introduces a small downward bias for the
90-day-half-life award events — at the cutoff exactly half the weight remains
— but events outside the window contribute <=~1% of base weight after six
half-lives for the default 30-day rate, so the bias is small. Bump
``lookback_days`` if you need precision on long-tail award decay.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from sportscards.db.models import PlayerEvent

EVENT_WEIGHTS: dict[str, Decimal] = {
    # Awards (positive, sparse, high-impact)
    "mvp":          Decimal("0.50"),
    "roy":          Decimal("0.40"),
    "dpoy":         Decimal("0.30"),
    "all_nba_1st":  Decimal("0.30"),
    "all_nba_2nd":  Decimal("0.20"),
    "all_nba_3rd":  Decimal("0.15"),
    "all_star":     Decimal("0.10"),
    "hof":          Decimal("0.40"),
    # Playoffs (compounds)
    "playoff_win":          Decimal("0.01"),
    "playoff_series_win":   Decimal("0.05"),
    "playoff_finals_win":   Decimal("0.20"),
    # Transactions
    "call_up":      Decimal("0.10"),
    "two_way":      Decimal("0.02"),
    "traded":       Decimal("0.00"),
    "signed":       Decimal("0.00"),
    # Injuries (severity-aware for injury_out — see compute_catalyst_score)
    "injury_out":       Decimal("-0.15"),
    "injury_dtd":       Decimal("-0.03"),
    "injury_return":    Decimal("0.05"),
}

DEFAULT_HALF_LIFE_DAYS: int = 30
HALF_LIFE_OVERRIDES: dict[str, int] = {
    "mvp": 90, "roy": 90, "dpoy": 90, "hof": 90,
    "all_nba_1st": 90, "all_nba_2nd": 90, "all_nba_3rd": 90, "all_star": 90,
    "playoff_finals_win": 60, "playoff_series_win": 45,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_datetime_utc(as_of: date | datetime) -> datetime:
    """Normalize ``date`` to end-of-day UTC; pass datetimes through (assume UTC if naive)."""
    if isinstance(as_of, datetime):
        if as_of.tzinfo is None:
            return as_of.replace(tzinfo=UTC)
        return as_of
    return datetime.combine(as_of, time.max, tzinfo=UTC)


def _injury_out_weight(payload: dict[str, Any] | None, base: Decimal) -> Decimal:
    """Apply severity bands for the ``injury_out`` event type.

    - ``season_ending: true`` → -0.30
    - ``expected_weeks_out >= 4`` → base (-0.15)
    - ``expected_weeks_out < 4`` → -0.05
    - otherwise → base
    """
    if not payload:
        return base
    if payload.get("season_ending") is True:
        return Decimal("-0.30")
    weeks = payload.get("expected_weeks_out")
    if weeks is not None:
        try:
            w = float(weeks)
        except (TypeError, ValueError):
            return base
        if w >= 4:
            return base
        return Decimal("-0.05")
    return base


def _contribution(
    event_type: str,
    payload: dict[str, Any] | None,
    age_days: float,
    weights: dict[str, Decimal],
    half_lives: dict[str, int],
) -> Decimal:
    base = weights.get(event_type)
    if base is None:
        return Decimal("0")
    weight = _injury_out_weight(payload, base) if event_type == "injury_out" else base
    if weight == 0:
        return Decimal("0")
    hl = half_lives.get(event_type, DEFAULT_HALF_LIFE_DAYS)
    decay = 0.5 ** (age_days / hl)
    value = float(weight) * decay
    return Decimal(str(round(value, 6)))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_catalyst_score(
    session: Session,
    player_id: int,
    as_of: date | datetime,
    *,
    lookback_days: int = 90,
    weights: dict[str, Decimal] | None = None,
    half_life_overrides: dict[str, int] | None = None,
) -> Decimal:
    """Aggregate recent player_events into a single decayed score.

    Sum over events with event_date <= as_of and >= as_of - lookback_days:
        contribution = weight(event_type) * 0.5 ** (age_days / half_life(event_type))

    Severity-aware ``injury_out``:
    - ``event_payload.season_ending = true`` → contribution = -0.30
    - ``expected_weeks_out >= 4`` → base (-0.15)
    - ``expected_weeks_out < 4`` → -0.05

    Returns a Decimal rounded to 6 decimal places. Returns ``Decimal("0")`` if
    no relevant events. Never reads events with ``event_date > as_of``
    (as-of safe).
    """
    bulk = compute_catalyst_scores_bulk(
        session,
        [player_id],
        as_of,
        lookback_days=lookback_days,
        weights=weights,
        half_life_overrides=half_life_overrides,
    )
    return bulk.get(player_id, Decimal("0"))


def compute_catalyst_scores_bulk(
    session: Session,
    player_ids: list[int],
    as_of: date | datetime,
    *,
    lookback_days: int = 90,
    weights: dict[str, Decimal] | None = None,
    half_life_overrides: dict[str, int] | None = None,
) -> dict[int, Decimal]:
    """Same as :func:`compute_catalyst_score` but for many players in one query.

    Players with zero relevant events are present in the result mapped to
    ``Decimal("0")``.
    """
    if not player_ids:
        return {}

    w = {**EVENT_WEIGHTS, **(weights or {})}
    hl = {**HALF_LIFE_OVERRIDES, **(half_life_overrides or {})}

    as_of_dt = _as_datetime_utc(as_of)
    lower = as_of_dt - timedelta(days=lookback_days)

    stmt = select(
        PlayerEvent.player_id,
        PlayerEvent.event_type,
        PlayerEvent.event_date,
        PlayerEvent.event_payload,
    ).where(
        and_(
            PlayerEvent.player_id.in_(player_ids),
            PlayerEvent.event_date <= as_of_dt,
            PlayerEvent.event_date >= lower,
        )
    )

    totals: dict[int, Decimal] = {pid: Decimal("0") for pid in player_ids}
    for pid, ev_type, ev_date, payload in session.execute(stmt).all():
        if ev_date.tzinfo is None:
            ev_date = ev_date.replace(tzinfo=UTC)
        age_days = (as_of_dt - ev_date).total_seconds() / 86400.0
        if age_days < 0:
            # Defensive: filter above should preclude this.
            continue
        totals[pid] += _contribution(ev_type, payload, age_days, w, hl)

    # Round each total to 6 decimals for storage stability.
    return {pid: Decimal(str(round(float(v), 6))) for pid, v in totals.items()}


def catalyst_score_30d_change(
    session: Session,
    player_id: int,
    as_of: date | datetime,
    **kwargs: Any,
) -> Decimal:
    """``score(as_of) - score(as_of - 30 days)``.

    Convenience for hedonic feature engineering — captures the recent change
    in catalyst momentum independent of the absolute level.
    """
    as_of_dt = _as_datetime_utc(as_of)
    prev = as_of_dt - timedelta(days=30)
    now_score = compute_catalyst_score(session, player_id, as_of_dt, **kwargs)
    prev_score = compute_catalyst_score(session, player_id, prev, **kwargs)
    diff = float(now_score) - float(prev_score)
    return Decimal(str(round(diff, 6)))
