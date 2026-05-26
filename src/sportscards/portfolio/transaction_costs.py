"""Transaction-cost model for sports-card trades.

Rates default to the research-doc snapshot (eBay 13.25% + $0.30 per sale,
Goldin/Heritage 20% buyer's premium, PSA Value Bulk $24.99 effective Feb 2026).
All rates are configurable via :class:`FeeSchedule` because fees shift.

Channel keys (``"ebay"``, ``"goldin"``, ...) are *fee-schedule labels*,
not ``tx_raw.source`` values. ``"ebay"`` covers all eBay activity — both
historical sold listings (``source='ebay'``) and Browse-API active
listings (``source='ebay_active'``) — because the fee structure is the
same for both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SellChannel = Literal["ebay", "goldin", "heritage", "fanatics_collect", "private"]
BuyChannel = Literal["ebay", "goldin", "heritage", "fanatics_collect", "private"]


def _default_grading_tiers() -> dict[str, float]:
    return {
        "value_bulk": 24.99,
        "value": 49.99,
        "regular": 99.99,
        "express": 149.99,
        "super_express": 249.99,
        "walk_through": 1000.00,
    }


@dataclass(frozen=True)
class FeeSchedule:
    ebay_pct: float = 0.1325
    ebay_flat: float = 0.30
    auction_buyer_premium_pct: float = 0.20
    auction_seller_commission_pct: float = 0.10
    private_pct: float = 0.0
    psa_regrade_pct_of_value: float = 0.05
    grading_tiers: dict[str, float] = field(default_factory=_default_grading_tiers)


DEFAULT_SCHEDULE = FeeSchedule()


def cost_to_grade(tier: str = "value_bulk", schedule: FeeSchedule = DEFAULT_SCHEDULE) -> float:
    try:
        return schedule.grading_tiers[tier]
    except KeyError as exc:
        raise ValueError(f"unknown grading tier: {tier!r}") from exc


def regrade_cost(declared_value: float, schedule: FeeSchedule = DEFAULT_SCHEDULE) -> float:
    """Approximate PSA cross-grade / regrade fee as % of declared value."""
    pct_cost = declared_value * schedule.psa_regrade_pct_of_value
    return max(cost_to_grade("regular", schedule), pct_cost)


def net_proceeds(
    gross_price: float,
    channel: SellChannel = "ebay",
    schedule: FeeSchedule = DEFAULT_SCHEDULE,
) -> float:
    """Amount the seller actually receives after channel fees."""
    if gross_price < 0:
        raise ValueError("gross_price must be non-negative")
    if channel == "ebay":
        return gross_price * (1 - schedule.ebay_pct) - schedule.ebay_flat
    if channel in ("goldin", "heritage", "fanatics_collect"):
        return gross_price * (1 - schedule.auction_seller_commission_pct)
    if channel == "private":
        return gross_price * (1 - schedule.private_pct)
    raise ValueError(f"unknown sell channel: {channel!r}")


def landed_cost(
    gross_price: float,
    channel: BuyChannel = "ebay",
    schedule: FeeSchedule = DEFAULT_SCHEDULE,
) -> float:
    """All-in cost to the buyer (hammer + buyer's premium where applicable)."""
    if gross_price < 0:
        raise ValueError("gross_price must be non-negative")
    if channel == "ebay":
        return gross_price
    if channel in ("goldin", "heritage", "fanatics_collect"):
        return gross_price * (1 + schedule.auction_buyer_premium_pct)
    if channel == "private":
        return gross_price
    raise ValueError(f"unknown buy channel: {channel!r}")


def round_trip_cost_pct(
    price_buy: float,
    price_sell: float,
    channel_buy: BuyChannel = "ebay",
    channel_sell: SellChannel = "ebay",
    schedule: FeeSchedule = DEFAULT_SCHEDULE,
) -> float:
    """Total friction (buy + sell) as a fraction of buy price.

    Reports >0 even when price_sell == price_buy because of fees.
    """
    if price_buy <= 0:
        raise ValueError("price_buy must be positive")
    landed = landed_cost(price_buy, channel_buy, schedule)
    proceeds = net_proceeds(price_sell, channel_sell, schedule)
    return (landed - proceeds) / price_buy
