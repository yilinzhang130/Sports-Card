from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class ParsedTitle(BaseModel):
    """Normalized SKU + slab info extracted from an eBay-style title."""

    year: int | None = None
    manufacturer: str | None = None
    set_name: str | None = Field(default=None, alias="set")
    card_number: str | None = None
    parallel: str = "Base"
    player_name: str | None = None
    is_rookie: bool = False
    has_auto: bool = False
    has_patch: bool = False
    is_one_of_one: bool = False

    slab_grader: str | None = None
    slab_grade: Decimal | None = None
    cert_number: str | None = None

    confidence: Decimal = Decimal("0")
    method: str = "regex"
