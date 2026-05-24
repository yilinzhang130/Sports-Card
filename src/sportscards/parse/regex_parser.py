"""High-confidence regex-based title parser for NBA basketball cards.

Targets the dominant title pattern on eBay sold listings, e.g.:
    "2018-19 Panini Prizm Luka Doncic #280 Silver PSA 10 Rookie RC"
    "2003-04 Topps Chrome Refractor LeBron James #111 PSA 9"
    "2019-20 Panini National Treasures Zion Williamson RPA /99 BGS 9.5"

This intentionally only handles the well-formed majority. Low-confidence
returns fall through to the LLM parser.
"""
from __future__ import annotations

import re
from decimal import Decimal

from sportscards.parse.schema import ParsedTitle

YEAR_RE = re.compile(r"\b((?:19|20)\d{2})(?:[-/](\d{2}))?\b")
CARD_NUM_RE = re.compile(r"#\s*([A-Z0-9-]+)")
CERT_RE = re.compile(r"\b(?:cert(?:ification)?|cert\s*#)\s*([0-9]{6,})\b", re.I)
GRADE_RE = re.compile(
    r"\b(PSA|BGS|SGC|CGC)\s*(10|9(?:\.5)?|8(?:\.5)?|[1-9](?:\.5)?)\b",
    re.I,
)
PRINT_RUN_RE = re.compile(r"/(\d{1,4})\b")

MANUFACTURERS = {
    "panini": "Panini",
    "topps": "Topps",
    "upper deck": "Upper Deck",
    "fleer": "Fleer",
}

# Order matters: longer/more-specific phrases first. "select" before "prizm"
# because "Panini Select ... Silver Prizm RC" uses "Prizm" as a parallel hint,
# not the set name.
SETS = [
    ("national treasures", "National Treasures"),
    ("flawless", "Flawless"),
    ("immaculate", "Immaculate"),
    ("topps chrome", "Topps Chrome"),
    ("donruss optic", "Donruss Optic"),
    ("optic", "Donruss Optic"),
    ("select", "Select"),
    ("prizm", "Prizm"),
    ("mosaic", "Mosaic"),
    ("contenders", "Contenders"),
    ("hoops", "Hoops"),
    ("donruss", "Donruss"),
]

# Sets that imply manufacturer when title doesn't say it explicitly.
SET_TO_MANUFACTURER: dict[str, str] = {
    "Prizm": "Panini",
    "Select": "Panini",
    "Mosaic": "Panini",
    "National Treasures": "Panini",
    "Flawless": "Panini",
    "Immaculate": "Panini",
    "Donruss Optic": "Panini",
    "Donruss": "Panini",
    "Contenders": "Panini",
    "Hoops": "Panini",
    "Topps Chrome": "Topps",
}

# Parallel synonyms. Match longest first.
PARALLELS = [
    ("black 1/1", "Black 1/1"),
    ("gold /10", "Gold /10"),
    ("gold vinyl", "Gold Vinyl"),
    ("red ice", "Red Ice"),
    ("silver", "Silver"),
    ("blue ice", "Blue Ice"),
    ("blue wave", "Blue Wave"),
    ("blue", "Blue"),
    ("red wave", "Red Wave"),
    ("red", "Red"),
    ("green", "Green"),
    ("orange", "Orange"),
    ("purple", "Purple"),
    ("pink", "Pink"),
    ("refractor", "Refractor"),
    ("x-fractor", "X-Fractor"),
    ("xfractor", "X-Fractor"),
    ("holo", "Holo"),
]


def _find_year(title: str) -> int | None:
    m = YEAR_RE.search(title)
    if not m:
        return None
    return int(m.group(1))


def _find_manufacturer(title: str) -> str | None:
    low = title.lower()
    for key, val in MANUFACTURERS.items():
        if key in low:
            return val
    return None


def _find_set(title: str) -> str | None:
    low = title.lower()
    for key, val in SETS:
        if key in low:
            return val
    return None


def _find_parallel(title: str) -> str:
    low = title.lower()
    for key, val in PARALLELS:
        if key in low:
            return val
    return "Base"


def parse_title(title: str) -> ParsedTitle:
    out = ParsedTitle(method="regex")

    out.year = _find_year(title)
    out.manufacturer = _find_manufacturer(title)
    out.set_name = _find_set(title)
    if out.manufacturer is None and out.set_name in SET_TO_MANUFACTURER:
        out.manufacturer = SET_TO_MANUFACTURER[out.set_name]
    out.parallel = _find_parallel(title)

    m = CARD_NUM_RE.search(title)
    if m:
        out.card_number = m.group(1)

    m = GRADE_RE.search(title)
    if m:
        out.slab_grader = m.group(1).upper()
        out.slab_grade = Decimal(m.group(2))

    m = CERT_RE.search(title)
    if m:
        out.cert_number = m.group(1)

    low = title.lower()
    out.is_rookie = bool(re.search(r"\b(rookie|rc)\b", low))
    out.has_auto = bool(re.search(r"\b(auto(?:graph)?)\b", low))
    out.has_patch = "patch" in low or "rpa" in low
    if "1/1" in low or "1 of 1" in low or "one of one" in low:
        out.is_one_of_one = True

    pr = PRINT_RUN_RE.search(title)
    # not stored on ParsedTitle directly; print-run lives in card_master,
    # so we only use it to disambiguate parallel naming, e.g. Gold /10 vs Gold /25.
    if pr and out.parallel == "Base":
        out.parallel = f"/{pr.group(1)}"

    # Confidence scoring: each strong field present buys you points.
    score = Decimal("0")
    if out.year:
        score += Decimal("0.25")
    if out.manufacturer:
        score += Decimal("0.15")
    if out.set_name:
        score += Decimal("0.25")
    if out.card_number:
        score += Decimal("0.15")
    if out.slab_grader and out.slab_grade is not None:
        score += Decimal("0.20")
    out.confidence = min(score, Decimal("1.000"))

    return out
