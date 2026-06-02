"""Conservative card identity extraction.

The parser intentionally separates a card's identity from a search basket.
Searches like "Stephen Curry Prizm PSA 10" can surface Select, Mosaic, Optic,
and Prizm cards; this module builds a canonical key from the title fields
instead of trusting the query text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from sportscards.parse.regex_parser import SET_TO_MANUFACTURER

YEAR_RE = re.compile(r"\b((?:19|20)\d{2})(?:[-/](\d{2}))?\b")
GRADE_RE = re.compile(r"\b(PSA|BGS|SGC|CGC)\s*(10|9(?:\.5)?|8(?:\.5)?|[1-9](?:\.5)?)\b", re.I)
CARD_NUM_RE = re.compile(r"#\s*(?:NO\.)?\s*([A-Z0-9.-]+)", re.I)
PRINT_RUN_RE = re.compile(r"/(\d{1,4})\b")

MANUFACTURERS = (
    ("panini", "Panini"),
    ("topps", "Topps"),
    ("bowman", "Bowman"),
    ("upper deck", "Upper Deck"),
    ("fleer", "Fleer"),
)

SETS = (
    ("bowman chrome", "Bowman Chrome"),
    ("topps chrome", "Topps Chrome"),
    ("donruss optic", "Donruss Optic"),
    ("national treasures", "National Treasures"),
    ("select", "Select"),
    ("mosaic", "Mosaic"),
    ("obsidian", "Obsidian"),
    ("hoops premium stock", "Hoops Premium Stock"),
    ("hoops", "Hoops"),
    ("donruss", "Donruss"),
    ("chronicles", "Chronicles"),
    ("prizm black", "Prizm Black"),
    ("prizm", "Prizm"),
)

SUBSETS = (
    "Storm Chasers",
    "Prizmania",
    "Prizm Break",
    "Mindset",
    "Fearless",
    "Bank Shot",
    "Thunder Road",
    "Net Marvels",
    "My House",
    "Epic Performers",
    "Widescreen",
    "Select Numbers",
    "Rainmakers",
    "Variation",
)

PARALLEL_PHRASES = (
    "Blue and Silver Prizm",
    "Green White Purple Prizm",
    "Fast Break Prizm",
    "Fast Break",
    "Pink Swirl Prizm",
    "Blue Shimmer Prizm",
    "Silver Prizm",
    "Silver",
    "Gold Prizm",
    "Gold",
    "Black",
    "Red Cracked Ice Prizm",
    "Red Cracked Ice",
    "Red Ice",
    "Blue Disco Prizm",
    "Green Prizm",
    "Green",
    "Purple Die-Cut",
    "Holo Prizm",
    "Holo",
    "Mojo",
    "Mosaic Silver Prizm",
    "Mosaic",
    "Cosmic Prizm",
    "Fractal",
)


@dataclass(frozen=True)
class CardIdentity:
    canonical_key: str
    player_name: str | None
    manufacturer: str | None
    year: int | None
    set_name: str | None
    subset: str | None
    card_number: str | None
    parallel: str
    print_run: int | None
    is_rookie: bool
    has_auto: bool
    has_patch: bool
    slab_grader: str | None
    slab_grade: Decimal | None
    confidence: Decimal
    needs_review: bool
    evidence: dict[str, str | int | bool | None]


def parse_card_identity(title: str, *, search_query: str | None = None) -> CardIdentity:
    normalized = _normalize(title)
    player_name = _find_player_name(normalized, search_query)
    year = _find_year(normalized)
    set_name = _find_set(normalized)
    manufacturer = _find_manufacturer(normalized, set_name)
    subset = _find_subset(normalized)
    card_number = _find_card_number(normalized)
    parallel = _find_parallel(normalized, set_name)
    print_run = _find_print_run(normalized)
    slab_grader, slab_grade = _find_grade(normalized)
    lower = normalized.casefold()
    is_rookie = bool(re.search(r"\b(rookie|rc)\b", lower))
    has_auto = bool(re.search(r"\b(auto|autograph)\b", lower))
    has_patch = "patch" in lower or "rpa" in lower

    confidence = _confidence(
        player_name=player_name,
        manufacturer=manufacturer,
        year=year,
        set_name=set_name,
        card_number=card_number,
        slab_grader=slab_grader,
        slab_grade=slab_grade,
    )
    needs_review = confidence < Decimal("0.850")
    canonical_key = _canonical_key(
        player_name,
        manufacturer,
        year,
        set_name,
        subset,
        card_number,
        parallel,
        print_run,
        is_rookie,
        has_auto,
        has_patch,
    )
    return CardIdentity(
        canonical_key=canonical_key,
        player_name=player_name,
        manufacturer=manufacturer,
        year=year,
        set_name=set_name,
        subset=subset,
        card_number=card_number,
        parallel=parallel,
        print_run=print_run,
        is_rookie=is_rookie,
        has_auto=has_auto,
        has_patch=has_patch,
        slab_grader=slab_grader,
        slab_grade=slab_grade,
        confidence=confidence,
        needs_review=needs_review,
        evidence={
            "title": normalized,
            "search_query": search_query,
            "source": "identity_regex_v1",
        },
    )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("≈", " ")).strip()


def _find_player_name(title: str, search_query: str | None) -> str | None:
    candidates: list[str] = []
    if search_query:
        pieces = re.split(
            r"\b(?:Bowman Chrome|Topps Chrome|Prizm|Select|Mosaic|PSA|BGS|SGC)\b",
            search_query,
        )
        if pieces and pieces[0].strip():
            candidates.append(pieces[0].strip())
    known = (
        "Stephen Curry",
        "Giannis Antetokounmpo",
        "Nikola Jokic",
        "LeBron James",
        "Kevin Durant",
        "Kobe Bryant",
        "Michael Jordan",
        "Luka Doncic",
        "Victor Wembanyama",
        "Anthony Edwards",
        "Shai Gilgeous-Alexander",
        "Cooper Flagg",
    )
    candidates.extend(name for name in known if name.casefold() in title.casefold())
    return candidates[0] if candidates else None


def _find_year(title: str) -> int | None:
    match = YEAR_RE.search(title)
    return int(match.group(1)) if match else None


def _find_manufacturer(title: str, set_name: str | None) -> str | None:
    lower = title.casefold()
    for token, value in MANUFACTURERS:
        if token in lower:
            return value
    if set_name == "Bowman Chrome":
        return "Bowman"
    return SET_TO_MANUFACTURER.get(set_name or "")


def _find_set(title: str) -> str | None:
    lower = title.casefold()
    for token, value in SETS:
        if token in lower:
            return value
    return None


def _find_subset(title: str) -> str | None:
    lower = title.casefold()
    for subset in SUBSETS:
        if subset.casefold() in lower:
            return subset
    return None


def _find_card_number(title: str) -> str | None:
    match = CARD_NUM_RE.search(title)
    if not match:
        return None
    value = match.group(1).strip(".").upper()
    if value == "NO":
        return None
    return value


def _find_parallel(title: str, set_name: str | None) -> str:
    lower = title.casefold()
    if "prizm blue and silver" in lower or "blue and silver prizm" in lower:
        return "Blue and Silver Prizm"
    found: list[str] = []
    for phrase in PARALLEL_PHRASES:
        if _contains_phrase(lower, phrase):
            found.append(phrase)
    if (
        "prizm" in lower
        and set_name not in {"Prizm", "Prizm Black"}
        and not any("Prizm" in item for item in found)
    ):
        found.append("Prizm")
    if not found:
        return "Base"
    # Keep compound phrases before shorter overlaps, then dedupe while preserving order.
    ordered: list[str] = []
    for item in found:
        if not any(item.casefold() in existing.casefold() for existing in ordered):
            ordered.append(item)
    return " / ".join(ordered)


def _contains_phrase(lower_title: str, phrase: str) -> bool:
    pattern = r"(?<![a-z0-9])" + re.escape(phrase.casefold()) + r"(?![a-z0-9])"
    return re.search(pattern, lower_title) is not None


def _find_print_run(title: str) -> int | None:
    match = PRINT_RUN_RE.search(title)
    return int(match.group(1)) if match else None


def _find_grade(title: str) -> tuple[str | None, Decimal | None]:
    match = GRADE_RE.search(title)
    if not match:
        return None, None
    return match.group(1).upper(), Decimal(match.group(2))


def _confidence(**fields: object) -> Decimal:
    score = Decimal("0")
    for key, points in {
        "player_name": "0.20",
        "manufacturer": "0.12",
        "year": "0.18",
        "set_name": "0.20",
        "card_number": "0.15",
        "slab_grader": "0.07",
        "slab_grade": "0.08",
    }.items():
        if fields[key]:
            score += Decimal(points)
    return min(score, Decimal("1.000"))


def _canonical_key(
    player_name: str | None,
    manufacturer: str | None,
    year: int | None,
    set_name: str | None,
    subset: str | None,
    card_number: str | None,
    parallel: str,
    print_run: int | None,
    is_rookie: bool,
    has_auto: bool,
    has_patch: bool,
) -> str:
    pieces = [
        player_name,
        manufacturer,
        str(year) if year else None,
        set_name,
        subset,
        f"#{card_number}" if card_number else None,
        parallel or "Base",
        f"/{print_run}" if print_run else None,
        "rookie" if is_rookie else None,
        "auto" if has_auto else None,
        "patch" if has_patch else None,
    ]
    return "|".join(_slug(piece) for piece in pieces if piece)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
