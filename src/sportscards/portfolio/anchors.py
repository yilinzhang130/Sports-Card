"""PWCC-100-tier blue-chip anchor list (hardcoded).

Known limitation: this list is hand-curated until ``tx_clean`` accumulates
~24 months of sales history, at which point the anchor selection should be
driven by trailing dollar-volume and price stability in (player, set, grade)
buckets. The research doc (SCAA Substack section, lines 142–147 of
``compass_artifact...md``) anchors the 70% sleeve to this PWCC-100 cohort.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AnchorSpec:
    year: int
    manufacturer: str
    set_name: str
    parallel: str
    player_name: str
    grader: str = "PSA"
    grade: float = 9.0


ANCHORS: tuple[AnchorSpec, ...] = (
    AnchorSpec(1986, "Fleer", "Fleer", "Base", "Michael Jordan", "PSA", 9),
    AnchorSpec(1986, "Fleer", "Fleer Stickers", "Base", "Michael Jordan", "PSA", 9),
    AnchorSpec(1996, "Topps", "Topps Chrome", "Refractor", "Kobe Bryant", "PSA", 9),
    AnchorSpec(2003, "Topps", "Topps Chrome", "Refractor", "LeBron James", "PSA", 9),
    AnchorSpec(2003, "Upper Deck", "Exquisite Collection", "Base", "LeBron James", "PSA", 8),
    AnchorSpec(2009, "Topps", "Topps Chrome", "Refractor", "Stephen Curry", "PSA", 9),
    AnchorSpec(2013, "Panini", "Prizm", "Base", "Giannis Antetokounmpo", "PSA", 10),
    AnchorSpec(2018, "Panini", "Prizm", "Base", "Luka Doncic", "PSA", 10),
    AnchorSpec(2018, "Panini", "Prizm", "Silver", "Luka Doncic", "PSA", 10),
    AnchorSpec(2018, "Panini", "Prizm", "Base", "Trae Young", "PSA", 10),
)


def resolve_anchors(session: Any) -> list[tuple[AnchorSpec, int | None]]:
    """Resolve each anchor to a ``card_master.card_id`` (or ``None`` if missing).

    Warns about unresolved anchors so missing seed data doesn't silently
    shrink the anchor sleeve.
    """
    from sqlalchemy import select

    from sportscards.db.models import Card, Player

    out: list[tuple[AnchorSpec, int | None]] = []
    for spec in ANCHORS:
        row = session.execute(
            select(Card.card_id)
            .join(Player, Player.player_id == Card.player_id)
            .where(
                Card.year == spec.year,
                Card.manufacturer == spec.manufacturer,
                Card.set_name == spec.set_name,
                Card.parallel == spec.parallel,
                Player.name == spec.player_name,
            )
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            warnings.warn(
                f"anchor unresolved: {spec.year} {spec.set_name} {spec.parallel} "
                f"{spec.player_name} — add to card_master seed",
                stacklevel=2,
            )
        out.append((spec, row))
    return out
