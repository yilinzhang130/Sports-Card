from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from sportscards.db.models import Card, PlayerStardomScore


def get_stardom_premium_for_card(
    session: Session, *, card_id: int, as_of: datetime
) -> Decimal | None:
    """Return the latest stardom premium for the player on `card_id` whose
    fit_at <= as_of. None if no eligible score."""
    s = get_stardom_premium_batch(session, card_ids=[card_id], as_of=as_of)
    if card_id not in s.index:
        return None
    val = s.loc[card_id]
    return None if pd.isna(val) else Decimal(str(val))


def get_stardom_premium_batch(
    session: Session, *, card_ids: list[int], as_of: datetime
) -> pd.Series:
    """Vectorized version. Series of Decimal premiums indexed by card_id.
    Cards with no eligible score are omitted."""
    if not card_ids:
        return pd.Series(dtype=object, name="stardom_premium")
    stmt = (
        select(Card.card_id, PlayerStardomScore.premium, PlayerStardomScore.fit_at)
        .join(PlayerStardomScore, PlayerStardomScore.player_id == Card.player_id)
        .where(Card.card_id.in_(card_ids))
        .where(PlayerStardomScore.fit_at <= as_of)
    )
    rows = session.execute(stmt).all()
    if not rows:
        return pd.Series(dtype=object, name="stardom_premium")
    df = pd.DataFrame(rows, columns=["card_id", "premium", "fit_at"])
    df = df.sort_values("fit_at").groupby("card_id", as_index=True).tail(1)
    return df.set_index("card_id")["premium"].rename("stardom_premium")
